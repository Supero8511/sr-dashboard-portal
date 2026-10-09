# 외부 경제지표 — 카탈로그·원천·함정 (물가·통화 / 고용 / 경기·심리 / PCE확산 / 통화정책일정 / 지표금리 / SEP점도표 / 재정적자 / 자동차대출연체율 탭)

> SR지수(`methodology.md`)와 별개로, 이 문서는 `docs/macro_client.js` · `docs/pce_data.js` · `docs/cbank.js` ·
> `docs/rates.js` · `docs/dots.js` · `docs/fiscal.js` · `docs/delinq.js`가 다루는 외부 공개지표의 원천·수집
> 방식·알려진 함정을 정리한다. 전부 외부 공개 데이터이며, 사내 시스템 데이터는 어떤 형태로도 이 저장소에
> 포함하지 않는다(아래 "포함 금지" 참조).

## 포함 금지 (반드시 지킬 것)

이 repo(특히 `docs/`)에는 다음을 절대 추가하지 않는다:
- 내부 자금·마감·대사·위원회 등 사내 시스템에서 조회되는 데이터와 그 조회 코드/엔드포인트
- 사내 저장소·BI·DB 관련 주소, 토큰, 인증 정보 일체
- 내부 문서(칼럼·보고서) 아카이브와 그 편집기 코드

## 아키텍처 원칙

- **브라우저 직접 수집**: `docs/macro_client.js`는 페이지가 열릴 때마다 방문자의 브라우저에서 공개 API를 직접 호출한다(서버 빌드 없음). 아래 원천은 전부 CORS 허용이 확인된 것들이다.
- **원천별 독립 실패 처리**: `Promise.allSettled`로 병렬 조회하며, 실패한 지표만 실패 사유 칩으로 표시한다 — 한 원천이 죽어도 나머지는 정상 표시된다.
- **수동 보정 오버레이**(`docs/macro_manual.json`): 모든 시리즈 키에 범용 적용 — 같은 월은 수동값이 우선, 없는 월은 추가. 무료 미러의 결측·오염 보정용.
- **정적 데이터 파일**: PCE 확산 지표(`docs/pce_data.js`)는 정적 JS 파일 — 갱신은 사람(또는 Claude)이 수행한다.
  통화정책 일정(`docs/cbank.js`)도 정적 JS 파일이지만, 2026-09-21부터 FOMC/BOK/ECB 항목은 GitHub Actions가
  자동으로 채운다(아래 "통화정책회의 데이터 규약" 참조) — BOJ만 여전히 사람(또는 Claude) 수동.
  2026-10-09부터 추가된 지표금리 현황(`docs/rates.js`)·SEP 점도표(`docs/dots.js`)·재정적자(`docs/fiscal.js`)·
  자동차대출 연체율(`docs/delinq.js`)도 같은 패턴 — GitHub Actions(`update-extra.yml`, 매일)가 API 키가
  필요한 원천을 서버에서 조회해 정적 JS 파일에 커밋하고, 화면은 cbank.js와 동일하게 탭을 열 때마다
  캐시 없이 다시 받아 그 파일을 그대로 그린다(아래 "보조 지표 자동화" 참조).
- 외부 라이브러리 0 — 차트는 인라인 SVG로 직접 생성한다(hover 툴팁, 기간 버튼, 기준선, 결측월 선 분절, ResizeObserver 반응형).

## 지표 카탈로그

