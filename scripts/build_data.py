#!/usr/bin/env python3
"""SR 생활비지수 대시보드 데이터 빌드.
소스: FRED(미국 4종) + KOSIS(한국 CPI 2종) + ECOS(한국 M2) + data/manual/KB엑셀.
모든 소스가 성공하고 검증 앵커를 통과할 때만 docs/data.js 갱신(실패 시 기존 유지, 비정상 종료). 산식은 methodology.md 고정.

KOSIS 통계표 코드·ECOS M2 코드는 이 환경에서 미리 확정할 수 없어, 실행 시 '자동 탐색 + 검증'으로 정한다:
  - 후보(검색·메타 API)를 모아 기존 docs/data.js(시드)의 값과 겹치는 구간에서 대조,
    허용오차 안에서 명확히 하나만 맞을 때만 채택. 모호하거나 0개면 추정하지 않고 중단(기존 data.js 유지).
  - 채택한 코드는 로그에 남긴다(다음 실행에서 고정값으로 박아 넣을 수 있게).
KB 엑셀은 월간시계열(날짜가 열) 또는 주간시계열(날짜가 행, 주간→월평균)을 자동 인식한다.
KB 엑셀이 시작되기 전(예: 2008-04 이전)이나 신규 계열이 시작되기 전 구간은 기존 data.js 값을 이어 쓴다.
"""
import os, re, sys, json, datetime, urllib.request, urllib.parse
import pandas as pd, numpy as np

FRED_KEY = os.environ.get("FRED_API_KEY", "")
KOSIS_KEY = os.environ.get("KOSIS_API_KEY", "")
ECOS_KEY = os.environ.get("ECOS_API_KEY", "")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_JS = os.path.join(ROOT, "docs", "data.js")
UA = {"User-Agent": "Mozilla/5.0 sr-dashboard-portal-bot/1.0"}


def log(msg):
    print(msg, flush=True)


def mask(s):
    for k in (FRED_KEY, KOSIS_KEY, ECOS_KEY):
        if k:
            s = s.replace(k, "***")
    return s


def http_json(url, timeout=60):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read().decode("utf-8", "replace")
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        # KOSIS는 따옴표 없는 키(JSON 변형)를 돌려줄 때가 있음
        fixed = re.sub(r'([\{,]\s*)([A-Za-z_][\w]*)\s*:', r'\1"\2":', raw)
        return json.loads(fixed)


# ───────────────────────── 공통 시계열 도구 ─────────────────────────

def yoy(s):
    s = s.dropna()
    s = s.asfreq("MS")
    return (s / s.shift(12) - 1) * 100


def pack(s, start):
    s = s.dropna()
    s = s[s.index >= start]
    return {d.strftime("%Y-%m"): round(float(v), 2) for d, v in s.items()}


def to_series(d):
    s = pd.Series({pd.Timestamp(k + "-01"): v for k, v in d.items()}) if d else pd.Series(dtype=float)
    return s.sort_index()


def load_existing():
    """기존 docs/data.js → dict (없거나 깨졌으면 {})."""
    try:
        t = open(DATA_JS, encoding="utf-8").read()
        return json.loads(t[t.index("{"):t.rindex("}") + 1])
    except Exception:
        return {}


def merge_history(new, old):
    """new가 시작되기 전 구간은 old 값을 이어 붙임(둘 다 월 키 'YYYY-MM')."""
    if not old:
        return dict(new)
    if not new:
        return dict(old)
    first = min(new)
    out = {k: v for k, v in old.items() if k < first}
    out.update(new)
    return dict(sorted(out.items()))


def mad(a, b, since="2005-01-01"):
    """두 yoy 시리즈의 겹치는 구간 평균절대차. (겹침 부족하면 None)"""
    j = pd.concat([a, b], axis=1, join="inner").dropna()
    j = j[j.index >= since]
    if len(j) < 24:
        return None
    return float((j.iloc[:, 0] - j.iloc[:, 1]).abs().mean())


