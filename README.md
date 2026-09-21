# SR지수 · 경제지표 통합 포털

CPI가 놓치는 자산 인플레이션을 포함한 대체 생활비지수(SR지수)와, 미국·한국 주요 거시경제지표(물가·고용·경기심리·PCE확산·중앙은행 통화정책 일정)를
하나의 GitHub Pages 사이트(`docs/index.html`)에서 탭으로 전환하며 볼 수 있습니다.

- SR지수 산식·처리 규칙은 `methodology.md`에 고정 (변경 금지)
- 외부지표(물가·통화 / 고용 / 경기·심리 / PCE확산 / 통화정책일정) 카탈로그·수집 방법론은 `methodology_external.md` 참조

## 포털 구조 — 왜 탭마다 동작 방식이 다른가

이 포털은 **두 가지 다른 자동화 방식**을 한 화면에 얹은 것입니다. 탭을 누르면 바로 이해됩니다.

| 탭 | 데이터가 만들어지는 곳 | 갱신 주기 | 필요한 것 |
|---|---|---|---|
| **SR 생활비지수** | GitHub Actions가 서버에서 미리 계산 → `docs/data.js`에 커밋 | 자동, 매월 5일·15일 06:00 KST | Secrets 3종(FRED/KOSIS/ECOS), KB 엑셀 월 1회 수동 커밋 |
| **물가·통화 / 고용 / 경기·심리** | 방문자의 **브라우저**가 탭을 열 때마다 공개 API를 직접 호출 | 완전 자동(볼 때마다 최신) | 없음(선택: BEA·BLS API 키 — 브라우저에만 저장) |
| **PCE 확산 지표** | 정적 데이터 파일(`docs/pce_data.js`) | 수동 — Claude에게 "PCE 확산 지표 업데이트해줘" 요청 | 없음 |
| **통화정책 일정 — FOMC/BOK/ECB** | GitHub Actions가 공식 API(FRED/ECOS/ECB)에서 직접 확인 → `docs/cbank.js`에 커밋 | 자동, 6시간마다, 승인 없음 | Secrets 2종(FRED/ECOS, SR지수와 공용) |
| **통화정책 일정 — BOJ** | 정적 데이터 파일(`docs/cbank.js`) | 수동 — Claude에게 "통화정책 일정 업데이트해줘" 요청 | 없음 |

즉 SR지수만 "서버가 미리 만들어둔 값"이고, 물가·통화/고용/경기·심리는 "당신이 페이지를 여는 순간 당신의 브라우저가 직접" 가져옵니다.
통화정책 일정은 은행마다 다릅니다 — FOMC/BOK/ECB는 공식 숫자 API가 있어 자동, BOJ는 목표금리를 주는 공식 API가 없어 여전히 수동입니다
(자세한 이유·안전장치는 `methodology_external.md`의 "통화정책회의 데이터 규약" 참조).
사내망처럼 서버 환경의 외부망이 막혀 있어도, 방문자 브라우저의 외부망이 열려 있으면 물가·통화/고용/경기·심리 탭은 정상 동작합니다.

## 파일 구성

```
docs/                    ← GitHub Pages가 서빙하는 폴더 (Settings → Pages → main /docs)
  index.html             ← 포털 셸 + 탭 네비게이션 + SR지수 렌더 로직
  data.js                ← SR지수 데이터 (GitHub Actions가 자동 갱신)
  macro_client.js         ← 물가·통화/고용/경기·심리 — 수집 + SVG 차트 엔진
  macro_manual.json       ← ISM PMI 등 수동 보정 오버레이
  pce_data.js             ← PCE 확산 지표 데이터 (정적, 수동 갱신)
  cbank.js                ← 중앙은행 통화정책 일정·결과 (FOMC/BOK/ECB는 자동, BOJ는 수동)
scripts/build_data.py     ← SR지수 빌드 스크립트 (FRED+KOSIS+ECOS+KB엑셀)
scripts/update_cbank.py   ← 통화정책 일정 결과 자동 갱신 스크립트 (FOMC/BOK/ECB)
.github/workflows/update.yml        ← SR지수 자동 빌드 스케줄
.github/workflows/update-cbank.yml  ← 통화정책 일정 자동 갱신 스케줄
data/manual/               ← KB 아파트 매매가격지수 엑셀 (월 1회 수동 커밋)
methodology.md             ← SR지수 산식 명세 (변경 금지)
methodology_external.md    ← 외부지표 카탈로그·원천·함정 (핸드오프 문서 요약)
```

## 최초 세팅 (약 30분)