| 지표 | 원천 (전부 무료) | 시리즈/테이블 | 키 | 최신성·함정 |
|---|---|---|---|---|
| CPI 헤드라인·코어 YoY | BLS API v2 | `CUSR0000SA0` / `CUSR0000SA0L1E` | 선택 | 최신. 무키 시 IP당 일 25회 |
| NFP 증감(천명) | BLS API v2 | `CES0000000001` | 선택 | 〃 |
| 실업률 / 참가율 | BLS API v2 | `LNS14000000` / `LNS11300000` | 선택 | 〃 |
| PCE 헤드라인·코어 YoY | BEA API (NIPA) | `T20804` M — Line 1 / "excluding food and energy" & 非market | **BEA 키** | 최신. 연준 목표 2% 기준선 |
| 미국 실질 GDP (전기비 연율) | BEA API (NIPA) | `T10101` Q Line 1 | **BEA 키** | 최신 |
| 장단기 금리차 10y-2y·10y-3m | 미 재무부 일별 수익률곡선 CSV | 연도별 CSV 3개년 병합 | 불필요 | 최신(일별). 0 = 역전 경계 |
| ISM 제조업 PMI | DBnomics 미러 `ISM/pmi/pm` | + `macro_manual.json` 수동 보정 | 불필요 | 미러 지연·말단 오염 — 20~90 범위 필터 필수 |
| 한국 실질 GDP (전기비) | DBnomics `IMF/IFS/Q.KR.NGDP_R_SA_XDC` | 수준→전기비 계산 | 불필요 | 미러 수분기 지연 |
| 광공업생산 YoY | DBnomics `FED/G17_IP_MAJOR_INDUSTRY_GROUPS/IP.B50001.S` | 지수→YoY | 불필요 | 대체로 최신 |
| 미시간 소비자심리 | DBnomics `SCSMICH/MICS/ICS` | 레벨 | 불필요 | 미러 약 1년 지연 |
| PCE 확산 지표 | BEA Underlying Detail `2.4.4U` 엑셀 | 말단 품목 217개 자체 계산 | (수동) | 아래 "PCE 확산 지표 방법론" |
| 통화정책 일정·결과(FOMC/BOK/ECB) | FRED / ECOS / ECB Data Portal 공식 API(자동) | `docs/cbank.js` 정적 데이터, `scripts/update_cbank.py`가 갱신 | FRED·ECOS 키 | 아래 "통화정책회의 데이터 규약" |
| 통화정책 일정·결과(BOJ) | 공식 발표(성명 PDF) + 보도 교차검증(수동) | `docs/cbank.js` 정적 데이터 | 불필요 | 아래 "통화정책회의 데이터 규약" |
| 지표금리 현황(미국 SOFR75−IORB) | FRED API(자동) | `docs/rates.js`, `scripts/update_rates.py` | FRED 키 | 아래 "보조 지표 자동화" |
| 지표금리 현황(한국 콜금리−기준금리) | 한국은행 ECOS API(자동) | `docs/rates.js`, `scripts/update_rates.py` | ECOS 키 | KOFR 대체 — 아래 "보조 지표 자동화" |
| FOMC 점도표(SEP, 중앙값 전망경로) | 연준 공식 발표표(HTML, 자동) | `docs/dots.js`, `scripts/update_dots.py` | 불필요 | 아래 "보조 지표 자동화" |
| 재정적자(기초/총/순이자, %GDP) | FRED FYFSD·FYOINT·FYGDP(OMB 공식, 자동) | `docs/fiscal.js`, `scripts/update_fiscal.py` | FRED 키 | 아래 "보조 지표 자동화" |
| 자동차대출 90+일 연체 전환율 | NY연은 분기 데이터 파일(자동, 검증 앵커 필수) | `docs/delinq.js`, `scripts/update_delinq.py` | 불필요 | 아래 "보조 지표 자동화" |

**수집 불가로 확정된 것** (재시도 낭비 방지): 신규 실업급여 신청건수(DOL 원천 차단), 내구재 수주·주택허가(Census 차단), 컨퍼런스보드 소비자신뢰(유료), 발틱운임지수 BDI(유료), FRED·ismworld(CORS 차단), 한국은행 ECOS·KOSIS(CORS 차단 → 한국 GDP는 IMF 미러 사용). KOFR(한국무위험지표금리)은 ECOS에 등록돼 있지 않고 민간 산출기관이 배포해 공식 숫자 API가 없음 — 지표금리 현황 탭에서는 같은 취지의 ECOS 콜금리−기준금리 스프레드로 대체(아래 "보조 지표 자동화" 참조).

## 원천별 함정 상세

**BLS** — 무키 시 IP당 일 25회 제한. 다수가 같은 네트워크(예: 회사·공용 와이파이)에서 접속하면 한도를 나눠 쓰게 되어 소진되기 쉽다. 한도는 미 동부 자정(≈KST 13~14시) 리셋. 폴백: ① 공유 스냅샷(`docs/macro_cache.json`, 미구현·선택) → ② localStorage 3시간 캐시 → ③ API 직접 호출, 실패 시 만료 캐시라도 사용. 한도 초과 시 화면에 🔑 BLS 키 등록 바 표시(무료 키 = 일 500회).

**BEA** — PCE 코어는 LineNumber가 아니라 LineDescription으로 매칭(`/excluding food and energy/i` 이면서 `/market/i` 아닌 행). DataValue는 콤마 포함 문자열 → 파싱 시 콤마 제거 필요.

**DBnomics 미러** — ISM PMI는 갱신 지연 + 말단값 오염(비정상값 실측) → 20~90 범위 필터 필수 + `macro_manual.json`으로 공식 발표치 수동 보정. 미시간심리 ~1년, 한국 GDP 수분기 지연 — 화면에 지연 사실 명시.

