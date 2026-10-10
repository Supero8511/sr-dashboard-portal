#!/usr/bin/env python3
"""연준 점도표(SEP, docs/dots.js) 자동 갱신.

docs/cbank.js의 FOMC 항목 중 비고가 "SEP"(경제전망 발표 회의)이고 결정일(종료일)이 지났는데
아직 dots.js에 없는 회의를 찾아, 연준 공식 HTML 발표표
  https://www.federalreserve.gov/monetarypolicy/fomcprojtabl{YYYYMMDD}.htm
에서 "Federal funds rate" 중앙값(median) 행을 가져온다. 날짜(YYYYMMDD)는 그 회의의 종료일.

표 구조(Table 1): 헤더 2줄(Variable | Median | Central Tendency | Range, 그 아래 연도 행 —
각 그룹마다 2026 2027 2028 2029 Longer run 반복) → 변수 행들 → "Memo: Projected appropriate policy
path" → "Federal funds rate" 행. 즉 연도 헤더는 Memo 행의 '위'에 있다.
연준 HTML은 </td>를 생략하는 경우가 있어 파서가 암묵적 셀 종료를 처리한다.

절대 규칙: "Memo: Projected appropriate policy path" 아래 "Federal funds rate" 라벨과 정확히 일치하는
행이 1개가 아니면(0개 또는 2개 이상 — 비교용 "~월 projection" 행과 혼동 위험),
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
    """[decision_date: date, ...] — cbank.js의 fomc 배열 전체(연도 무관)에서
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
    """표를 행(row) 단위 셀 텍스트 리스트로 단순화. <sup>(각주)</sup> 내용은 제외.
    </td>·</tr>가 생략된 HTML(암묵적 종료)도 처리: 새 <td>/<th>/<tr>/<table>이 시작되면
    진행 중이던 셀·행을 먼저 마감한다."""
    def __init__(self):
        super().__init__()
        self.rows = []
        self._cur_row = None
        self._cur_cell = None
        self._in_sup = 0

    def _flush_cell(self):
        if self._cur_cell is not None:
            txt = re.sub(r"\s+", " ", "".join(self._cur_cell).replace("\xa0", " ")).strip()
            if self._cur_row is not None:
                self._cur_row.append(txt)
            self._cur_cell = None
        self._in_sup = 0

    def _flush_row(self):
        self._flush_cell()
        if self._cur_row is not None:
            self.rows.append(self._cur_row)
            self._cur_row = None

    def handle_starttag(self, tag, attrs):
        if tag == "table":
            self._flush_row()
        elif tag == "tr":
            self._flush_row()
            self._cur_row = []
        elif tag in ("td", "th"):
            self._flush_cell()
            if self._cur_row is None:
                self._cur_row = []
            self._cur_cell = []
        elif tag == "sup":
            self._in_sup += 1

    def handle_endtag(self, tag):
        if tag == "tr":
            self._flush_row()
        elif tag in ("td", "th"):
            self._flush_cell()
        elif tag == "table":
            self._flush_row()
        elif tag == "sup":
            self._in_sup = max(0, self._in_sup - 1)

    def handle_data(self, data):
        if self._cur_cell is not None and not self._in_sup:
            self._cur_cell.append(data)

    def close(self):
        super().close()
        self._flush_row()

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

def _is_year_header(row):
    years = [c for c in row if _YEAR_RE.match(c)]
    has_longrun = any("longer" in c.lower() for c in row)
    return len(years) >= 3 and has_longrun

def _parse_sep_html(html):
    p = _TableCellCollector()
    p.feed(html)
    p.close()
    rows = p.rows

    # "Memo: Projected appropriate policy path" 행 위치
    memo_idx = None
    for i, row in enumerate(rows):
        if any("memo" in c.lower() and "policy path" in c.lower() for c in row):
            memo_idx = i
            break
    if memo_idx is None:
        return None, "Memo: Projected appropriate policy path 구간을 찾지 못함"

    # 연도 헤더: 같은 표에서 Memo 행 '위'로 가장 가까운 행(표 1은 헤더 2줄 + 변수행 8줄 뒤에 Memo 행).
    # 다른 표(Figure 등)의 헤더와 섞이지 않도록 'Variable' 헤더 행(또는 표 시작)을 만나면 중단한다.
    year_header = None
    for j in range(memo_idx - 1, max(-1, memo_idx - 20), -1):
        if _is_year_header(rows[j]):
            year_header = rows[j]
            break
    if year_header is None:
        return None, "Memo 행 위에서 연도 헤더 행을 찾지 못함"

    # Median 그룹은 표의 첫 번째 연도 그룹(첫 "Longer run" 열까지) — Central Tendency·Range 그룹의
    # 반복 헤더와 섞이지 않게 한다.
    first_longrun_idx = next((i for i, c in enumerate(year_header) if "longer" in c.lower()), None)
    if first_longrun_idx is None:
        return None, "Longer run 열을 찾지 못함"
    year_cols = [c for c in year_header[:first_longrun_idx + 1] if _YEAR_RE.match(c) or "longer" in c.lower()]
    n = len(year_cols)
    if n < 4:
        return None, f"Median 그룹 연도 열이 너무 적음({year_cols})"
    # 헤더 전체가 (Median, Central Tendency, Range) 3개 그룹으로 반복되는 구조인지 확인
    total_cols = len([c for c in year_header if _YEAR_RE.match(c) or "longer" in c.lower()])
    if total_cols != 3 * n:
        return None, f"연도 헤더가 3개 그룹 반복 구조가 아님(총 {total_cols}열, 그룹당 {n}열) — skip"

    # "Federal funds rate" 행: memo_idx 이후, 같은 표 안(다음 'Variable' 헤더 전)에서
    # 라벨이 정확히 일치하는 행이 1개여야 함
    matches = []
    for row in rows[memo_idx + 1:]:
        if not row:
            continue
        if row[0].strip().lower() == "variable":
            break  # 다음 표 시작
        if row[0].strip().lower() == "federal funds rate":
            matches.append(row)
    if len(matches) != 1:
        return None, f"'Federal funds rate' 행 매칭 {len(matches)}건 — 명확하지 않아 skip"
    ffr_row = matches[0]

    cells = ffr_row[1:]
    if len(cells) != 3 * n:
        return None, f"Federal funds rate 행 셀 수({len(cells)})가 헤더({3 * n})와 다름 — skip"
    data_cells = cells[:n]
    # 구조 검증: 두 번째 그룹(Central Tendency)의 첫 셀은 범위값(– 또는 -)이거나 단일값이어야 함 —
    # Median 그룹 셀은 반드시 단일 숫자여야 한다(아래에서 검사).
    median = {}
    for yc, cell in zip(year_cols, data_cells):
        cell = cell.strip()
        key = "longrun" if "longer" in yc.lower() else yc
        if cell == "" or cell in ("—", "-", "–"):
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