1. **repo 생성**: 이 폴더 전체를 새 GitHub repo(public)에 push
2. **API 키 3종 발급** (모두 무료, SR지수 서버 빌드 + 통화정책 일정 자동화 공용)
   - FRED: fred.stlouisfed.org → My Account → API Keys
   - KOSIS: kosis.kr 공유서비스 → Open API → 활용신청
   - ECOS: ecos.bok.or.kr → Open API → 인증키 신청
3. **Secrets 등록**: repo Settings → Secrets and variables → Actions →
   `FRED_API_KEY`, `KOSIS_API_KEY`, `ECOS_API_KEY`
4. **KOSIS/ECOS 코드 확정**: `scripts/build_data.py`의 `TODO` 4곳
   - KOSIS에서 '자가주거비포함 소비자물가지수', '소비자물가지수'(전국, 2020=100) 표를 열고
     Open API 발급 URL의 orgId/tblId/itmId/objL1 값을 그대로 기입
   - ECOS 통계목록에서 'M2 상품별 구성내역(평잔, 원계열)' stat/item 코드 기입
     (계열이 신·구로 나뉘면 build_data.py의 splice_growth로 2004-10 접합)
5. **KB 엑셀 1회 커밋**: `data/manual/README.md` 참조
6. **Pages 활성화**: Settings → Pages → Source: Deploy from a branch → main /docs
7. **테스트**: Actions 탭 → update-dashboard → Run workflow → 성공 후
   `https://<계정>.github.io/<repo>/` 접속, methodology.md의 검증 앵커와 대조

물가·통화/고용/경기·심리/PCE확산/통화정책(BOJ 제외) 탭은 위 세팅과 무관하게 Pages 활성화 즉시 동작합니다(4~6단계는 SR지수 탭 전용).
통화정책 일정의 FOMC/BOK/ECB 자동화는 2단계·3단계(FRED·ECOS 키·Secrets)만 있으면 됩니다.

## 운영 — 탭별로 다름

- **SR 생활비지수**: 자동(매월 5일·15일) + 월 1회 KB 엑셀 커밋 외 수작업 없음. `docs/data.js`는 세팅 완료 전까지 시드 데이터(2026-08까지 실측)로 동작.
- **물가·통화/고용/경기·심리**: 완전 자동 — 브라우저가 열릴 때마다 최신값을 직접 조회. 운영자가 할 일 없음(PMI 미러 결측이 심하면 `macro_manual.json`에 공식 발표치 추가 권장).
- **PCE 확산 지표**: 월 1회 정도, Claude에 "PCE 확산 지표 업데이트해줘" 요청 → `docs/pce_data.js`의 해당 기준 values 배열 끝에 새 월 값 추가.
- **통화정책 일정 — FOMC/BOK/ECB**: 완전 자동(6시간마다 확인, 승인 없이 커밋) — 운영자가 할 일 없음. 소스가 애매하면 스스로 건너뛰고 재시도하므로, 회의 직후 반영이 늦어질 수 있음(특히 ECB는 발효일 지연으로 결정일+10일 이후 확정).
- **통화정책 일정 — BOJ**: 각 회의 직후, Claude에 "통화정책 일정 업데이트해줘" 요청 → `docs/cbank.js`에 결과·발효일 기입.

## 자동화 방식 검토 (요약)

- **더 자동화할 수 있는 것 없음**: SR지수(서버 빌드), 물가·통화/고용/경기·심리(브라우저 실시간 조회), 통화정책 일정 중 FOMC/BOK/ECB(공식 API 자동 확인)는 이미 사람 개입 없이 갱신됩니다.
- **구조적으로 완전 자동화가 어려운 것**: PCE 확산 지표(BEA 2.4.4U는 API가 아니라 엑셀 배포, 매월 발표일도 비정기), BOJ 통화정책 결과(목표금리를 주는 공식 숫자 API가 없고 성명이 PDF로만, 예측 가능한 URL 규칙도 없음 — `scripts/update_cbank.py`에 PDF URL을 직접 넘기면 파싱을 시도하는 보조 경로는 있음).
- **선택적으로 자동화를 더할 수 있는 지점**: BLS 무료 한도(무키 시 IP당 일 25회)를 팀 전체가 공유하는 구조라, 하루 1회 GitHub Actions가 BLS 5개 시리즈를 받아 `docs/macro_cache.json`으로 게시하면 브라우저는 이 파일을 우선 사용해 API 호출이 일 5회로 고정됩니다(현재 미구현 — 필요 시 `update.yml`에 잡 추가 가능). 사용자 수가 적다면 없어도 무방합니다.