def choose_unique(cands, tol, margin=0.03, what=""):
    """cands: [(mad, label, series)] → 허용오차 안의 최선 하나(차순위와 margin 이상 차이)만 반환, 아니면 예외."""
    good = sorted([c for c in cands if c[0] is not None and c[0] <= tol], key=lambda x: x[0])
    # 같은 데이터가 여러 표에 중복 등재된 경우(yoy가 사실상 동일)는 하나로 본다
    uniq = []
    for c in good:
        def _dup(u):
            m = mad(yoy(c[2]), yoy(u[2]), since="1990-01-01")
            return m is not None and m < 0.005
        if not any(_dup(u) for u in uniq):
            uniq.append(c)
    good = uniq
    log(f"[{what}] 검증 통과 후보 {len(good)}개 (전체 {len(cands)}개, 허용 {tol})")
    for c in sorted([c for c in cands if c[0] is not None], key=lambda x: x[0])[:5]:
        log(f"    mad={c[0]:.3f}  {c[1]}")
    nones = [c for c in cands if c[0] is None]
    if nones:
        log(f"    (대조 불가 {len(nones)}개 — 겹치는 구간<24개월) 예:")
        for c in nones[:6]:
            sx = c[2].dropna()
            rng = f"{sx.index[0]:%Y-%m}~{sx.index[-1]:%Y-%m} n={len(sx)}" if len(sx) else "빈 시리즈"
            log(f"      {c[1]} [{rng}]")
    if not good:
        raise RuntimeError(f"{what}: 시드와 맞는 후보가 없음 — 코드 탐색 실패(위 후보 로그 참고)")
    if len(good) > 1 and good[1][0] - good[0][0] < margin:
        raise RuntimeError(f"{what}: 후보가 모호함({good[0][1]} vs {good[1][1]}) — 추정하지 않고 중단")
    log(f"[{what}] 채택: {good[0][1]} (mad={good[0][0]:.3f})")
    return good[0]


# ───────────────────────── FRED ─────────────────────────

def fred(series_id):
    url = (f"https://api.stlouisfed.org/fred/series/observations?series_id={series_id}"
           f"&api_key={FRED_KEY}&file_type=json&observation_start=1985-01-01")
    obs = http_json(url)["observations"]
    s = pd.Series({pd.Timestamp(o["date"]): float(o["value"]) for o in obs if o["value"] != "."})
    return s.sort_index()


# ───────────────────────── KB 엑셀 ─────────────────────────

_MONTH_RE = re.compile(r"^\s*((?:19|20)\d{2})\s*[.\-/년\s]\s*(\d{1,2})")


def parse_month(x):
    if x is None:
        return None
    if isinstance(x, (datetime.datetime, datetime.date, pd.Timestamp)):
        return pd.Timestamp(x.year, x.month, 1)
    if isinstance(x, str):
        m = _MONTH_RE.match(x)
        if m and 1 <= int(m.group(2)) <= 12:
            return pd.Timestamp(int(m.group(1)), int(m.group(2)), 1)
    return None


def _num(v):
    if isinstance(v, bool) or v is None:
        return np.nan
    if isinstance(v, (int, float)):
        return float(v)
    try:
        return float(str(v).replace(",", "").strip())
    except ValueError:
        return np.nan


def kb_monthly_layout(rows):
    """월간시계열: 어떤 행의 열들이 날짜(≥24개), '전국' 행이 따로 있음."""
    hdr_i = hdr_map = None
    for i, r in enumerate(rows[:8]):
        mp = {j: parse_month(c) for j, c in enumerate(r) if j > 0 and parse_month(c)}
        if len(mp) >= 24:
            hdr_i, hdr_map = i, mp
            break
    if hdr_map is None:
        return None
    for r in rows[hdr_i + 1:]:
        if r and r[0] is not None and str(r[0]).strip() == "전국":
            s = {}
            for j, d in hdr_map.items():
                if j < len(r):
                    v = _num(r[j])
                    if not np.isnan(v):
                        s[d] = v
            if len(s) >= 24:
                return pd.Series(s).sort_index(), "월간(날짜=열)"
    return None


def kb_weekly_layout(rows):
    """주간시계열: 헤더 행에 '전국' 열, 날짜가 첫 열(행). 월 평균으로 환산."""
    col = hdr_i = None
    for i, r in enumerate(rows[:8]):
        for j, c in enumerate(r[:6]):
            if c is not None and str(c).strip() == "전국":
                col, hdr_i = j, i
                break
        if col is not None:
            break
    if col is None:
        return None
    vals = {}
    for r in rows[hdr_i + 1:]:
        if not r or r[0] is None or len(r) <= col:
            continue
        d = r[0]
        if not isinstance(d, (datetime.datetime, datetime.date, pd.Timestamp)):
            try:
                d = pd.Timestamp(str(d))
            except Exception:
                continue
        v = _num(r[col])
        if not np.isnan(v):
            vals[pd.Timestamp(d)] = v
    if len(vals) < 100:
        return None
    w = pd.Series(vals).sort_index()
    m = w.groupby(w.index.to_period("M")).mean()
    m.index = m.index.to_timestamp()
    return m, "주간(월평균 환산)"


