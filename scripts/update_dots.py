#!/usr/bin/env python3
"""연준 점도표(SEP, docs/dots.js) 자동 갱신.

docs/cbank.js의 FOMC 항목 중 비고가 "SEP"(경제전망 발표 회의)이고 결정일(종료일)이 지났는데
아직 dots.js에 없는 회의를 찾아, 연준 공식 HTML 발표표
  https://www.federalreserve.gov/monetarypolicy/fomcprojtabl{YYYYMMDD}.htm
에서 "Federal funds rate" 중앙값(median) 행을 가져온다. 날짜(YYYYMMDD)는 그 회의의 종료일.

절대 규칙: "Memo: Projected appropriate policy path" 구간에서 "Federal funds rate" 라벨과
정확히 일치하는 행이 1개가 아니면(0개 또는 2개 이상 — 비교용 "~월 projection" 행과 혼동 위험),
또는 연도 헤더를 명확히 못 찾으면, 그 회의는 건너뛰고 다음 실행에서 재시도한다. 추정 금지.
"""
import os, re, sys, json, datetime, urllib.request
from html.parser import HTMLParser

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CBANK_PATH = os.path.join(ROOT, "docs", "cbank.js")
OUT = os.path.join(ROOT, "docs", "dots.js")
TODAY = datetime.date.today()

LOG = []
def log(msg):
    LOG.append(msg); print(msg)

# ───────────────────────── cbank.js에서 FOMC·SEP 회의 추출 ─────────────────────────

_ENTRY_RE = re.compile(r"\[[^\[\]]*\]")
_ELEM_RE = re.compile(r'"((?:[^"\\]|\\.)*)"|null')

def _parse_entry(entry_text):
    inner = entry_text[1:-1]
    return [m.group(1) if m.group(0) != "null" else None for m in _ELEM_RE.finditer(inner)]

def fomc_sep_meetings():
    """[(decision_date: date, id_str: 'YYYYMMDD'), ...] — cbank.js의 fomc 배열 전체(연도 무관)에서
    비고(3번째 요소)가 'SEP'인 항목만."""
    text = open(CBANK_PATH, encoding="utf-8").read()
    out = []
    for fm in re.finditer(r"fomc\s*:\s*\[", text):
        start = fm.end() - 1
        depth = 0
        i = start
        in_str = False
        while i < len(text):
            c = text[i]
            if in_str:
                if c == '"' and text[i - 1] != "\\":
                    in_str = False
            elif c == '"':
                in_str = True
            elif c in "[":
                depth += 1
            elif c in "]":
                depth -= 1
                if depth == 0:
                    break
            i += 1
        block = text[start:i + 1]
        for em in _ENTRY_RE.finditer(block):
            elems = _parse_entry(em.group(0))
            if len(elems) < 3 or elems[2] != "SEP" or not elems[0]:
                continue
            dd = elems[1] or elems[0]
            try:
                d = datetime.date.fromisoformat(dd)
            except ValueError:
                continue
            out.append(d)
    return sorted(set(out))

# ───────────────────────── 연준 SEP 표 파싱 ─────────────────────────

class _TableCellCollector(HTMLParser):
    """표를 행(row) 단위 셀 텍스트 리스트로 단순화. <sup>(각주)</sup> 내용은 제외."""
    def __init__(self):
        super().__init__()
        self.rows = []
        self._cur_row = None
        self._cur_cell = None
        self._in_sup = 0

    def handle_starttag(self, tag, attrs):
        if tag == "tr":
            self._cur_row = []
        elif tag in ("td", "th"):
            self._cur_cell = []
        elif tag == "sup":
            self._in_sup += 1

    def handle_endtag(self, tag):
        if tag == "tr" and self._cur_row is not None:
            self.rows.append(self._cur_row)
            self._cur_row = None
        elif tag in ("td", "th") and self._cur_cell is not None:
            txt = "".join(self._cur_cell).strip()
            txt = re.sub(r"\s+", " ", txt)
            if self._cur_row is not None:
                self._cur_row.append(txt)
            self._cur_cell = None
        elif tag == "sup":
            self._in_sup = max(0, self._in_sup - 1)

    def handle_data(self, data):
        if self._cur_cell is not None and not self._in_sup:
            self._cur_cell.append(data)

_NUM_RE = re.compile(r"^-?\d+(?:\.\d+)?$")
_YEAR_RE = re.compile(r"^(19|20)\d{2}$")

def fetch_sep_median(decision_date):
    ymd = decision_date.strftime("%Y%m%d")
    url = f"https://www.federalreserve.gov/monetarypolicy/fomcprojtabl{ymd}.htm"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "sr-dashboard-portal-bot/1.0"})
        with urllib.request.urlopen(req, timeout=30) as r:
            html = r.read().decode("utf-8", "replace")
    except Exception as e:
        return None, f"페이지 조회 실패({url}): {e}"
    return _parse_sep_html(html)

