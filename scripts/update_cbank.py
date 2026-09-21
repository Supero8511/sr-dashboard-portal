#!/usr/bin/env python3
"""통화정책 일정(docs/cbank.js) 결과 자동 갱신.

회의 종료(발표)일이 지났는데 '결과'가 비어 있는 항목을 각 은행의 공식/공개 데이터
원천에서 직접 조회한 확정 금리로 채운다.

절대 규칙: 원천 조회가 실패하거나 값이 애매하면(파싱 실패, 날짜 불일치, 확신 불가)
'절대 추정하지 않고' 그 항목만 건너뛴다 — 다음 실행에서 재시도된다. 다른 항목/은행에는
영향 없음. 이 규칙은 methodology_external.md가 원래 수동 게이트를 둔 이유(조기 확정·오보
방지)를 자동화 안에서도 지키기 위함이다.

원천:
  - FOMC : FRED DFEDTARU/DFEDTARL (목표범위 상·하단, 일별, 공식) — FRED_API_KEY 필요
  - BOK  : ECOS 722Y001/0101000 (한국은행 기준금리, 일별, 공식) — ECOS_API_KEY 필요
  - ECB  : ECB Data Portal API FM.D.U2.EUR.4F.KR.DFR.LEV (수신금리 DFR, 일별, 공식,
           키 불필요) — 값이 바뀐 날짜를 그대로 발효일(5번째 요소)로 사용.
           ECB는 결정일과 발효일(통상 다음 MRO 결제일, 약 6~8일 후)이 다르므로,
           결정일로부터 10일이 지나기 전에는 절대 조회하지 않는다(그 전에 조회하면
           아직 옛 금리가 유지 중인 걸 '동결'로 오판할 위험 — 실제로는 변경이 예정돼
           있을 뿐 아직 발효 전인 경우가 있기 때문).
  - BOJ  : 목표금리를 바로 주는 공식 숫자 API/피드가 없고(성명은 매번 다른 URL의 PDF로만
           발표), 안정적으로 예측 가능한 URL 규칙도 확인되지 않았다. 그래서 BOJ만은
           이 스크립트가 URL을 스스로 찾지 않는다 — 성명 PDF URL을 BOJ_STATEMENT_URL
           환경변수로 넘겨준 경우에 한해 그 안의 "around X percent" 문구를 파싱해 채택
           (패턴이 정확히 1개로 명확히 매칭될 때만). 값을 넘기지 않으면 항상 건너뛰고
           기존 수동 절차("Claude에게 업데이트 요청")로 남는다 — 이는 자동화 범위의
           의도적인 한계이지 버그가 아니다.
"""
import os, re, sys, json, datetime, urllib.request

FRED_KEY = os.environ.get("FRED_API_KEY", "")
ECOS_KEY = os.environ.get("ECOS_API_KEY", "")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CBANK_PATH = os.path.join(ROOT, "docs", "cbank.js")

TODAY = datetime.date.today()
LOG = []


def log(bank, date, action, detail=""):
    line = f"[{bank} {date}] {action}" + (f" — {detail}" if detail else "")
    LOG.append(line)
    print(line)


# ───────────────────────── 원천 조회 ─────────────────────────