def read_kb():
    from openpyxl import load_workbook
    mdir = os.path.join(ROOT, "data", "manual")
    xs = sorted([f for f in os.listdir(mdir) if f.lower().endswith(".xlsx") and not f.startswith("~$")], reverse=True)
    if not xs:
        raise RuntimeError("data/manual/에 KB 아파트 매매가격지수 엑셀이 없습니다")
    last_err = []
    for fn in xs:
        path = os.path.join(mdir, fn)
        wb = load_workbook(path, read_only=True, data_only=True)
        titles = [ws.title for ws in wb.worksheets]
        # '매매지수'가 들어간 시트(증감 제외)를 먼저, 그다음 나머지
        pri = [t for t in titles if "매매" in t and "지수" in t and "증감" not in t]
        order = pri + [t for t in titles if t not in pri and "증감" not in t and "전세" not in t][:4]
        for t in order:
            rows = [list(r) for r in wb[t].iter_rows(values_only=True)]
            for fnc in (kb_monthly_layout, kb_weekly_layout):
                got = fnc(rows)
                if got:
                    s, kind = got
                    s.index = s.index.to_period("M").to_timestamp()
                    s = s[~s.index.duplicated(keep="last")].sort_index()
                    log(f"KB 엑셀: {fn} / 시트 '{t}' / {kind} / {s.index[0]:%Y-%m}~{s.index[-1]:%Y-%m} ({len(s)}개월)")
                    wb.close()
                    return s
            last_err.append(f"{fn}:{t}")
        wb.close()
    raise RuntimeError(f"KB 엑셀에서 '전국' 매매지수 시계열을 찾지 못함 (시도: {last_err[:10]})")


# ───────────────────────── ECOS: M2 탐색 ─────────────────────────

def ecos_rows(stat_code, item_code, cycle="M", start="198601"):
    end = datetime.date.today().strftime("%Y%m")
    url = (f"https://ecos.bok.or.kr/api/StatisticSearch/{ECOS_KEY}/json/kr/1/3000/"
           f"{stat_code}/{cycle}/{start}/{end}/{item_code}")
    d = http_json(url, timeout=25)
    if "StatisticSearch" not in d:
        raise RuntimeError(f"ECOS 응답 이상: {mask(json.dumps(d, ensure_ascii=False))[:200]}")
    return d["StatisticSearch"]["row"]


def ecos_series(stat_code, item_code):
    rows = ecos_rows(stat_code, item_code)
    s = pd.Series({pd.Timestamp(x["TIME"][:4] + "-" + x["TIME"][4:6] + "-01"): float(x["DATA_VALUE"])
                   for x in rows if x.get("DATA_VALUE") not in (None, "")})
    return s.sort_index()


def ecos_m2_stat_codes():
    """ECOS 통계표 목록에서 이름에 M2/통화량/유동성이 들어간 표 + 알려진 범위(101Y/161Y 001~010)."""
    codes = {}
    try:
        d = http_json(f"https://ecos.bok.or.kr/api/StatisticTableList/{ECOS_KEY}/json/kr/1/1000/", timeout=40)
        rows = d["StatisticTableList"]["row"]
        log(f"  ECOS 통계표 목록 {len(rows)}개 조회")
        for r in rows:
            nm = r.get("STAT_NAME", "")
            if r.get("STAT_CODE") and (("M2" in nm.upper()) or ("통화량" in nm) or ("유동성" in nm)):
                codes[r["STAT_CODE"]] = nm
    except Exception as e:
        log(f"  ECOS 통계표 목록 조회 실패({type(e).__name__}: {mask(str(e))[:120]})")
    for pre in ("101Y", "161Y"):
        for n in range(1, 11):
            codes.setdefault(f"{pre}{n:03d}", "(고정 범위)")
    log(f"  ECOS 탐색 대상 통계표 {len(codes)}개: " + "; ".join(f"{k}={v[:30]}" for k, v in list(codes.items())[:25]))
    return codes


