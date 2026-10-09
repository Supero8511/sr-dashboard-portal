#!/usr/bin/env python3
"""지표금리 현황(docs/rates.js) 자동 갱신 — '자금조달 여건' 스프레드.

- 미국: SOFR(75분위) − IORB (bp, 일별) — 둘 다 FRED 공식 시리즈.
- 한국: 콜금리(1일물) − 기준금리 (bp, 일별) — 둘 다 ECOS 공식 통계.
  ※ 원래 핸드오프 문서의 'KOFR−콜금리'는 KOFR에 공식 숫자 공개 API가 없어
    (ECOS StatisticWord 검색 결과 없음, 민간 산출기관 배포) 자동화 범위에서 제외하고,
    같은 취지(시장 자금조달 압력)를 ECOS로만 완전자동화할 수 있는 '콜금리−기준금리'
    스프레드로 대체했다. KOFR 자체가 필요해지면 별도로(수동) 추가 가능.
  각 스프레드에 20거래일 이동평균을 함께 제공(원 핸드오프 사양과 동일).

절대 규칙(다른 update_*.py와 동일): 소스 조회가 실패하거나 응답이 비어 있으면
해당 다리(미국 또는 한국)만 건너뛰고 기존 값을 유지한다 — 추정하지 않는다.
"""
import os, sys, json, datetime, urllib.request

FRED_KEY = os.environ.get("FRED_API_KEY", "")
ECOS_KEY = os.environ.get("ECOS_API_KEY", "")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "docs", "rates.js")

US_BACKFILL_START = "2018-04-03"   # SOFR 공식 집계 시작일
KR_BACKFILL_START = "2016-01-04"   # ECOS 콜금리·기준금리 일별 안정적으로 제공되는 구간

LOG = []
def log(msg):
    LOG.append(msg); print(msg)

