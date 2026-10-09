#!/usr/bin/env python3
"""자동차대출 연체(90일 이상 연체 전환율, docs/delinq.js) 자동 갱신.

NY연은 Household Debt and Credit Report(분기)가 매 분기 공개하는 인터랙티브용 데이터 엑셀
(각 릴리스에 1999~최신까지 전체 히스토리가 들어있음, URL 패턴:
 https://www.newyorkfed.org/medialibrary/interactives/householdcredit/data/xls/HHD_C_Report_{YYYY}Q{Q})
에서 "Auto Loan" 90일 이상 연체 전환율(annualized transition into 90+ delinquency) 행을 찾는다.

이 엑셀의 정확한 시트·행 레이아웃은 공식 문서로 고정돼 있지 않아(비공개/변경 가능) 레이블만으로
행을 특정하면 다른 지표(잔액·연체잔액비중 등) 행을 잘못 집을 위험이 있다. 그래서 레이블 매칭만으로
찾은 모든 후보 행 중, 공식 보도자료로 확인한 두 분기의 실측값(검증 앵커)과 정확히 일치하는 단 하나의
후보만 채택한다 — 앵커를 만족하는 후보가 0개나 2개 이상이면 전부 skip(절대 추정하지 않음).

검증 앵커(2026-08-11 NY연은 보도자료 실측):
  2025Q2 = 2.93% , 2026Q2 = 3.00%  (잔액 대비 연율화 90+ 연체 전환 비중)
"""
import os, re, sys, io, json, datetime, urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "docs", "delinq.js")

ANCHOR = {(2025, 2): 2.93, (2026, 2): 3.00}
ANCHOR_TOL = 0.02

def log(msg):
    print(msg)

# ───────────────────────── 1) 최신 입수 가능한 분기 엑셀 찾기 ─────────────────────────

def _fetch(url, timeout=30):
    req = urllib.request.Request(url, headers={"User-Agent": "sr-dashboard-portal-bot/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()

def find_latest_report(max_back=6):
    today = datetime.date.today()
    q = (today.month - 1) // 3 + 1
    y = today.year
    tried = []
    for _ in range(max_back):
        url = f"https://www.newyorkfed.org/medialibrary/interactives/householdcredit/data/xls/HHD_C_Report_{y}Q{q}"
        tried.append(f"{y}Q{q}")
        try:
            raw = _fetch(url)
            if raw[:2] == b"PK":  # xlsx(zip) 매직 바이트
                return raw, y, q, tried
        except Exception:
            pass
        q -= 1
        if q == 0:
            q = 4; y -= 1
    return None, None, None, tried

# ───────────────────────── 2) 분기 헤더 라벨 인식 ─────────────────────────

_QPATTERNS = [
    re.compile(r"^(\d{2}):([1-4])$"),                  # "03:1" -> 2003Q1
    re.compile(r"^(19|20)(\d{2})[:\-\s]?[Qq]?([1-4])$"),# "2003Q1" / "2003:1" / "2003 1"
    re.compile(r"^[Qq]([1-4])\s*[:\-\s]?\s*(19|20)(\d{2})$"),  # "Q1 2003"
]

def parse_quarter_label(s):
    s = str(s).strip()
    m = _QPATTERNS[0].match(s)
    if m:
        yy, q = int(m.group(1)), int(m.group(2))
        return (2000 + yy if yy < 90 else 1900 + yy), q
    m = _QPATTERNS[1].match(s)
    if m:
        return int(m.group(1) + m.group(2)), int(m.group(3))
    m = _QPATTERNS[2].match(s)
    if m:
        return int(m.group(2) + m.group(3)), int(m.group(1))
    return None

# ───────────────────────── 3) 워크북 전체 스캔 → 후보 시리즈 수집 ─────────────────────────

def scan_candidates(xlsx_bytes):
    try:
        import openpyxl
    except ImportError:
        return None, "openpyxl 미설치"
    try:
        wb = openpyxl.load_workbook(io.BytesIO(xlsx_bytes), read_only=True, data_only=True)
    except Exception as e:
        return None, f"워크북 열기 실패: {e}"

    candidates = []  # [{(year,q): value}, ...]
    for ws in wb.worksheets:
        rows = []
        try:
            for row in ws.iter_rows(values_only=True):
                rows.append(row)
        except Exception:
            continue
        header_rows = []  # (row_idx, {col_idx: (year,q)})
        for ri, row in enumerate(rows):
            mapping = {}
            for ci, cell in enumerate(row):
                if cell is None:
                    continue
                yq = parse_quarter_label(cell)
                if yq:
                    mapping[ci] = yq
            if len(mapping) >= 8:
                header_rows.append((ri, mapping))
        if not header_rows:
            continue
        for ri, row in enumerate(rows):
            if not row or row[0] is None:
                continue
            label = str(row[0]).strip().lower()
            if "auto loan" not in label and "auto" not in label:
                continue
            # 이 행보다 위에 있는 가장 가까운 헤더 행 사용
            hdr = None
            for hri, mapping in header_rows:
                if hri < ri:
                    hdr = mapping
                else:
                    break
            if not hdr:
                continue
            series = {}
            for ci, yq in hdr.items():
                if ci >= len(row):
                    continue
                v = row[ci]
                if isinstance(v, (int, float)):
                    series[yq] = round(float(v), 2)
            if series:
                candidates.append({"sheet": ws.title, "row": ri, "label": str(row[0]).strip(), "series": series})
    wb.close()
    return candidates, None

def pick_anchor_matching(candidates):
    ok = []
    for c in candidates:
        s = c["series"]
        if all(yq in s and abs(s[yq] - v) <= ANCHOR_TOL for yq, v in ANCHOR.items()):
            ok.append(c)
    return ok

def main():
    raw, y, q, tried = find_latest_report()
    if raw is None:
        log(f"입수 가능한 분기 엑셀을 찾지 못함 (시도: {tried}) — 건너뛰고 기존 값 유지")
        return 0
    log(f"최신 입수 파일: {y}Q{q}")

    candidates, err = scan_candidates(raw)
    if err:
        log(f"파싱 실패: {err} — 건너뛰고 기존 값 유지")
        return 0
    matched = pick_anchor_matching(candidates)
    if len(matched) != 1:
        log(f"검증 앵커와 정확히 일치하는 후보 {len(matched)}개(1개여야 함, 전체 후보 {len(candidates)}개) "
            f"— skip. 후보: {[(c['sheet'], c['label']) for c in candidates][:20]}")
        return 0

    series = matched[0]["series"]
    obs = sorted(series.items())
    data = {
        "updated": datetime.date.today().isoformat(),
        "source": f"NY연은 Household Debt and Credit Report {y}Q{q} 공개 데이터 — "
                   "자동차대출 90일 이상 연체 전환율(연율화, 잔액 대비 %)",
        "series": {
            "auto_90d_transition_pct": {
                "obs": [[f"{yr}-{(qq-1)*3+1:02d}", v] for (yr, qq), v in obs]
            }
        },
    }
    with open(OUT, "w", encoding="utf-8") as f:
        f.write("window.DELINQ_DATA = " + json.dumps(data, ensure_ascii=False) + ";\n")
    log(f"wrote {OUT} ({len(obs)}개 분기, 최신 {obs[-1]})")
    return 0

if __name__ == "__main__":
    sys.exit(main())
