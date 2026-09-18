#!/usr/bin/env python3
"""SR 생활비지수 대시보드 데이터 빌드.
소스: FRED(미국 4종) + KOSIS(한국 CPI 2종) + ECOS(한국 M2) + data/manual/KB엑셀.
모든 소스 성공 시에만 docs/data.js 갱신(실패 시 기존 유지). 산식은 methodology.md 고정."""
import os, sys, json, datetime, urllib.request, urllib.parse
import pandas as pd, numpy as np

FRED_KEY = os.environ.get("FRED_API_KEY", "")
KOSIS_KEY = os.environ.get("KOSIS_API_KEY", "")
ECOS_KEY = os.environ.get("ECOS_API_KEY", "")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

def fred(series_id):
    url = (f"https://api.stlouisfed.org/fred/series/observations?series_id={series_id}"
           f"&api_key={FRED_KEY}&file_type=json&observation_start=1985-01-01")
    with urllib.request.urlopen(url, timeout=60) as r:
        obs = json.load(r)["observations"]
    s = pd.Series({pd.Timestamp(o["date"]): float(o["value"]) for o in obs if o["value"] != "."})
    return s.sort_index()

def kosis(org_id, tbl_id, itm_id, obj_l1):
    """KOSIS 통계자료 API. ◆세팅 시 확인: orgId/tblId/itmId/objL1은 KOSIS 공유서비스에서
    '자가주거비포함 소비자물가지수', '소비자물가지수(전국)' 표로 발급 URL을 만들어 그대로 기입."""
    url = ("https://kosis.kr/openapi/Param/statisticsParameterData.do?method=getList"
           f"&apiKey={KOSIS_KEY}&orgId={org_id}&tblId={tbl_id}&itmId={itm_id}&objL1={obj_l1}"
           "&format=json&jsonVD=Y&prdSe=M&startPrdDe=198501&endPrdDe=" 
           + datetime.date.today().strftime("%Y%m"))
    with urllib.request.urlopen(url, timeout=60) as r:
        rows = json.load(r)
    s = pd.Series({pd.Timestamp(x["PRD_DE"][:4]+"-"+x["PRD_DE"][4:6]+"-01"): float(x["DT"]) for x in rows})
    return s.sort_index()

def ecos(stat_code, item_code):
    """ECOS 100건 페이징. ◆세팅 시 확인: M2(평잔, 원계열) stat/item 코드는 ECOS OpenAPI
    서비스 통계목록에서 검색해 기입. 신·구 계열이 나뉘면 2004-10 증감률 접합(아래 splice)."""
    end = datetime.date.today().strftime("%Y%m")
    url = (f"https://ecos.bok.or.kr/api/StatisticSearch/{ECOS_KEY}/json/kr/1/2000/"
           f"{stat_code}/M/198601/{end}/{item_code}")
    with urllib.request.urlopen(url, timeout=60) as r:
        rows = json.load(r)["StatisticSearch"]["row"]
    s = pd.Series({pd.Timestamp(x["TIME"][:4]+"-"+x["TIME"][4:6]+"-01"): float(x["DATA_VALUE"]) for x in rows})
    return s.sort_index()

def splice_growth(old, new, at="2004-10-01"):
    """구계열을 신계열 레벨로 증감률 접합."""
    at = pd.Timestamp(at)
    old_g = old.pct_change()
    out = new.copy()
    back = new.loc[at]
    idx = [d for d in old.index if d < at][::-1]
    prev = at
    for d in idx:
        back = back / (1 + old_g.loc[prev]) if prev in old_g.index and not np.isnan(old_g.loc[prev]) else back
        out.loc[d] = back
        prev = d
    return out.sort_index()

def read_kb():
    from openpyxl import load_workbook
    mdir = os.path.join(ROOT, "data", "manual")
    xs = sorted([f for f in os.listdir(mdir) if f.endswith(".xlsx")])
    if not xs:
        raise RuntimeError("data/manual/에 KB 아파트 매매가격지수 엑셀이 없습니다")
    wb = load_workbook(os.path.join(mdir, xs[-1]), read_only=True)
    rows = wb.active.iter_rows(values_only=True); h = next(rows)
    dates = pd.to_datetime([d for d in h[1:] if d is not None])
    for r in rows:
        if r[0] == "전국":
            v = [float(x) if x not in (None, "-") else np.nan for x in r[1:1+len(dates)]]
            s = pd.Series(v, index=dates)
            s.index = s.index.to_period("M").to_timestamp()
            return s.sort_index()
    raise RuntimeError("'전국' 행을 찾지 못했습니다")

def yoy(s): return (s / s.shift(12) - 1) * 100

def pack(s, start):
    s = s.dropna(); s = s[s.index >= start]
    return {d.strftime("%Y-%m"): round(float(v), 2) for d, v in s.items()}

def main():
    # --- 미국 (FRED) ---
    pce, cs, cpius, m2us = map(fred, ["PCEPI", "CSUSHPISA", "CPIAUCSL", "M2SL"])
    us_sr = 0.7 * yoy(pce) + 0.3 * yoy(cs)
    # --- 한국 ---
    # ◆확인필요: 아래 4개 파라미터는 세팅 단계에서 KOSIS 발급 URL 값으로 교체
    ooh = kosis("101", "TBL_OOH_TODO", "ITM_TODO", "OBJ_TODO")     # 자가주거비포함지수
    cpikr = kosis("101", "TBL_CPI_TODO", "ITM_TODO", "OBJ_TODO")   # 소비자물가지수(전국)
    # ◆확인필요: ECOS M2 stat/item 코드. 계열이 신·구로 나뉘면:
    # m2 = splice_growth(ecos(구코드...), ecos(신코드...))
    m2kr = ecos("ECOS_STAT_TODO", "ECOS_ITEM_TODO")                # M2 평잔 원계열
    kb = read_kb()
    kr_sr = 0.7 * yoy(ooh) + 0.3 * yoy(kb).reindex(ooh.index)
    data = {
        "meta": {"built": datetime.date.today().isoformat(), "source": "live",
                 "formula": "KR 0.7*OOH+0.3*KB / US 0.7*PCEPI+0.3*CS (yoy)"},
        "kr": {"sr": pack(kr_sr, "1996-01-01"), "cpi": pack(yoy(cpikr), "1996-01-01"),
               "spread": pack(kr_sr - yoy(cpikr), "1996-01-01"), "m2": pack(yoy(m2kr), "1996-01-01")},
        "us": {"sr": pack(us_sr, "1988-01-01"), "cpi": pack(yoy(cpius), "1988-01-01"),
               "spread": pack(us_sr - yoy(cpius), "1988-01-01"), "m2": pack(yoy(m2us), "1988-01-01")},
    }
    # 검증 앵커
    a = data["kr"]["sr"].get("2021-12"); assert a and 7.5 < a < 9.5, f"anchor fail 2021-12: {a}"
    out = os.path.join(ROOT, "docs", "data.js")
    with open(out, "w", encoding="utf-8") as f:
        f.write("const DATA = " + json.dumps(data, ensure_ascii=False) + ";")
    print("wrote", out)

if __name__ == "__main__":
    main()