def _parse_sep_html(html):
    p = _TableCellCollector()
    p.feed(html)
    rows = p.rows

    # "Memo: Projected appropriate policy path" 행 위치를 찾고, 그 아래에서
    # 연도 헤더 행과 "Federal funds rate" 행을 찾는다.
    memo_idx = None
    for i, row in enumerate(rows):
        if any("memo" in c.lower() and "policy path" in c.lower() for c in row):
            memo_idx = i
            break
    if memo_idx is None:
        return None, "Memo: Projected appropriate policy path 구간을 찾지 못함"

    # 연도 헤더: memo_idx 이후 가까운 행 중, 4자리 연도가 2개 이상 + "longer" 포함 셀이 있는 행
    year_header = None
    for row in rows[memo_idx:memo_idx + 6]:
        years = [c for c in row if _YEAR_RE.match(c)]
        has_longrun = any("longer" in c.lower() for c in row)
        if len(years) >= 3 and has_longrun:
            year_header = row
            break
    if year_header is None:
        return None, "연도 헤더 행을 찾지 못함"
    # Median 그룹은 표의 첫 번째 연도 그룹(가장 앞쪽 "Longer run"까지) — Central Tendency·Range
    # 그룹의 중복된 연도 헤더까지 섞이지 않도록 첫 "Longer run" 열까지만 사용한다.
    first_longrun_idx = next((i for i, c in enumerate(year_header) if "longer" in c.lower()), None)
    if first_longrun_idx is None:
        return None, "Longer run 열을 찾지 못함"
    year_cols = [c for c in year_header[:first_longrun_idx + 1] if _YEAR_RE.match(c) or "longer" in c.lower()]
    if len(year_cols) < 4:
        return None, f"Median 그룹 연도 열이 너무 적음({year_cols})"

    # "Federal funds rate" 행: memo_idx 이후 전체에서 라벨이 정확히 일치하는 행이 1개여야 함
    matches = []
    for row in rows[memo_idx:]:
        if not row:
            continue
        label = row[0].strip().lower()
        if label == "federal funds rate":
            matches.append(row)
    if len(matches) != 1:
        return None, f"'Federal funds rate' 행 매칭 {len(matches)}건 — 명확하지 않아 skip"
    ffr_row = matches[0]

    data_cells = ffr_row[1:1 + len(year_cols)]
    if len(data_cells) < len(year_cols):
        return None, "Federal funds rate 행의 셀 수가 연도 헤더보다 적음"

    median = {}
    for yc, cell in zip(year_cols, data_cells):
        cell = cell.strip()
        key = "longrun" if "longer" in yc.lower() else yc
        if cell == "" or cell == "—" or cell == "-":
            median[key] = None
            continue
        if not _NUM_RE.match(cell):
            return None, f"'{key}' 열 값이 단일 숫자가 아님({cell!r}) — 범위/구간값 혼입 의심, skip"
        v = float(cell)
        if not (0 <= v <= 15):
            return None, f"'{key}' 값이 비정상 범위({v}) — skip"
        median[key] = v

    if sum(1 for v in median.values() if v is not None) < 4:
        return None, "유효 median 값이 4개 미만 — skip"
    return median, None

def load_existing():
    if not os.path.exists(OUT):
        return {}
    text = open(OUT, encoding="utf-8").read()
    s = text.find("{")
    if s < 0:
        return {}
    depth = 0; in_str = False; i = s
    while i < len(text):
        c = text[i]
        if in_str:
            if c == '"' and text[i-1] != "\\": in_str = False
        elif c == '"': in_str = True
        elif c == "{": depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(text[s:i+1]).get("meetings", {})
                except Exception:
                    return {}
        i += 1
    return {}

def main():
    meetings = fomc_sep_meetings()
    existing = load_existing()
    any_update = False
    for d in meetings:
        if d > TODAY:
            continue
        key = d.isoformat()
        if key in existing:
            continue
        median, reason = fetch_sep_median(d)
        if median is None:
            log(f"[{key}] skip — {reason}")
            continue
        existing[key] = {"median": median}
        any_update = True
        log(f"[{key}] 갱신 — {median}")

    if not any_update:
        log("no-op — 갱신 대상 없음")
        return 0

    data = {
        "updated": TODAY.isoformat(),
        "source": "연준 SEP(Summary of Economic Projections) 공식 발표표, federalreserve.gov",
        "meetings": existing,
    }
    with open(OUT, "w", encoding="utf-8") as f:
        f.write("window.DOTS_DATA = " + json.dumps(data, ensure_ascii=False) + ";\n")
    print("wrote", OUT)
    return 0

if __name__ == "__main__":
    sys.exit(main())