def find_m2_ecos(seed_m2_yoy):
    """ECOS 통화량 관련 표의 'M2' 월별 계열 중 최근까지 갱신되는 것만 모아 시드 M2 증감률과 대조."""
    cands, fetched = [], 0
    for stat, sname in ecos_m2_stat_codes().items():
        try:
            d = http_json(f"https://ecos.bok.or.kr/api/StatisticItemList/{ECOS_KEY}/json/kr/1/300/{stat}", timeout=25)
            items = d["StatisticItemList"]["row"]
        except Exception as e:
            log(f"  ECOS {stat}: 항목 조회 실패({type(e).__name__})")
            continue
        m2_items = [it for it in items if "M2" in (it.get("ITEM_NAME", "") + " " + it.get("STAT_NAME", "")).upper() and it.get("CYCLE") == "M"]
        log(f"  ECOS {stat} '{(items[0].get('STAT_NAME','') if items else '')[:40]}': 항목 {len(items)}개 / M2 월별 {len(m2_items)}개 / 종료시점 예 {[str(i.get('END_TIME')) for i in m2_items[:3]]}")
        for it in items:
            nm = it.get("ITEM_NAME", "")
            if "M2" not in (nm + " " + it.get("STAT_NAME", "")).upper() or it.get("CYCLE") != "M":
                continue
            end_t = str(it.get("END_TIME", ""))
            if end_t and end_t[:4].isdigit() and int(end_t[:4]) < 2025:
                continue   # 종료된 구 계열 제외
            if fetched >= 150:
                break
            fetched += 1
            try:
                s = ecos_series(stat, it["ITEM_CODE"])
            except Exception as e:
                log(f"  ECOS {stat}/{it['ITEM_CODE']} 조회 실패({type(e).__name__})")
                continue
            m = mad(yoy(s), seed_m2_yoy)
            cands.append((m, f"ECOS {stat}/{it['ITEM_CODE']} '{it.get('STAT_NAME','')}|{nm}'", s))
    log(f"  ECOS M2 후보 시리즈 {len(cands)}개 조회")
    return cands


# ───────────────────────── KOSIS: CPI 표 탐색 ─────────────────────────

KB = "https://kosis.kr/openapi/"


def kosis_search(name, org="101", n=30):
    url = (f"{KB}statisticsSearch.do?method=getList&apiKey={KOSIS_KEY}&format=json&jsonVD=Y"
           f"&searchNm={urllib.parse.quote(name)}&orgId={org}&sort=RANK&startCount=1&resultCount={n}")
    d = http_json(url)
    if isinstance(d, dict):
        raise RuntimeError(f"KOSIS 검색 오류: {mask(json.dumps(d, ensure_ascii=False))[:200]}")
    return d


def kosis_meta(org, tbl, typ):
    url = (f"{KB}statisticsData.do?method=getMeta&apiKey={KOSIS_KEY}&type={typ}"
           f"&orgId={org}&tblId={tbl}&format=json&jsonVD=Y")
    d = http_json(url)
    if isinstance(d, dict):
        raise RuntimeError(f"KOSIS 메타 오류: {mask(json.dumps(d, ensure_ascii=False))[:200]}")
    return d