**PCE 확산 지표 계산 방법론** — BEA NIPA Underlying Detail Table 2.4.4U(품목별 가격지수, 월간) 엑셀에서 말단(최하위) 품목 217개 추출. 12개월=전년동월비, 6/3개월=해당 기간 상승률 연율화 후 각 기준 3% 초과 품목 수 비중(%). 갱신: 새 2.4.4U를 받아 재계산하거나 `docs/pce_data.js`의 해당 기준 values 배열 끝에 새 월 값 추가 + updated/log 갱신.

**미 재무부 수익률곡선 CSV** — 헤더에서 `Date`/`3 Mo`/`2 Yr`/`10 Yr` 열 인덱스를 찾아 10y-2y, 10y-3m 스프레드 계산. 날짜 형식 `MM/DD/YYYY`.

## 통화정책회의 데이터 규약 (`docs/cbank.js`)

- 회의 항목 형식: `[시작일, 종료일|null(1일 회의), 비고|null, 결과, 발효일]`
  - 결정 발표일 = 마지막 날. 비고: FOMC `"SEP"`(경제전망), BOJ `"전망보고서"`.
  - **결과**(4번째) = `"동결/인상/인하 <결정 후 금리>"` — 회의 후 확정치. ECB는 수신금리(DFR), FOMC는 목표범위.
  - **발효일**(5번째, value date) = ECB만, 금리 변경 회의에 `"YYYY-MM-DD"`(보도자료 "with effect from" 날짜).

### 갱신 운영 — 2026-09-21부터 은행별로 다름 (저장소 소유자 명시적 요청으로 변경)

> 원래 이 절은 "결과는 자동 수집이 아니다 — 조기 확정·오보 리스크가 커서 의도적으로 사람 검증 게이트를 둔
> 설계"였다. 2026-09-21, 저장소 소유자가 승인 게이트 없는 완전 자동화를 명시적으로 요청해 아래와 같이
> 바뀌었다(`MASTER_PROMPT.md`의 "이 규약도 변경 금지" 조항을 이 항목에 한해 소유자 본인이 해제함 — 다른
> 규약·산식은 여전히 변경 금지).

- **FOMC · BOK · ECB — 완전 자동, 승인 없음.** `.github/workflows/update-cbank.yml`이 6시간마다
  `scripts/update_cbank.py`를 실행해 각 은행 결과를 공식 소스에서 직접 확인하고, 사람 검토 없이 바로
  `docs/cbank.js`에 커밋한다.
  - FOMC: FRED `DFEDTARU`/`DFEDTARL`(목표범위 상·하단, 일별)
  - BOK: ECOS `722Y001`/`0101000`(기준금리, 일별)
  - ECB: ECB Data Portal API `FM.D.U2.EUR.4F.KR.DFR.LEV`(수신금리 DFR, 일별) — 값이 바뀐 날짜를 발효일로 사용.
    결정일과 발효일 사이 통상 6~8일 지연이 있어, **결정일로부터 10일 이내에는 조회하지 않음**(그 전에
    조회하면 아직 발효 전인 옛 금리를 '동결'로 오판할 위험).
  - **조기 확정·오보 위험에 대한 대응**: 사람 게이트 대신, 스크립트 자체가 "확신 없으면 절대 추정하지
    않고 건너뛴다"를 원칙으로 한다 — 소스 조회 실패, 상·하단 날짜 불일치, 직전 결과 미존재(방향 판단
    불가) 등 애매한 모든 경우 해당 항목만 skip하고 다음 실행에서 재시도한다. 다른 은행·회의에는 영향
    없음. 실행 로그(GitHub Actions 로그)가 매 실행의 판단 근거를 남겨 사후 감사가 가능하다.
- **BOJ — 여전히 수동.** 목표금리를 직접 주는 공식 숫자 API/피드가 없고(성명이 매번 다른 URL의 PDF로만
  발표되며 안정적으로 예측 가능한 URL 규칙이 확인되지 않음) 자동 판정의 신뢰도를 담보할 수 없어, 기존
  방식을 유지한다: 각 회의 후 Claude에 "통화정책 일정 업데이트해줘" 요청 → 공식 발표·보도 교차검증 후
  4·5번째 요소 기입. (`scripts/update_cbank.py`에 `BOJ_STATEMENT_URL` 환경변수로 성명 PDF URL을 직접
  넘기면 그 안의 문구를 파싱해 자동 채택하는 보조 경로가 있지만, URL을 스스로 찾지는 않는다.)

