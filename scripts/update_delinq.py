#!/usr/bin/env python3
"""자동차대출 연체(90일 이상 연체 전환율, docs/delinq.js) 자동 갱신.

데이터 원천 — NY연은 Household Debt and Credit Report(분기). 두 경로를 모두 시도해 후보를 모은다:
  (1) 인터랙티브 페이지(https://www.newyorkfed.org/householdcredit/hhdc-iframe)가 불러오는 JS(main.js 등)에
      내장된 데이터 배열 — 2004Q1~최신까지 들어 있음. 페이지의 <script src>/<iframe src>를 따라가고
      알려진 경로(.../interactives/householdcredit/js/main.js)도 시도한다.
  (2) 분기 데이터 엑셀(.../data/xls/HHD_C_Report_{YYYY}Q{Q}) — 시트 레이아웃이 문서화돼 있지 않아
      가로(분기가 헤더 행)·세로(분기가 첫 열) 두 방향을 모두 스캔.

어느 쪽이든 내부 레이아웃이 공식적으로 고정돼 있지 않으므로, 찾은 모든 후보 시리즈 중 공식 보도자료로
확인한 두 분기의 실측값(검증 앵커)과 정확히 일치하는 '단 하나의 시리즈'만 채택한다 —
0개거나 2개 이상(값이 서로 다른 후보)이면 전부 skip(절대 추정하지 않음). 동일 값을 가진 중복 후보는 하나로 본다.
(값이 비율(0.0293)로 저장된 경우를 위해 ×1, ×100 두 스케일을 시도한다.)

검증 앵커(2026-08-11 NY연은 보도자료 실측):
  2025Q2 = 2.93% , 2026Q2 = 3.00%  (잔액 대비 연율화 90+ 연체 전환 비중)

라벨이 없는 단순 숫자 배열은 '끝에서 k번째가 2026Q2' 위치 관계(2026Q2=3.00 이고 그 4분기 전 2025Q2=2.93)로만
앵커를 확인하고 분기를 부여한다(k=0..8, 최신 릴리스가 한두 분기 더 진행돼도 동작).

못 찾았을 때는 수집한 문서 목록·크기·후보 수 등 진단 정보를 로그로 남겨 다음 보정에 쓸 수 있게 한다.
"""
import os, re, sys, io, json, datetime, urllib.request, urllib.parse

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "docs", "delinq.js")

ANCHOR = {(2025, 2): 2.93, (2026, 2): 3.00}
ANCHOR_TOL = 0.02

IFRAME_PAGE = "https://www.newyorkfed.org/householdcredit/hhdc-iframe"
KNOWN_JS = [
    "https://www.newyorkfed.org/medialibrary/interactives/householdcredit/js/main.js",
]
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36 sr-dashboard-portal-bot/1.0"

def log(msg):
    print(msg)

def _fetch(url, timeout=40):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()

# ───────────────────────── 분기 라벨 인식 ─────────────────────────

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
    if isinstance(s, (int, float)) and not isinstance(s, bool):
        return None
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