def kosis_table_groups(org, tbl):
    """통계표에서 '총지수'(없으면 실패)로 월별 자료를 받아 그룹별(항목·보조분류) 시리즈로 분해."""
    meta = None
    for typ in ("ITM", "OBJ"):   # KOSIS 메타: 분류/항목은 type=ITM (항목 행의 OBJ_ID는 'ITEM')
        try:
            meta = kosis_meta(org, tbl, typ)
            break
        except Exception as e:
            last = e
    if meta is None:
        raise last
    obj = [o for o in meta if str(o.get("OBJ_ID", "")).upper() != "ITEM"]
    if not obj:
        raise RuntimeError("분류 메타가 비어 있음")

    def _sn(o):
        try:
            return int(o.get("OBJ_ID_SN"))
        except (TypeError, ValueError):
            return 0
    # 분류(차원) 순서: OBJ_ID_SN 우선, 없으면 등장 순서 → objL1, objL2, ...
    dims = []
    for o in sorted(obj, key=_sn):
        if o["OBJ_ID"] not in dims:
            dims.append(o["OBJ_ID"])
    PREF = ("총지수", "전국", "소비자물가지수", "전국총지수", "자가주거비포함총지수")
    params, picked = "", []
    for k, did in enumerate(dims, start=1):
        code = None
        for o in obj:
            if o["OBJ_ID"] == did and o.get("ITM_NM", "").replace(" ", "") in PREF:
                code = o["ITM_ID"]
                picked.append(o.get("ITM_NM"))
                break
        if code is None:
            for o in obj:
                if o["OBJ_ID"] == did and "총지수" in o.get("ITM_NM", ""):
                    code = o["ITM_ID"]
                    picked.append(o.get("ITM_NM"))
                    break
        params += f"&objL{k}={code if code else 'ALL'}"
        if not code:
            picked.append("ALL")
    if all(x == "ALL" for x in picked):
        # 고를 기준값이 없으면 ALL로 받되, 분류가 너무 많은 표(해외 국가별 등)는 제외
        n_items = sum(1 for o in obj if o["OBJ_ID"] == dims[0])
        if n_items > 12 or len(dims) > 1:
            names = [o.get("ITM_NM") for o in obj if o["OBJ_ID"] == dims[0]][:6]
            raise RuntimeError(f"총지수/전국 분류값을 찾지 못함(1분류 {n_items}개, 예: {names})")
    code1 = "/".join(str(x) for x in picked)
    end = datetime.date.today().strftime("%Y%m")
    url = (f"{KB}Param/statisticsParameterData.do?method=getList&apiKey={KOSIS_KEY}&itmId=ALL"
           f"&orgId={org}&tblId={tbl}{params}&format=json&jsonVD=Y&prdSe=M&startPrdDe=198501&endPrdDe={end}")
    rows = http_json(url)
    if isinstance(rows, dict):
        raise RuntimeError(f"KOSIS 자료 오류: {mask(json.dumps(rows, ensure_ascii=False))[:200]}")
    groups = {}
    for r in rows:
        key = (r.get("ITM_NM", ""),) + tuple(r.get(f"C{k}_NM", "") for k in range(1, 6) if "ALL" in str(code1) or k > 1)
        v = _num(r.get("DT"))
        p = str(r.get("PRD_DE", ""))
        if len(p) < 6 or np.isnan(v):
            continue
        groups.setdefault(key, {})[pd.Timestamp(p[:4] + "-" + p[4:6] + "-01")] = v
    return {k: pd.Series(v).sort_index() for k, v in groups.items() if len(v) >= 120}, code1


def find_kosis(target_yoy, queries, must, mustnot, what):
    """검색어로 통계표 후보를 모으고, 각 표의 그룹별 시리즈 yoy를 target과 대조."""
    seen, cands = set(), []
    for q in queries:
        try:
            res = kosis_search(q)
        except Exception as e:
            log(f"  KOSIS 검색 '{q}' 실패: {mask(str(e))[:150]}")
            continue
        for t in res:
            nm = (t.get("TBL_NM") or "") + " " + (t.get("STAT_NAME") or "")
            key = (t.get("ORG_ID"), t.get("TBL_ID"))
            if key in seen or not t.get("TBL_ID"):
                continue
            if not all(m in nm for m in must) or any(x in nm for x in mustnot):
                continue
            seen.add(key)
            log(f"  [{what}] 표 후보: {key} {t.get('TBL_NM')} ({t.get('STAT_NAME')})")
            if len(seen) > 12:
                break
    for org, tbl in list(seen):
        try:
            groups, code1 = kosis_table_groups(org, tbl)
        except Exception as e:
            log(f"  [{what}] 표 {tbl} 자료 실패: {mask(str(e))[:150]}")
            continue
        for g, s in groups.items():
            cands.append((mad(yoy(s), target_yoy), f"KOSIS {org}/{tbl} objL1={code1} {g}", s))
    return cands


# ───────────────────────── 메인 ─────────────────────────