def _http_json(url, timeout=30):
    req = urllib.request.Request(url, headers={"User-Agent": "sr-dashboard-portal-bot/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def _fred_obs(series_id, start):
    url = (f"https://api.stlouisfed.org/fred/series/observations?series_id={series_id}"
           f"&api_key={FRED_KEY}&file_type=json&observation_start={start}&sort_order=asc")
    obs = _http_json(url)["observations"]
    return [(o["date"], o["value"]) for o in obs if o["value"] != "."]


def fomc_rate(decision_date):
    if not FRED_KEY:
        return None, "FRED_API_KEY 없음"
    start = decision_date.isoformat()
    try:
        lo = _fred_obs("DFEDTARL", start)
        hi = _fred_obs("DFEDTARU", start)
    except Exception as e:
        return None, f"FRED 조회 실패: {e}"
    if not lo or not hi:
        return None, "결정일 이후 관측치 없음(아직 미반영)"
    lo_d, lo_v = lo[0]
    hi_d, hi_v = hi[0]
    if lo_d != hi_d:
        return None, f"상/하단 날짜 불일치({lo_d} vs {hi_d}) — 확신 불가"
    return {"label": f"{float(lo_v):.2f}~{float(hi_v):.2f}%", "sort_key": (float(lo_v) + float(hi_v)) / 2,
            "effective": None}, None


def bok_rate(decision_date):
    if not ECOS_KEY:
        return None, "ECOS_API_KEY 없음"
    start = decision_date.strftime("%Y%m%d")
    end = (TODAY + datetime.timedelta(days=1)).strftime("%Y%m%d")
    url = (f"https://ecos.bok.or.kr/api/StatisticSearch/{ECOS_KEY}/json/kr/1/100/"
           f"722Y001/D/{start}/{end}/0101000")
    try:
        data = _http_json(url)
    except Exception as e:
        return None, f"ECOS 조회 실패: {e}"
    rows = (data.get("StatisticSearch") or {}).get("row") or []
    rows = [(x["TIME"], x["DATA_VALUE"]) for x in rows if len(x.get("TIME", "")) == 8]
    if not rows:
        return None, "결정일 이후 관측치 없음"
    d, v = rows[-1]
    try:
        val = float(v)
    except ValueError:
        return None, f"값 파싱 실패: {v!r}"
    return {"label": f"{val:.2f}%", "sort_key": val, "effective": None}, None


def ecb_rate(decision_date):
    start = decision_date.isoformat()
    url = (f"https://data-api.ecb.europa.eu/service/data/FM/FM.D.U2.EUR.4F.KR.DFR.LEV"
           f"?format=jsondata&startPeriod={start}")
    try:
        data = _http_json(url)
    except Exception as e:
        return None, f"ECB 조회 실패: {e}"
    try:
        series_obj = data["dataSets"][0]["series"]
        key0 = next(iter(series_obj))
        obs_map = series_obj[key0]["observations"]
        date_values = data["structure"]["dimensions"]["observation"][0]["values"]
        pairs = []
        for idx_str, arr in obs_map.items():
            idx = int(idx_str)
            date_id = date_values[idx]["id"]
            pairs.append((date_id, arr[0]))
        pairs.sort()
    except Exception as e:
        return None, f"ECB 응답 파싱 실패: {e}"
    if not pairs:
        return None, "결정일 이후 관측치 없음"
    d, v = pairs[-1]
    try:
        val = float(v)
    except (TypeError, ValueError):
        return None, f"값 파싱 실패: {v!r}"
    # 발효일 = 값이 바뀐 그 날짜. 여러 관측치가 왔으면 마지막(최신) 값이 바뀐 시점을 찾는다.
    effective = d
    for date_id, vv in pairs:
        try:
            if float(vv) == val:
                effective = date_id
                break
        except (TypeError, ValueError):
            continue
    return {"label": f"{val:.2f}%", "sort_key": val, "effective": effective}, None


_BOJ_PCT_RE = re.compile(
    r"uncollateralized\s+overnight\s+call\s+rate[^.]{0,120}?(?:at\s+)?around\s+([0-9]+(?:\.[0-9]+)?)\s*(?:to\s+([0-9]+(?:\.[0-9]+)?)\s*)?percent",
    re.IGNORECASE | re.DOTALL,
)


def boj_rate(decision_date):
    """BOJ는 목표금리 숫자 API가 없음 — 정책 성명 PDF 텍스트에서 1개의 명확한 패턴만
    매칭될 때 채택. 조금이라도 애매하면 skip(수동 절차로 남김)."""
    try:
        import pypdf  # noqa: F401
    except ImportError:
        return None, "pypdf 미설치 — requirements에 추가 필요"
    # 성명 PDF는 URL이 회차마다 다르고 공식 목록 페이지 파싱이 별도로 필요해
    # PDF_URL_HINT 환경변수(워크플로에서 연도별 인덱스를 먼저 조회해 넘겨줌)가 없으면 skip.
    pdf_url = os.environ.get("BOJ_STATEMENT_URL", "")
    if not pdf_url:
        return None, "성명 PDF URL 미확인 — 수동 확인 필요(자동 목록 조회 미구현)"
    import io
    try:
        req = urllib.request.Request(pdf_url, headers={"User-Agent": "sr-dashboard-portal-bot/1.0"})
        with urllib.request.urlopen(req, timeout=30) as r:
            raw = r.read()
        reader = pypdf.PdfReader(io.BytesIO(raw))
        text = "\n".join(page.extract_text() or "" for page in reader.pages)
    except Exception as e:
        return None, f"PDF 조회/파싱 실패: {e}"
    matches = _BOJ_PCT_RE.findall(text)
    if len(matches) != 1:
        return None, f"패턴 매칭 {len(matches)}건 — 명확하지 않아 skip"
    lo, hi = matches[0]
    if hi:
        label = f"{float(lo):.2f}~{float(hi):.2f}%"
        sort_key = (float(lo) + float(hi)) / 2
    else:
        label = f"{float(lo):.2f}%"
        sort_key = float(lo)
    return {"label": label, "sort_key": sort_key, "effective": None}, None


FETCHERS = {"fomc": fomc_rate, "bok": bok_rate, "ecb": ecb_rate, "boj": boj_rate}


# ───────────────────────── cbank.js 파싱/편집 ─────────────────────────

_PREV_RATE_RE = re.compile(r"([\d.]+)(?:~([\d.]+))?%")


def prev_sort_key(label):
    m = _PREV_RATE_RE.search(label)
    if not m:
        return None
    lo = float(m.group(1))
    hi = float(m.group(2)) if m.group(2) else None
    return (lo + hi) / 2 if hi is not None else lo


def classify(prev_key, new_key):
    """이전 확정 결과가 없으면 방향(인상/인하/동결)을 판단할 근거가 없으므로 None을
    반환한다 — 호출부에서 이 경우 값을 추정하지 않고 건너뛴다."""
    if prev_key is None:
        return None
    if abs(new_key - prev_key) < 1e-9:
        return "동결"
    return "인상" if new_key > prev_key else "인하"


def _find_balanced(text, open_idx):
    """text[open_idx]가 '[' 또는 '{'라고 할 때, 그에 대응하는 닫는 괄호의 인덱스를 찾는다.
    문자열 리터럴(쌍따옴표) 안의 괄호는 무시한다."""
    stack = []
    i = open_idx
    in_str = False
    while i < len(text):
        c = text[i]
        if in_str:
            if c == '"' and text[i - 1] != "\\":
                in_str = False
        elif c == '"':
            in_str = True
        elif c in "[{":
            stack.append(c)
        elif c in "]}":
            stack.pop()
            if not stack:
                return i
        i += 1
    raise ValueError("balanced bracket not found")


_ENTRY_RE = re.compile(r"\[[^\[\]]*\]")
_ELEM_RE = re.compile(r'"((?:[^"\\]|\\.)*)"|null')


def _parse_entry(entry_text):
    """entry_text 예: '["2026-01-15", null, null, "동결 2.50%"]' -> [ "2026-01-15", None, None, "동결 2.50%" ]"""
    inner = entry_text[1:-1]
    elems = []
    for m in _ELEM_RE.finditer(inner):
        elems.append(m.group(1) if m.group(0) != "null" else None)
    return elems


def _serialize_entry(elems):
    parts = []
    for e in elems:
        parts.append("null" if e is None else json.dumps(e, ensure_ascii=False))
    return "[" + ", ".join(parts) + "]"


def find_bank_blocks(text):
    """반환: [(bank_key, year, block_start, block_end)] — block_start/end는 'key: [' 의 '[' 부터
    대응하는 ']'까지(포함) 범위."""
    blocks = []
    for ym in re.finditer(r'"(\d{4})"\s*:\s*\{', text):
        year = ym.group(1)
        y_open = ym.end() - 1
        y_close = _find_balanced(text, y_open)
        year_body = text[y_open:y_close]
        base = y_open
        for bm in re.finditer(r'(bok|fomc|boj|ecb)\s*:\s*\[', year_body):
            key = bm.group(1)
            arr_open = base + bm.end() - 1
            arr_close = _find_balanced(text, arr_open)
            blocks.append((key, year, arr_open, arr_close))
    return blocks


def main():
    text = open(CBANK_PATH, encoding="utf-8").read()
    blocks = find_bank_blocks(text)

    # 은행별로 연도 순 전체 회의 리스트를 모아, 직전 확정 결과를 추적할 수 있게 한다.
    by_bank = {}
    for key, year, start, end in blocks:
        by_bank.setdefault(key, []).append((year, start, end))
    for key in by_bank:
        by_bank[key].sort(key=lambda t: t[0])  # 연도 오름차순

    replacements = []  # (start_offset, end_offset, new_text)
    any_update = False

    for bank_key, year_blocks in by_bank.items():
        fetcher = FETCHERS[bank_key]
        last_key = None  # 직전 확정 결과의 sort_key (연도 넘어가며 이어짐)
        for year, block_start, block_end in year_blocks:
            block_text = text[block_start:block_end + 1]
            for em in _ENTRY_RE.finditer(block_text):
                elems = _parse_entry(em.group(0))
                if not elems or elems[0] is None:
                    continue
                try:
                    start_date = datetime.date.fromisoformat(elems[0])
                except ValueError:
                    continue
                end_date = None
                if len(elems) > 1 and elems[1]:
                    try:
                        end_date = datetime.date.fromisoformat(elems[1])
                    except ValueError:
                        pass
                decision_date = end_date or start_date
                has_result = len(elems) >= 4 and elems[3]

                if has_result:
                    k = prev_sort_key(elems[3])
                    if k is not None:
                        last_key = k
                    continue

                if decision_date > TODAY:
                    continue  # 아직 회의 전 — 대상 아님

                if bank_key == "ecb" and (TODAY - decision_date).days < 10:
                    log(bank_key, decision_date, "skip", "발효일(약 6~8일 후) 대기 중 — 10일 경과 후 재시도")
                    continue

                fetched, reason = fetcher(decision_date)
                if fetched is None:
                    log(bank_key, decision_date, "skip", reason)
                    continue

                direction = classify(last_key, fetched["sort_key"])
                if direction is None:
                    log(bank_key, decision_date, "skip", "직전 확정 결과가 없어 방향 판단 불가 — 최초 1건은 수동 기입 필요")
                    continue
                result_label = f"{direction} {fetched['label']}"
                new_elems = [elems[0], elems[1] if len(elems) > 1 else None,
                             elems[2] if len(elems) > 2 else None, result_label]
                if bank_key == "ecb" and fetched.get("effective") and direction != "동결":
                    new_elems.append(fetched["effective"])

                abs_start = block_start + em.start()
                abs_end = block_start + em.end()
                replacements.append((abs_start, abs_end, _serialize_entry(new_elems)))
                last_key = fetched["sort_key"]
                any_update = True
                log(bank_key, decision_date, "update", result_label)

    if not any_update:
        log("*", TODAY, "no-op", "갱신 대상 없음")
        return 0

    replacements.sort(key=lambda t: t[0], reverse=True)
    for s, e, new_text in replacements:
        text = text[:s] + new_text + text[e:]

    text = re.sub(r'updated:\s*"\d{4}-\d{2}-\d{2}"', f'updated: "{TODAY.isoformat()}"', text, count=1)

    with open(CBANK_PATH, "w", encoding="utf-8") as f:
        f.write(text)
    print("wrote", CBANK_PATH)
    return 0


if __name__ == "__main__":
    sys.exit(main())
