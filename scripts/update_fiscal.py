#!/usr/bin/env python3
"""미국 재정적자(기초적자, docs/fiscal.js) 자동 갱신.

FRED FYFSD(연방 재정수지, 백만달러) · FYOINT(이자지출, 백만달러) · FYGDP(GDP, 10억달러) ·
연간(회계연도) 공식 시리즈만 사용. 기초재정수지(이자 제외) = 총수지 + 이자지출
(총수지 = 수입-지출, 이자지출은 지출의 일부이므로 되돌려 더하면 이자 제외 수지가 됨).

  primary_pct = (FYFSD + FYOINT) / (FYGDP*1000) * 100
  total_pct   =  FYFSD            / (FYGDP*1000) * 100
  net_int_pct =  FYOINT           / (FYGDP*1000) * 100   (항상 비음수)

검증 앵커(FY2025, OMB 확정치 기준 — 2026-10 기준 BEA/OMB 공식 발표치와 대조 확인됨):
  total ≈ -5.86%, net_interest ≈ 3.20%, primary ≈ -2.65%  (허용오차 ±0.5%p)
FY2025 데이터가 수신됐는데 앵커와 어긋나면 — 집계 방식이 깨졌을 가능성 — 전체를 쓰지 않고 skip.
연 1회 정도만 갱신되는 지표라 매번 전체 시리즈를 다시 받아 덮어쓴다(증분 불필요)."""
import os, sys, json, datetime, urllib.request

FRED_KEY = os.environ.get("FRED_API_KEY", "")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "docs", "fiscal.js")

ANCHOR_FY = 2025
ANCHOR = {"total_pct": -5.86, "net_interest_pct": 3.20, "primary_pct": -2.65}
TOL = 0.5

def log(msg):
    print(msg)

def fred_series(series_id):
    url = (f"https://api.stlouisfed.org/fred/series/observations?series_id={series_id}"
           f"&api_key={FRED_KEY}&file_type=json&observation_start=1962-01-01&sort_order=asc")
    req = urllib.request.Request(url, headers={"User-Agent": "sr-dashboard-portal-bot/1.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        obs = json.load(r)["observations"]
    # FRED 회계연도 시리즈의 날짜는 회계연도 "종료"월(보통 9월) — 연도만 추출해서 키로 사용
    out = {}
    for o in obs:
        if o["value"] == ".":
            continue
        fy = int(o["date"][:4])
        out[fy] = float(o["value"])
    return out

def main():
    if not FRED_KEY:
        log("FRED_API_KEY 없음 — 건너뛰고 기존 값 유지")
        return 0
    try:
        fsd = fred_series("FYFSD")
        oint = fred_series("FYOINT")
        gdp = fred_series("FYGDP")
    except Exception as e:
        log(f"FRED 조회 실패: {e} — 건너뛰고 기존 값 유지")
        return 0

    years = sorted(set(fsd) & set(oint) & set(gdp))
    if not years:
        log("공통 회계연도 없음 — 건너뛰고 기존 값 유지")
        return 0

    primary, total, neti = {}, {}, {}
    for y in years:
        gdp_m = gdp[y] * 1000.0  # 10억달러 → 백만달러
        if gdp_m <= 0:
            continue
        total[y] = round(fsd[y] / gdp_m * 100, 2)
        neti[y] = round(oint[y] / gdp_m * 100, 2)
        primary[y] = round((fsd[y] + oint[y]) / gdp_m * 100, 2)

    if ANCHOR_FY in total:
        bad = [k for k, v in ANCHOR.items()
               if abs({"total_pct": total, "net_interest_pct": neti, "primary_pct": primary}[k][ANCHOR_FY] - v) > TOL]
        if bad:
            got = {k: {"total_pct": total, "net_interest_pct": neti, "primary_pct": primary}[k][ANCHOR_FY] for k in bad}
            log(f"검증 앵커(FY{ANCHOR_FY}) 불일치 {bad} — 계산값 {got} vs 기대 {ANCHOR} — 쓰지 않고 skip")
            return 0

    def pack(d):
        return {"obs": [[f"{y}-01", v] for y, v in sorted(d.items())]}

    data = {
        "updated": datetime.date.today().isoformat(),
        "source": "FRED FYFSD·FYOINT·FYGDP (OMB 공식, 연간 회계연도)",
        "series": {
            "primary_deficit_pct_gdp": pack(primary),
            "total_deficit_pct_gdp": pack(total),
            "net_interest_pct_gdp": pack(neti),
        },
    }
    with open(OUT, "w", encoding="utf-8") as f:
        f.write("window.FISCAL_DATA = " + json.dumps(data, ensure_ascii=False) + ";\n")
    log(f"wrote {OUT} ({len(years)}개 회계연도, 최신 FY{years[-1]} primary={primary[years[-1]]}%)")
    return 0

if __name__ == "__main__":
    sys.exit(main())
