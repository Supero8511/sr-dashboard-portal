#!/usr/bin/env python3
"""자동차대출 연체(90일 이상 연체 전환율, docs/delinq.js) 자동 갱신.

NY연은 Household Debt and Credit Report(분기)가 매 분기 공개하는 인터랙티브용 데이터 엑셀
(각 릴리스에 1999~최신까지 전체 히스토리가 들어있음, URL 패턴:
 https://www.newyorkfed.org/medialibrary/interactives/householdcredit/data/xls/HHD_C_Report_{YYYY}Q{Q})
에서 "Auto Loan" 90일 이상 연체 전환율(annualized transition into 90+ delinquency) 시리즈를 찾는다.

이 엑셀의 정확한 시트·행·열 레이아웃은 공식 문서로 고정돼 있지 않다(비공개/변경 가능).
분기 라벨이 가로(헤더 행)로 놓였을 수도, 세로(첫 열)로 놓였을 수도 있어 두 방향을 모두 스캔한다.
레이블 매칭만으로 시리즈를 특정하면 다른 지표(잔액·연체잔액비중 등)를 잘못 집을 위험이 있으므로,
찾은 모든 후보 중 공식 보도자료로 확인한 두 분기의 실측값(검증 앵커)과 정확히 일치하는
단 하나의 후보만 채택한다 — 앵커를 만족하는 후보가 0개나 2개 이상이면 전부 skip(절대 추정하지 않음).
(값이 비율(0.0293)로 저장된 경우를 위해 ×1, ×100 두 스케일을 모두 시도한다.)

검증 앵커(2026-08-11 NY연은 보도자료 실측):
  2025Q2 = 2.93% , 2026Q2 = 3.00%  (잔액 대비 연율화 90+ 연체 전환 비중)

못 찾았을 때는 워크북 구조(시트명·상단 몇 줄)를 로그로 남겨 다음 보정에 쓸 수 있게 한다.
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
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (sr-dashboard-portal-bot/1.0)"})
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

# ───────────────────────── 2) 분기 라벨 인식 ─────────────────────────

_QPATTERNS = [
    (re.compile(r"^(\d{2})[:\-\s]?[Qq]?([1-4])$"), "yy_q"),                 # "03:Q1" "03:1" "03Q1"
    (re.compile(r"^(19|20)(\d{2})[:\-\s]?[Qq]?([1-4])$"), "yyyy_q"),         # "2003Q1" "2003:Q1" "2003 1"
    (re.compile(r"^[Qq]([1-4])[:\-\s]*(19|20)(\d{2})$"), "q_yyyy"),          # "Q1 2003"
    (re.compile(r"^[Qq]([1-4])[:\-\s]*(\d{2})$"), "q_yy"),                   # "Q1:03" "Q1 03"
]

def _yy(yy):
    return 2000 + yy if yy < 90 else 1900 + yy

def parse_quarter_label(s):
    if s is None:
        return None
    if isinstance(s, (datetime.datetime, datetime.date)):
        return (s.year, (s.month - 1) // 3 + 1)
    s = str(s).strip()
    if not s:
        return None
    for pat, kind in _QPATTERNS:
        m = pat.match(s)
        if not m:
            continue
        if kind == "yy_q":
            return _yy(int(m.group(1))), int(m.group(2))
        if kind == "yyyy_q":
            return int(m.group(1) + m.group(2)), int(m.group(3))
        if kind == "q_yyyy":
            return int(m.group(2) + m.group(3)), int(m.group(1))
        if kind == "q_yy":
            return _yy(int(m.group(2))), int(m.group(1))
    return None

def _num(v):
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    return None

# ───────────────────────── 3) 워크북 전체 스캔 → 후보 시리즈 수집 (가로·세로 모두) ─────────────────────────

def _load_rows(xlsx_bytes):
    try:
        import openpyxl
    except ImportError:
        return None, "openpyxl 미설치"
    try:
        wb = openpyxl.load_workbook(io.BytesIO(xlsx_bytes), read_only=True, data_only=True)
    except Exception as e:
        return None, f"워크북 열기 실패: {e}"
    sheets = []
    for ws in wb.worksheets:
        rows = []
        try:
            for row in ws.iter_rows(values_only=True):
                rows.append(list(row))
        except Exception:
            continue
        sheets.append((ws.title, rows))
    wb.close()
    return sheets, None

def scan_rows(sheets):
    """sheets: [(title, rows)] → 후보 [{sheet, kind, label, series{(y,q):v}}]"""
    cands = []
    for title, rows in sheets:
        # ── 방향 A: 분기 라벨이 헤더 행(가로)에 있고, 'auto' 행의 값이 그 아래 ──
        header_rows = []
        for ri, row in enumerate(rows):
            mapping = {}
            for ci, cell in enumerate(row):
                yq = parse_quarter_label(cell)
                if yq:
                    mapping[ci] = yq
            if len(mapping) >= 8:
                header_rows.append((ri, mapping))
        if header_rows:
            for ri, row in enumerate(rows):
                if not row or row[0] is None:
                    continue
                label = str(row[0]).strip()
                if "auto" not in label.lower():
                    continue
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
                    if ci < len(row):
                        v = _num(row[ci])
                        if v is not None:
                            series[yq] = v
                if series:
                    cands.append({"sheet": title, "kind": "row", "label": label, "series": series})

        # ── 방향 B: 분기 라벨이 한 열(세로)에 있고, 'auto'가 열 머리글 ──
        ncols = max((len(r) for r in rows), default=0)
        for qc in range(min(ncols, 4)):  # 분기 열은 보통 맨 앞쪽
            qrows = {}
            for ri, row in enumerate(rows):
                if qc < len(row):
                    yq = parse_quarter_label(row[qc])
                    if yq:
                        qrows[ri] = yq
            if len(qrows) < 8:
                continue
            first_q = min(qrows)
            for cj in range(ncols):
                if cj == qc:
                    continue
                head_txt = " ".join(str(rows[ri][cj]).strip() for ri in range(max(0, first_q - 8), first_q)
                                    if cj < len(rows[ri]) and rows[ri][cj] is not None)
                if "auto" not in head_txt.lower():
                    continue
                series = {}
                for ri, yq in qrows.items():
                    if cj < len(rows[ri]):
                        v = _num(rows[ri][cj])
                        if v is not None:
                            series[yq] = v
                if series:
                    cands.append({"sheet": title, "kind": "col", "label": head_txt.strip()[:60], "series": series})
    return cands

def scan_candidates(xlsx_bytes):
    sheets, err = _load_rows(xlsx_bytes)
    if err:
        return None, None, err
    return scan_rows(sheets), sheets, None

def describe_workbook(sheets, max_sheets=20):
    """매칭 실패 시 다음 보정을 위한 구조 요약 로그."""
    out = []
    for title, rows in sheets[:max_sheets]:
        sample = []
        for row in rows:
            cells = [str(c)[:14] for c in row[:8] if c is not None]
            if cells:
                sample.append("|".join(cells))
            if len(sample) >= 3:
                break
        out.append(f"  - 시트 '{title}' ({len(rows)}행): {sample}")
    return "\n".join(out)

def pick_anchor_matching(candidates):
    ok = []
    for c in candidates:
        for scale in (1.0, 100.0):
            s = {yq: round(v * scale, 2) for yq, v in c["series"].items()}
            if all(yq in s and abs(s[yq] - v) <= ANCHOR_TOL for yq, v in ANCHOR.items()):
                ok.append(dict(c, series=s))
                break
    return ok

def main():
    raw, y, q, tried = find_latest_report()
    if raw is None:
        log(f"입수 가능한 분기 엑셀을 찾지 못함 (시도: {tried}) — 건너뛰고 기존 값 유지")
        return 0
    log(f"최신 입수 파일: {y}Q{q} ({len(raw)} bytes)")

    candidates, sheets, err = scan_candidates(raw)
    if err:
        log(f"파싱 실패: {err} — 건너뛰고 기존 값 유지")
        return 0
    matched = pick_anchor_matching(candidates)
    # 같은 데이터가 여러 시트에 중복 수록된 경우(시리즈 값이 완전히 동일)는 하나로 취급
    uniq = []
    for c in matched:
        if not any(u["series"] == c["series"] for u in uniq):
            uniq.append(c)
    matched = uniq
    if len(matched) != 1:
        log(f"검증 앵커와 정확히 일치하는 후보 {len(matched)}개(1개여야 함, 전체 후보 {len(candidates)}개) — skip.")
        log("후보: " + str([(c['sheet'], c['kind'], c['label']) for c in candidates][:20]))
        if len(candidates) == 0:
            log("워크북 구조(진단용):\n" + describe_workbook(sheets))
        else:
            for c in candidates[:8]:
                got = {f"{a}Q{b}": c["series"].get((a, b)) for (a, b) in ANCHOR}
                log(f"  후보 '{c['sheet']}'/{c['label']}: 앵커 분기 값 {got}")
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
    log(f"wrote {OUT} ({len(obs)}개 분기, 최신 {obs[-1]}) — 채택: 시트 '{matched[0]['sheet']}' / {matched[0]['label']}")
    return 0

if __name__ == "__main__":
    sys.exit(main())