def main():
    for name, k in (("FRED_API_KEY", FRED_KEY), ("KOSIS_API_KEY", KOSIS_KEY), ("ECOS_API_KEY", ECOS_KEY)):
        log(f"{name} set: {'yes' if k else 'NO'}")
        if not k:
            raise RuntimeError(f"{name} 가 비어 있음 — Secrets 등록 필요")
    seed = load_existing()
    if not seed.get("kr"):
        raise RuntimeError("기존 docs/data.js 가 없어 코드 탐색용 대조 기준을 만들 수 없음")
    seed_kr, seed_us = seed["kr"], seed.get("us", {})

    # --- 미국 (FRED) ---
    pce, cs, cpius, m2us = map(fred, ["PCEPI", "CSUSHPISA", "CPIAUCSL", "M2SL"])
    us_sr = 0.7 * yoy(pce) + 0.3 * yoy(cs)

    # --- 한국 ---
    kb = read_kb()
    kb_y = yoy(kb)
    seed_sr = to_series(seed_kr.get("sr"))
    seed_cpi = to_series(seed_kr.get("cpi"))
    seed_m2 = to_series(seed_kr.get("m2"))

    # 일반 CPI: 시드 kr.cpi(전년동월비)와 대조
    cpi_c = find_kosis(seed_cpi, ["소비자물가지수", "소비자물가지수 품목성질별", "소비자물가지수 총지수"],
                       must=["소비자물가"], mustnot=["자가주거", "지역", "도시", "생활물가", "특수", "농축", "근원", "시도", "연쇄", "상승률", "환율", "연도별", "지출목적", "품목성질"], what="CPI")
    cpi_pick = choose_unique(cpi_c, tol=0.06, margin=0.01, what="일반 CPI")
    cpikr = cpi_pick[2]

    # 자가주거비포함 CPI: 시드 SR과 KB(주간 월평균)로 역산한 값과 대조 (KB 환산 차이를 감안해 허용오차 완화)
    implied_ooh = ((seed_sr - 0.3 * kb_y) / 0.7).dropna()
    ooh_c = find_kosis(implied_ooh, ["자가주거비 포함 소비자물가지수", "자가주거비포함 소비자물가지수", "자가주거비"],
                       must=["자가주거"], mustnot=[], what="OOH")
    ooh_pick = choose_unique(ooh_c, tol=0.45, margin=0.05, what="자가주거비포함 CPI")
    ooh = ooh_pick[2]

    # M2 평잔 원계열: 시드 kr.m2(전년동월비)와 대조
    m2_c = find_m2_ecos(seed_m2)
    m2_pick = choose_unique(m2_c, tol=0.15, margin=0.02, what="한국 M2")
    m2kr = m2_pick[2]

    kr_sr = 0.7 * yoy(ooh) + 0.3 * kb_y.reindex(yoy(ooh).index)

    new = {
        "kr": {"sr": pack(kr_sr, "1996-01-01"), "cpi": pack(yoy(cpikr), "1996-01-01"),
               "spread": pack(kr_sr - yoy(cpikr), "1996-01-01"), "m2": pack(yoy(m2kr), "1996-01-01")},
        "us": {"sr": pack(us_sr, "1988-01-01"), "cpi": pack(yoy(cpius), "1988-01-01"),
               "spread": pack(us_sr - yoy(cpius), "1988-01-01"), "m2": pack(yoy(m2us), "1988-01-01")},
    }
    data = {
        "meta": {"built": datetime.date.today().isoformat(), "source": "live",
                 "formula": "KR 0.7*OOH+0.3*KB / US 0.7*PCEPI+0.3*CS (yoy)"},
        "kr": {k: merge_history(new["kr"][k], seed_kr.get(k)) for k in new["kr"]},
        "us": {k: merge_history(new["us"][k], seed_us.get(k)) for k in new["us"]},
    }
    for blk, nm in (("kr", "한국"), ("us", "미국")):
        for k in data[blk]:
            fresh = len(new[blk][k])
            log(f"{nm}.{k}: 신규 {fresh}개월 + 이전 이력 {len(data[blk][k]) - fresh}개월 = {len(data[blk][k])}")

    # 검증 앵커 (methodology.md) — 어긋나면 배포 금지
    anchors = [("kr", "sr", "2021-12", 8.41), ("kr", "sr", "2023-07", -1.80), ("us", "sr", "2022-03", 11.06)]
    bad = []
    for blk, k, m, want in anchors:
        got = data[blk][k].get(m)
        ok = got is not None and abs(got - want) <= 0.30
        log(f"앵커 {blk}.{k} {m}: 기대 {want} / 실측 {got} → {'OK' if ok else 'FAIL'}")
        if not ok:
            bad.append((blk, k, m, want, got))
    sp = data["kr"]["spread"]
    log(f"참고: 한국 스프레드 2026-07={sp.get('2026-07')} 2026-08={sp.get('2026-08')} (≈0 부근 기대)")
    if bad:
        raise RuntimeError(f"검증 앵커 불일치 {bad} — data.js를 갱신하지 않습니다")

    with open(DATA_JS, "w", encoding="utf-8") as f:
        f.write("const DATA = " + json.dumps(data, ensure_ascii=False) + ";")
    log(f"wrote {DATA_JS}")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log(f"[중단] {mask(str(e))}")
        sys.exit(1)