## 보조 지표 자동화 (`docs/rates.js` · `dots.js` · `fiscal.js` · `delinq.js`, 2026-10-09 추가)

> 저장소 소유자가 다른 프로젝트(사내 Treasury 외화 포털)의 핸드오프 문서를 참고해 "없는 자료만 골라와서
> 같은 형태로" 추가해 달라고 요청했다. 자동화 수준은 "가능한 만큼 완전자동, 애매하면 skip-and-retry"로
> 명시적으로 합의됨 — cbank.js와 같은 원칙(사람 승인 게이트 없음 + 애매하면 절대 추정하지 않고 skip)을
> 그대로 적용한다. `.github/workflows/update-extra.yml`이 매일 1회 네 스크립트를 모두 실행하고 변경분만
> 한 커밋으로 올린다.

- **지표금리 현황**(`scripts/update_rates.py`) — 미국 SOFR(75분위)−IORB, 한국 콜금리(1일물)−기준금리,
  각각 bp 스프레드 + 20거래일 이동평균. 전부 FRED(`SOFR75`,`IORB`)·ECOS(`817Y002`/`010101000`,
  `722Y001`/`0101000`) 공식 시리즈만 사용. **원 핸드오프의 'KOFR−콜금리'는 그대로 가져오지 않았다** —
  KOFR은 ECOS StatisticWord 검색 결과가 없어(민간 산출기관 배포, 공식 숫자 API 미확인) 완전자동화
  대상에서 제외하고, 같은 취지(시장 자금조달 압력)를 ECOS만으로 완전자동화할 수 있는
  '콜금리−기준금리' 스프레드로 대체했다. 어느 쪽이든 원천 조회가 실패하면 그 다리(미국 또는 한국)만
  건너뛰고 기존 값을 유지한다.
- **FOMC 점도표(SEP)**(`scripts/update_dots.py`) — `docs/cbank.js`의 FOMC 항목 중 비고가 `"SEP"`인
  회의의 결정일(YYYYMMDD)로 `https://www.federalreserve.gov/monetarypolicy/fomcprojtabl{날짜}.htm`
  (연준 공식 발표표, HTML)을 열어 "Federal funds rate" 중앙값(median) 행을 가져온다. "Memo: Projected
  appropriate policy path" 구간에서 그 라벨과 정확히 일치하는 행이 1개가 아니면(0개나, "~월
  projection" 비교행과 혼동되어 2개 이상 잡히면) 그 회의는 skip. cbank.js가 이미 FOMC·SEP 일정의
  단일 소스이므로 이 스크립트가 날짜를 별도로 추정하지 않는다.
- **재정적자**(`scripts/update_fiscal.py`) — FRED `FYFSD`(연방 재정수지)·`FYOINT`(이자지출)·
  `FYGDP`(GDP), 전부 OMB 공식·연간(회계연도). 기초재정수지(이자 제외) = 총수지 + 이자지출.
  **검증 앵커**: FY2025 공식 확정치 기준 총수지 ≈ -5.86%, 순이자 ≈ 3.20%, 기초수지 ≈ -2.65%(GDP 대비,
  허용오차 ±0.5%p) — 계산값이 앵커와 어긋나면(집계 방식이 깨졌을 가능성) 전체를 쓰지 않고 skip.
- **자동차대출 90+일 연체 전환율**(`scripts/update_delinq.py`) — NY연은 Household Debt and Credit
  Report가 분기마다 공개하는 인터랙티브용 데이터 파일
  (`https://www.newyorkfed.org/medialibrary/interactives/householdcredit/data/xls/HHD_C_Report_{YYYY}Q{Q}`,
  매 릴리스에 1999~최신 전체 히스토리 포함)에서 "Auto Loan" 레이블 행을 찾는다. 이 파일의 시트·행
  레이아웃은 공식 문서로 고정돼 있지 않아 레이블만으로는 다른 지표(잔액 등) 행과 혼동될 수 있다 — 그래서
  레이블로 찾은 모든 후보 행 중, 공식 보도자료로 확인한 **검증 앵커 두 분기(2025Q2=2.93%,
  2026Q2=3.00%)와 정확히 일치하는 단 하나의 후보**만 채택한다. 일치하는 후보가 0개나 2개 이상이면
  전부 skip — 추정 금지.

---
*출처: 2026-09-18 핸드오프 문서 기반, 2026-09-21 통화정책 자동화 반영, 2026-10-09 보조 지표(지표금리·SEP점도표·재정적자·자동차대출연체율) 추가.
사내 시스템 접근·인증 정보는 일체 포함하지 않는다.*