def _http_json(url, timeout=30):
    req = urllib.request.Request(url, headers={"User-Agent": "sr-dashboard-portal-bot/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)

def fred_series(series_id, start):
    url = (f"https://api.stlouisfed.org/fred/series/observations?series_id={series_id}"
           f"&api_key={FRED_KEY}&file_type=json&observation_start={start}&sort_order=asc")
    obs = _http_json(url)["observations"]
    return {o["date"]: float(o["value"]) for o in obs if o["value"] != "."}

def ecos_series(stat_code, item_code, start, end):
    url = (f"https://ecos.bok.or.kr/api/StatisticSearch/{ECOS_KEY}/json/kr/1/5000/"
           f"{stat_code}/D/{start}/{end}/{item_code}")
    data = _http_json(url)
    rows = (data.get("StatisticSearch") or {}).get("row") or []
    out = {}
    for x in rows:
        t = x.get("TIME", "")
        if len(t) != 8:
            continue
        try:
            out[f"{t[0:4]}-{t[4:6]}-{t[6:8]}"] = float(x["DATA_VALUE"])
        except (KeyError, ValueError):
            continue
    return out

def rolling_ma(obs_sorted, n):
    """obs_sorted: [(date,val), ...] 오름차순. 거래일(=관측치) 기준 n개 이동평균."""
    out = []
    window = []
    for d, v in obs_sorted:
        window.append(v)
        if len(window) > n:
            window.pop(0)
        out.append((d, round(sum(window) / len(window), 1)))
    return out

def _extract_json_object(text):
    """'window.X = {...};' 꼴의 텍스트에서 중괄호 균형을 맞춰 JSON 객체 부분만 추출."""
    start = text.find("{")
    if start < 0:
        return None
    depth = 0
    in_str = False
    i = start
    while i < len(text):
        c = text[i]
        if in_str:
            if c == '"' and text[i - 1] != "\\":
                in_str = False
        elif c == '"':
            in_str = True
        elif c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return text[start:i + 1]
        i += 1
    return None

def load_existing():
    if not os.path.exists(OUT):
        return {}
    text = open(OUT, encoding="utf-8").read()
    frag = _extract_json_object(text)
    if not frag:
        return {}
    try:
        obj = json.loads(frag)
    except Exception as e:
        log(f"기존 rates.js 파싱 실패({e}) — 빈 상태로 취급")
        return {}
    return obj.get("series", {})

def merge(existing_obs, new_map):
    """existing_obs: [[date,val],...] / new_map: {date:val} → 병합해 날짜 오름차순 리스트로."""
    merged = {d: v for d, v in existing_obs}
    merged.update(new_map)
    return sorted(merged.items())

def build_leg(name, raw_a, raw_b, existing_series, backfill_start):
    """raw_a, raw_b: {date: value} (스프레드 = a − b, bp). 둘 다 성공해야 이 다리를 갱신."""
    dates = sorted(set(raw_a) & set(raw_b))
    if not dates:
        log(f"[{name}] 공통 날짜 없음 — 건너뛰고 기존 값 유지")
        return None
    spread = {d: round((raw_a[d] - raw_b[d]) * 100, 1) for d in dates}
    base_key = name + "_bp"
    ma_key = name + "_ma20_bp"
    existing_base = (existing_series.get(base_key) or {}).get("obs", [])
    merged_base = merge(existing_base, spread)
    ma = rolling_ma(merged_base, 20)
    return {base_key: {"obs": [[d, v] for d, v in merged_base]},
            ma_key: {"obs": [[d, v] for d, v in ma]}}

def main():
    existing = load_existing()
    today = datetime.date.today()
    end = (today + datetime.timedelta(days=1)).isoformat()
    out_series = dict(existing)  # 실패한 다리는 그대로 유지
    any_update = False

    # ── 미국: SOFR(75분위) − IORB ──
    us_start = US_BACKFILL_START if not existing.get("us_sofr75_iorb_bp") else \
        ((datetime.date.fromisoformat((existing["us_sofr75_iorb_bp"]["obs"] or [[US_BACKFILL_START]])[-1][0]) - datetime.timedelta(days=10)).isoformat())
    if not FRED_KEY:
        log("[us] FRED_API_KEY 없음 — 건너뛰고 기존 값 유지")
    else:
        try:
            sofr75 = fred_series("SOFR75", us_start)
            iorb = fred_series("IORB", us_start)
        except Exception as e:
            log(f"[us] FRED 조회 실패: {e} — 건너뛰고 기존 값 유지")
        else:
            leg = build_leg("us_sofr75_iorb", sofr75, iorb, existing, US_BACKFILL_START)
            if leg:
                out_series.update(leg); any_update = True
                log(f"[us] 갱신 — 최신 {leg['us_sofr75_iorb_bp']['obs'][-1]}")

    # ── 한국: 콜금리(1일물) − 기준금리 ──
    kr_start = KR_BACKFILL_START if not existing.get("kr_call_base_bp") else \
        ((datetime.date.fromisoformat((existing["kr_call_base_bp"]["obs"] or [[KR_BACKFILL_START]])[-1][0]) - datetime.timedelta(days=10)).isoformat())
    kr_start_c = kr_start.replace("-", "")
    end_c = end.replace("-", "")
    if not ECOS_KEY:
        log("[kr] ECOS_API_KEY 없음 — 건너뛰고 기존 값 유지")
    else:
        try:
            call = ecos_series("817Y002", "010101000", kr_start_c, end_c)
            base = ecos_series("722Y001", "0101000", kr_start_c, end_c)
        except Exception as e:
            log(f"[kr] ECOS 조회 실패: {e} — 건너뛰고 기존 값 유지")
        else:
            leg = build_leg("kr_call_base", call, base, existing, KR_BACKFILL_START)
            if leg:
                out_series.update(leg); any_update = True
                log(f"[kr] 갱신 — 최신 {leg['kr_call_base_bp']['obs'][-1]}")

    if not any_update:
        log("no-op — 갱신된 다리 없음")
        return 0

    data = {
        "updated": today.isoformat(),
        "source": "FRED(SOFR75·IORB, 미국) · 한국은행 ECOS(817Y002 콜금리·722Y001 기준금리, 한국)",
        "series": out_series,
    }
    with open(OUT, "w", encoding="utf-8") as f:
        f.write("window.RATES_DATA = " + json.dumps(data, ensure_ascii=False) + ";\n")
    print("wrote", OUT)
    return 0

if __name__ == "__main__":
    sys.exit(main())