def add_quarters(yq, k):
    y, q = yq
    t = y * 4 + (q - 1) + k
    return (t // 4, t % 4 + 1)

# ───────────────────────── 경로 (1): 인터랙티브 JS 내장 데이터 ─────────────────────────

_SRC_RE = re.compile(r"""(?:src|href)\s*=\s*["']([^"']+)["']""", re.I)

def discover_documents():
    """인터랙티브 페이지와 그 하위 JS/JSON을 수집 → [(url, text)], 진단 로그용 문자열 리스트"""
    docs, notes, seen = [], [], set()
    queue = [IFRAME_PAGE] + KNOWN_JS
    while queue and len(seen) < 14:
        url = queue.pop(0)
        if url in seen:
            continue
        seen.add(url)
        try:
            raw = _fetch(url)
        except Exception as e:
            notes.append(f"  ✗ {url}: {e}")
            continue
        text = raw.decode("utf-8", "replace")
        notes.append(f"  ✓ {url}: {len(raw)} bytes")
        docs.append((url, text))
        if len(raw) > 6_000_000:
            continue
        # 같은 사이트의 하위 JS/JSON/iframe만 따라간다
        for m in _SRC_RE.finditer(text):
            ref = m.group(1).strip()
            if not re.search(r"\.(js|json)(\?|$)|householdcredit|hhdc", ref, re.I):
                continue
            full = urllib.parse.urljoin(url, ref)
            if urllib.parse.urlparse(full).netloc.endswith("newyorkfed.org") and full not in seen and full not in queue:
                if re.search(r"\.(png|gif|jpg|jpeg|css|svg|pdf|xlsx?)(\?|$)", full, re.I):
                    continue
                queue.append(full)
    return docs, notes

def _js_literal_to_json(s):
    """JS 배열/객체 리터럴을 json.loads 가능한 형태로 최소 변환."""
    s = re.sub(r"\bundefined\b|\bNaN\b|\bInfinity\b", "null", s)
    # 단따옴표 문자열 → 쌍따옴표 (문자열 안에 쌍따옴표가 없다고 가정, 아니면 파싱 실패로 skip)
    s = re.sub(r"'([^'\"\\\n]*)'", r'"\1"', s)
    # 따옴표 없는 키
    s = re.sub(r'([\{,]\s*)([A-Za-z_$][\w$]*)\s*:', r'\1"\2":', s)
    # 후행 쉼표
    s = re.sub(r",\s*([\]\}])", r"\1", s)
    return s

def _balanced(text, start, limit=3_000_000):
    """text[start]가 '['일 때 짝이 맞는 ']' 위치를 반환(문자열 리터럴 고려). 실패 시 -1."""
    depth = 0
    i = start
    n = min(len(text), start + limit)
    in_s = None
    while i < n:
        c = text[i]
        if in_s:
            if c == "\\":
                i += 2
                continue
            if c == in_s:
                in_s = None
        elif c in "\"'":
            in_s = c
        elif c == "[":
            depth += 1
        elif c == "]":
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return -1

def extract_arrays(text, min_len=200, max_arrays=400):
    """텍스트에서 숫자/객체/문자열로 시작하는 큰 배열 리터럴을 파싱해 python 객체로."""
    out = []
    i = 0
    n = len(text)
    while i < n and len(out) < max_arrays:
        j = text.find("[", i)
        if j < 0:
            break
        k = j + 1
        while k < n and text[k] in " \t\r\n":
            k += 1
        if k >= n or not (text[k].isdigit() or text[k] in "-{\"'["):
            i = j + 1
            continue
        end = _balanced(text, j)
        if end < 0:
            i = j + 1
            continue
        frag = text[j:end + 1]
        if len(frag) >= min_len:
            try:
                out.append(json.loads(_js_literal_to_json(frag)))
                i = end + 1
                continue
            except Exception:
                pass
        i = j + 1
    return out

def _is_numlist(a, min_len=30):
    if not isinstance(a, list) or len(a) < min_len:
        return False
    nums = sum(1 for v in a if _num(v) is not None)
    return nums >= 0.9 * len(a)

def candidates_from_object(obj, tag):
    """파싱된 객체에서 후보 시리즈 {(y,q):v}를 수집.
    - 라벨(분기 문자열)이 있는 [[라벨, 값, ...], ...] / [{label:.., k:v}, ...] → 라벨 기준
    - 라벨 없는 숫자 배열 → 앵커 위치 관계로 분기 부여(add_quarters)"""
    cands = []

    def positional(nums, label):
        nums = [_num(v) for v in nums]
        L = len(nums)
        for k in range(0, 9):
            i26 = L - 1 - k      # 2026Q2 위치 (k분기 더 진행된 릴리스 가정)
            i25 = i26 - 4        # 2025Q2
            if i25 < 0 or i26 >= L:
                continue
            a, b = nums[i25], nums[i26]
            if a is None or b is None:
                continue
            for scale in (1.0, 100.0):
                if abs(a * scale - ANCHOR[(2025, 2)]) <= ANCHOR_TOL and abs(b * scale - ANCHOR[(2026, 2)]) <= ANCHOR_TOL:
                    last = add_quarters((2026, 2), k)
                    series = {}
                    for idx, v in enumerate(nums):
                        if v is None:
                            continue
                        series[add_quarters(last, idx - (L - 1))] = round(v * scale, 2)
                    cands.append({"sheet": tag, "kind": "pos", "label": f"{label} (끝에서 {k}번째=2026Q2)", "series": series})

    def walk(o, path, depth=0):
        if depth > 6:
            return
        if _is_numlist(o):
            positional(o, path)
            return
        if isinstance(o, dict):
            # 값이 숫자 배열인 키들
            for key, v in o.items():
                walk(v, f"{path}.{key}", depth + 1)
            # 분기 라벨 → 숫자 맵
            labelled = {parse_quarter_label(k): v for k, v in o.items() if parse_quarter_label(k)}
            if len(labelled) >= 30:
                s = {yq: _num(v) for yq, v in labelled.items() if _num(v) is not None}
                if s:
                    cands.append({"sheet": tag, "kind": "map", "label": path, "series": s})
        elif isinstance(o, list) and len(o) >= 30:
            # 행 단위 레코드: [label, v1, v2..] 또는 {label.., a:.., b:..}
            rows = o
            if all(isinstance(r, list) for r in rows[:5]):
                width = max(len(r) for r in rows)
                lab_idx = None
                for ci in range(min(width, 3)):
                    if sum(1 for r in rows if ci < len(r) and parse_quarter_label(r[ci])) >= 0.8 * len(rows):
                        lab_idx = ci
                        break
                for ci in range(width):
                    col = [(r[lab_idx] if lab_idx is not None else None, r[ci] if ci < len(r) else None) for r in rows]
                    nums = [_num(v) for _, v in col]
                    if sum(1 for v in nums if v is not None) < 0.8 * len(rows) or ci == lab_idx:
                        continue
                    if lab_idx is not None:
                        s = {parse_quarter_label(l): _num(v) for l, v in col if parse_quarter_label(l) and _num(v) is not None}
                        if s:
                            cands.append({"sheet": tag, "kind": "rows", "label": f"{path}[col{ci}]", "series": s})
                    elif _is_numlist([v for _, v in col]):
                        positional([v for _, v in col], f"{path}[col{ci}]")
            elif all(isinstance(r, dict) for r in rows[:5]):
                keys = []
                for r in rows[:5]:
                    for kk in r:
                        if kk not in keys:
                            keys.append(kk)
                lab_key = next((kk for kk in keys if sum(1 for r in rows if parse_quarter_label(r.get(kk))) >= 0.8 * len(rows)), None)
                for kk in keys:
                    if kk == lab_key:
                        continue
                    vals = [r.get(kk) for r in rows]
                    if sum(1 for v in vals if _num(v) is not None) < 0.8 * len(rows):
                        continue
                    if lab_key:
                        s = {parse_quarter_label(r.get(lab_key)): _num(r.get(kk)) for r in rows
                             if parse_quarter_label(r.get(lab_key)) and _num(r.get(kk)) is not None}
                        if s:
                            cands.append({"sheet": tag, "kind": "records", "label": f"{path}.{kk}", "series": s})
                    else:
                        positional(vals, f"{path}.{kk}")
            else:
                for idx, v in enumerate(rows[:60]):
                    walk(v, f"{path}[{idx}]", depth + 1)
        elif isinstance(o, list):
            for idx, v in enumerate(o):
                walk(v, f"{path}[{idx}]", depth + 1)

    walk(obj, tag)
    return cands

def js_candidates(docs):
    cands, stats = [], []
    for url, text in docs:
        arrays = extract_arrays(text)
        n_before = len(cands)
        for ai, arr in enumerate(arrays):
            cands.extend(candidates_from_object(arr, f"{url.rsplit('/', 1)[-1]}#arr{ai}"))
        stats.append(f"  - {url.rsplit('/', 1)[-1]}: 배열 {len(arrays)}개 파싱, 후보 +{len(cands) - n_before}")
    return cands, stats

# ───────────────────────── 경로 (2): 분기 엑셀 ─────────────────────────

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
    """sheets: [(title, rows)] → 후보 [{sheet, kind, label, series{(y,q):v}}] (가로·세로 모두)"""
    cands = []
    for title, rows in sheets:
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

        ncols = max((len(r) for r in rows), default=0)
        for qc in range(min(ncols, 4)):
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

def describe_workbook(sheets, max_sheets=20):
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

# ───────────────────────── 앵커 검증 · 선택 ─────────────────────────

def pick_anchor_matching(candidates):
    ok = []
    for c in candidates:
        for scale in (1.0, 100.0):
            s = {yq: round(v * scale, 2) for yq, v in c["series"].items()}
            if all(yq in s and abs(s[yq] - v) <= ANCHOR_TOL for yq, v in ANCHOR.items()):
                ok.append(dict(c, series=s))
                break
    uniq = []
    for c in ok:
        if not any(u["series"] == c["series"] for u in uniq):
            uniq.append(c)
    return uniq

def write_out(c, source_note):
    obs = sorted(c["series"].items())
    data = {
        "updated": datetime.date.today().isoformat(),
        "source": f"NY연은 Household Debt and Credit Report ({source_note}) — "
                   "자동차대출 90일 이상 연체 전환율(연율화, 잔액 대비 %)",
        "series": {
            "auto_90d_transition_pct": {
                "obs": [[f"{yr}-{(qq-1)*3+1:02d}", v] for (yr, qq), v in obs]
            }
        },
    }
    with open(OUT, "w", encoding="utf-8") as f:
        f.write("window.DELINQ_DATA = " + json.dumps(data, ensure_ascii=False) + ";\n")
    log(f"wrote {OUT} ({len(obs)}개 분기, 최신 {obs[-1]}) — 채택: {c['sheet']} / {c['label']} [{c['kind']}]")

def main():
    all_cands = []

    # (1) 인터랙티브 JS 내장 배열
    docs, notes = discover_documents()
    log("인터랙티브 문서 수집:\n" + "\n".join(notes))
    js_c, js_stats = js_candidates(docs)
    log("JS 후보 추출:\n" + ("\n".join(js_stats) if js_stats else "  (수집된 문서 없음)"))
    matched = pick_anchor_matching(js_c)
    if len(matched) == 1:
        write_out(matched[0], "인터랙티브 내장 데이터")
        return 0
    log(f"JS 경로: 앵커 일치 후보 {len(matched)}개(전체 {len(js_c)}개) — 엑셀 경로 시도")
    all_cands.extend(js_c)

    # (2) 분기 엑셀
    raw, y, q, tried = find_latest_report()
    if raw is None:
        log(f"입수 가능한 분기 엑셀을 찾지 못함 (시도: {tried})")
    else:
        log(f"최신 입수 파일: {y}Q{q} ({len(raw)} bytes)")
        sheets, err = _load_rows(raw)
        if err:
            log(f"엑셀 파싱 실패: {err}")
        else:
            xl_c = scan_rows(sheets)
            matched = pick_anchor_matching(xl_c)
            if len(matched) == 1:
                write_out(matched[0], f"{y}Q{q} 엑셀")
                return 0
            log(f"엑셀 경로: 앵커 일치 후보 {len(matched)}개(전체 {len(xl_c)}개)")
            all_cands.extend(xl_c)
            if not xl_c:
                log("워크북 구조(진단용):\n" + describe_workbook(sheets))

    # 어느 경로도 확정 못 함 → skip (추정 금지)
    both = pick_anchor_matching(all_cands)
    log(f"최종: 앵커와 일치하는 단일 시리즈를 확정하지 못함(일치 {len(both)}개, 전체 후보 {len(all_cands)}개) — 건너뛰고 기존 값 유지")
    for c in all_cands[:8]:
        got = {f"{a}Q{b}": c["series"].get((a, b)) for (a, b) in ANCHOR}
        log(f"  후보 {c['sheet']}/{c['label']} [{c['kind']}]: 앵커 분기 값 {got}")
    return 0

if __name__ == "__main__":
    sys.exit(main())
