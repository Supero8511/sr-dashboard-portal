/* ══════════════════════════════════════════════════════════════════
   미국 주요 경제지표 — 브라우저 직접 수집
   사내 서버 환경은 외부망 전면 차단('26-09-08 실측)이라 서버 사이드 수집 불가.
   사용자 브라우저는 외부망이 열려 있으므로 페이지가 직접 공개 API를 호출한다.

   원천 (모두 CORS 허용 실측 완료):
     CPI·고용(NFP/실업률/참여율) : BLS API v2 (키 불필요·최신, 무키 시 IP당 일 25회 제한
                                   → 공유 스냅샷 macro_cache.json 우선 + 3시간 캐시 + 선택적 BLS 키)
     PCE·미국 GDP               : BEA API (무료 키, localStorage "bea_api_key")
     광공업생산(IP)              : FED G.17 (DBnomics 미러 — 최신 확인)
     ISM 제조업 PMI              : ISM (DBnomics 미러 — 지연·말단값 오염 실측 → 범위 필터
                                   + macro_manual.json의 공식 발표치로 수동 보정)
     미시간 소비자심리           : SCSMICH (DBnomics 미러 — 약 1년 지연)
     한국 GDP                    : IMF IFS (DBnomics 미러 — 수분기 지연)
     국채 수익률곡선(금리차)     : 미 재무부 일별 CSV (최신)
   수집 불가(원천 차단/유료): 신규 실업급여 신청건수(DOL), 내구재 수주·주택허가(Census),
     컨퍼런스보드 소비자신뢰(유료), 발틱운임지수(유료). 이 파일 상단 주석에 사유 기록.
   ══════════════════════════════════════════════════════════════════ */

function yoyFromLevels(obs) { // [["YYYY-MM", 지수]] 오름차순 → [["YYYY-MM", YoY%]]
  const pos = {};
  obs.forEach(o => { pos[o[0]] = o[1]; });
  const out = [];
  for (const [p, v] of obs) {
    const base = pos[(+p.slice(0, 4) - 1) + "-" + p.slice(5, 7)];
    if (base && !isNaN(v) && !isNaN(base)) out.push([p, Math.round((v / base - 1) * 10000) / 100]);
  }
  return out;
}
function qToMonth(p) { // "2026Q2" / "2026-Q2" → "2026-06"
  const m = /^(\d{4})-?Q([1-4])$/.exec(String(p));
  return m ? m[1] + "-" + ("0" + m[2] * 3).slice(-2) : String(p);
}

/* ── DBnomics 공용 ── */
async function fetchDbnSeries(path, label) {
  const r = await fetch("https://api.db.nomics.world/v22/series/" + path + "?observations=1", { cache: "no-store" });
  if (!r.ok) throw new Error(label + " HTTP " + r.status);
  const d = (((await r.json()).series || {}).docs || [])[0] || {};
  const obs = [];
  (d.period || []).forEach((p, i) => {
    const v = (d.value || [])[i];
    if (v == null || v === "NA" || isNaN(v)) return;
    obs.push([qToMonth(String(p).slice(0, 10)).slice(0, 7), Math.round(v * 100) / 100]);
  });
  if (!obs.length) throw new Error(label + " 관측치 없음");
  return obs;
}

async function fetchPmiClient() {
  // 미러 파서 오류로 말단에 10~11 같은 비정상값이 붙는 사례('25-09~12 실측) → PMI 정상범위 밖 값 제거
  const obs = (await fetchDbnSeries("ISM/pmi/pm", "PMI")).filter(o => o[1] >= 20 && o[1] <= 90);
  if (!obs.length) throw new Error("PMI 유효 관측치 없음");
  return { ism_pmi: { obs: obs.slice(-120) } };
}
async function fetchIpClient() { // 광공업생산 총지수 → YoY
  const lv = await fetchDbnSeries("FED/G17_IP_MAJOR_INDUSTRY_GROUPS/IP.B50001.S", "IP");
  const yy = yoyFromLevels(lv);
  if (!yy.length) throw new Error("IP YoY 계산 실패");
  return { ip_yoy: { obs: yy.slice(-120) } };
}
async function fetchMichClient() { // 미시간 소비자심리 (지수 레벨)
  const obs = await fetchDbnSeries("SCSMICH/MICS/ICS", "미시간심리");
  return { mich_ics: { obs: obs.slice(-120) } };
}
async function fetchGdpKrClient() { // 한국 실질GDP (IMF IFS, 계절조정 수준) → 전기비 %
  const lv = await fetchDbnSeries("IMF/IFS/Q.KR.NGDP_R_SA_XDC", "한국GDP");
  const out = [];
  for (let i = 1; i < lv.length; i++) {
    out.push([lv[i][0], Math.round((lv[i][1] / lv[i - 1][1] - 1) * 10000) / 100]);
  }
  if (!out.length) throw new Error("한국GDP 계산 실패");
  return { kr_gdp: { obs: out.slice(-40) } };
}

/* ── 공유 스냅샷 (docs/macro_cache.json — 스케줄 작업이 매일 1회 BLS를 받아 게시(선택 구현))
   포털은 API 대신 이 파일을 먼저 읽는다: 같은 도메인이라 한도 없음, 팀 전체 BLS 호출이 일 5회로 고정 ── */
let _macroCacheP = null;
function loadMacroCache() {
  if (!_macroCacheP) {
    _macroCacheP = fetch("macro_cache.json", { cache: "no-store" })
      .then(r => (r.ok ? r.json() : null)).catch(() => null);
  }
  return _macroCacheP;
}

/* ── BLS (스냅샷 우선 → 3시간 localStorage 캐시 → API. 무키 시 IP당 일 25회 제한, 사내망은 IP 공유라 팀 전체가 한도 공유) ── */
function getBlsKey(force) {
  let k = localStorage.getItem("bls_api_key") || "";
  if (force) {
    k = window.prompt("BLS API 키를 입력하세요.\n무료 발급: https://data.bls.gov/registrationEngine/ (이메일 등록 → 메일로 수신, 일 500회)\n이 브라우저에만 저장됩니다.", k || "") || "";
    if (k) localStorage.setItem("bls_api_key", k.trim());
  }
  return (k || "").trim();
}
async function fetchBlsSeries(id) {
  // 1순위: 공유 스냅샷 (48시간 이내면 API를 아예 부르지 않음)
  try {
    const snap = await loadMacroCache();
    const s = snap && snap.bls && snap.bls[id];
    if (s && s.obs && s.obs.length && snap.updated &&
        Date.now() - new Date(snap.updated).getTime() < 172800000) return s.obs;
  } catch (e) {}
  const CK = "blsc_" + id;
  let cached = null;
  try { cached = JSON.parse(localStorage.getItem(CK) || "null"); } catch (e) {}
  if (cached && Date.now() - cached.t < 10800000 && cached.obs && cached.obs.length) return cached.obs;
  try {
    const y = new Date().getFullYear();
    const bkey = getBlsKey(false);
    const r = await fetch("https://api.bls.gov/publicAPI/v2/timeseries/data/" + id +
      "?startyear=" + (y - 7) + "&endyear=" + y +
      (bkey ? "&registrationkey=" + encodeURIComponent(bkey) : ""), { cache: "no-store" });
    if (!r.ok) throw new Error("BLS HTTP " + r.status);
    const j = await r.json();
    if (j.status !== "REQUEST_SUCCEEDED") {
      const m = String((j.message || [])[0] || j.status);
      throw new Error(/threshold/i.test(m) ? "BLS_QUOTA" : "BLS: " + m);
    }
    const rows = ((((j.Results || {}).series) || [])[0] || {}).data || [];
    const obs = [];
    rows.forEach(d => {
      // 값 "-" 등 비수치(예: '25.10 정부 셧다운 미발표월)는 제외
      const v = parseFloat(d.value);
      if (/^M(0[1-9]|1[0-2])$/.test(d.period) && !isNaN(v)) obs.push([d.year + "-" + d.period.slice(1), v]);
    });
    obs.sort((a, b) => (a[0] < b[0] ? -1 : 1));
    if (!obs.length) throw new Error("BLS " + id + " 관측치 없음");
    try { localStorage.setItem(CK, JSON.stringify({ t: Date.now(), obs })); } catch (e) {}
    return obs;
  } catch (e) {
    // 한도 초과 등 실패 시: 만료된 캐시라도 있으면 그걸로 표시 (신선도보다 가용성 우선)
    if (cached && cached.obs && cached.obs.length) return cached.obs;
    throw e;
  }
}
async function fetchCpiClient() {
  const [h, c] = await Promise.all([fetchBlsSeries("CUSR0000SA0"), fetchBlsSeries("CUSR0000SA0L1E")]);
  const hy = yoyFromLevels(h), cy = yoyFromLevels(c);
  if (!hy.length) throw new Error("CPI YoY 계산 실패");
  return { cpi_yoy: { obs: hy }, cpi_core_yoy: { obs: cy } };
}
async function fetchJobsClient() { // 비농업고용 증감(천명) + 실업률 + 경제활동참가율
  const [nfp, unemp, part] = await Promise.all([
    fetchBlsSeries("CES0000000001"), fetchBlsSeries("LNS14000000"), fetchBlsSeries("LNS11300000")]);
  const chg = [];
  for (let i = 1; i < nfp.length; i++) chg.push([nfp[i][0], Math.round(nfp[i][1] - nfp[i - 1][1])]);
  if (!chg.length) throw new Error("NFP 계산 실패");
  return { nfp_chg: { obs: chg }, unemp_rate: { obs: unemp }, part_rate: { obs: part } };
}

/* ── BEA (PCE 물가 + 미국 GDP — 공용 키) ── */
function getBeaKey(force) {
  let k = localStorage.getItem("bea_api_key") || "";
  if (force) {
    k = window.prompt("BEA API 키를 입력하세요.\n무료 발급: https://apps.bea.gov/api/signup/ (이메일 등록 즉시 메일로 수신)\n이 브라우저에만 저장됩니다.", k || "") || "";
    if (k) localStorage.setItem("bea_api_key", k.trim());
  }
  return (k || "").trim();
}
async function fetchBeaTable(table, freq, yearsBack) {
  const key = getBeaKey(false);
  if (!key) throw new Error("NEED_KEY");
  const y = new Date().getFullYear();
  const years = [];
  for (let i = y - yearsBack; i <= y; i++) years.push(i);
  const r = await fetch("https://apps.bea.gov/api/data?UserID=" + encodeURIComponent(key) +
    "&method=GetData&datasetname=NIPA&TableName=" + table + "&Frequency=" + freq +
    "&Year=" + years.join(",") + "&ResultFormat=JSON", { cache: "no-store" });
  if (!r.ok) throw new Error("BEA HTTP " + r.status);
  const res = ((await r.json()).BEAAPI || {}).Results || {};
  if (res.Error) throw new Error("BEA: " + (res.Error.APIErrorDescription || res.Error["@APIErrorDescription"] || "오류 (키 확인)"));
  const data = res.Data || [];
  if (!data.length) throw new Error("BEA: 데이터 없음");
  return data;
}
async function fetchPceClient() {
  const data = await fetchBeaTable("T20804", "M", 7);
  const h = {}, c = {};
  data.forEach(d => {
    const mm = /^(\d{4})M(\d{2})$/.exec(String(d.TimePeriod || ""));
    if (!mm) return;
    const p = mm[1] + "-" + mm[2];
    const v = parseFloat(String(d.DataValue).replace(/,/g, ""));
    if (isNaN(v)) return;
    const desc = String(d.LineDescription || "");
    if (String(d.LineNumber) === "1") h[p] = v;
    else if (/excluding food and energy/i.test(desc) && !/market/i.test(desc)) c[p] = v;
  });
  const toObs = o => Object.keys(o).sort().map(p => [p, o[p]]);
  const hy = yoyFromLevels(toObs(h)), cy = yoyFromLevels(toObs(c));
  if (!hy.length) throw new Error("BEA: PCE 지수 파싱 실패");
  return { pce_yoy: { obs: hy }, pce_core_yoy: { obs: cy } };
}
async function fetchGdpUsClient() { // 실질GDP 성장률 (전기비 연율, T10101 Line 1)
  const data = await fetchBeaTable("T10101", "Q", 10);
  const o = {};
  data.forEach(d => {
    if (String(d.LineNumber) !== "1") return;
    const mm = /^(\d{4})Q([1-4])$/.exec(String(d.TimePeriod || ""));
    if (!mm) return;
    const v = parseFloat(String(d.DataValue).replace(/,/g, ""));
    if (!isNaN(v)) o[qToMonth(mm[1] + "Q" + mm[2])] = v;
  });
  const obs = Object.keys(o).sort().map(p => [p, o[p]]);
  if (!obs.length) throw new Error("BEA: GDP 파싱 실패");
  return { us_gdp: { obs: obs.slice(-40) } };
}

/* ── 미 재무부 일별 수익률곡선 → 장단기 금리차 ── */
async function fetchSpreadsClient() {
  const y = new Date().getFullYear();
  const years = [y - 2, y - 1, y];
  const texts = await Promise.all(years.map(yy =>
    fetch("https://home.treasury.gov/resource-center/data-chart-center/interest-rates/daily-treasury-rates.csv/" +
      yy + "/all?type=daily_treasury_yield_curve&field_tdr_date_value=" + yy + "&page&_format=csv",
      { cache: "no-store" }).then(r => (r.ok ? r.text() : "")).catch(() => "")));
  const rows = {};
  texts.forEach(txt => {
    if (!txt) return;
    const lines = txt.trim().split(/\r?\n/);
    const hdr = lines[0].split(",").map(h => h.replace(/"/g, "").trim());
    const iD = hdr.indexOf("Date"), i3m = hdr.indexOf("3 Mo"), i2 = hdr.indexOf("2 Yr"), i10 = hdr.indexOf("10 Yr");
    if (iD < 0 || i2 < 0 || i10 < 0) return;
    for (let li = 1; li < lines.length; li++) {
      const c = lines[li].split(",");
      const dm = /^(\d{2})\/(\d{2})\/(\d{4})$/.exec((c[iD] || "").trim());
      if (!dm) continue;
      const f = x => { const v = parseFloat(c[x]); return isNaN(v) ? null : v; };
      rows[dm[3] + "-" + dm[1] + "-" + dm[2]] = { m3: i3m >= 0 ? f(i3m) : null, y2: f(i2), y10: f(i10) };
    }
  });
  const ps = Object.keys(rows).sort();
  const s210 = [], s310 = [];
  ps.forEach(p => {
    const r = rows[p];
    if (r.y10 != null && r.y2 != null) s210.push([p, Math.round((r.y10 - r.y2) * 100) / 100]);
    if (r.y10 != null && r.m3 != null) s310.push([p, Math.round((r.y10 - r.m3) * 100) / 100]);
  });
  if (!s210.length) throw new Error("수익률곡선 파싱 실패");
  return { spread_2s10s: { obs: s210 }, spread_3m10s: { obs: s310 } };
}

/* ── 화면 구성 — 7번 메뉴 하위 3분류: prices(1 물가·통화) / labor(2 고용) / activity(3 경기·심리) ── */
const MACRO_GROUPS = {
  prices: {
    jobs: [["CPI(BLS)", fetchCpiClient], ["PCE(BEA)", fetchPceClient], ["금리차(재무부)", fetchSpreadsClient]],
    hint: "그래프 위에 마우스를 올리면 각 시점의 수치를 확인할 수 있습니다. 상단 🔄 새로고침으로 언제든 재조회됩니다.<br>" +
      "이 메뉴는 열 때마다 브라우저가 공개 API(BLS·BEA·미 재무부)에서 직접 데이터를 받아옵니다.<br>" +
      "PCE는 BEA 무료 API 키가 필요합니다 — 미등록 시 상단 🔑 버튼으로 등록하세요."
  },
  labor: {
    jobs: [["고용(BLS)", fetchJobsClient]],
    hint: "그래프 위에 마우스를 올리면 각 시점의 수치를 확인할 수 있습니다. 상단 🔄 새로고침으로 언제든 재조회됩니다.<br>" +
      "이 메뉴는 열 때마다 브라우저가 미 노동통계국(BLS) API에서 직접 데이터를 받아옵니다.<br>" +
      "신규 실업급여 신청건수(미 노동부 DOL)는 사내망에서 원천이 차단되어 제공할 수 없습니다."
  },
  activity: {
    jobs: [["PMI(DBnomics)", fetchPmiClient], ["미국GDP(BEA)", fetchGdpUsClient], ["한국GDP(IMF)", fetchGdpKrClient],
           ["광공업(FED)", fetchIpClient], ["미시간심리", fetchMichClient]],
    hint: "그래프 위에 마우스를 올리면 각 시점의 수치를 확인할 수 있습니다. 상단 🔄 새로고침으로 언제든 재조회됩니다.<br>" +
      "이 메뉴는 열 때마다 브라우저가 공개 API(BEA·DBnomics 미러)에서 직접 데이터를 받아옵니다.<br>" +
      "내구재 수주·신규 주택허가(센서스국)는 사내망에서 원천이 차단되고,<br>" +
      "컨퍼런스보드 소비자신뢰지수·발틱운임지수(BDI)는 유료 데이터라 자동 수집이 불가합니다."
  }
};

function buildMacroPane(pane, menu) {
  const wrap = el("div", "wrap");
  pane.appendChild(wrap);
  renderMacroInto(wrap, (menu && menu.group) || "prices");
}

async function renderMacroInto(wrap, groupKey) {
  const G = MACRO_GROUPS[groupKey] || MACRO_GROUPS.prices;
  _macroCacheP = null;  // 🔄 새로고침 시 스냅샷도 다시 읽기
  wrap.innerHTML = "";
  wrap.appendChild(el("div", "updated-chip", "지표를 브라우저에서 직접 조회하는 중…"));

  const results = await Promise.allSettled(G.jobs.map(j => j[1]()));
  const S = {}, fails = [];
  let needKey = false, blsQuota = false;
  results.forEach((res, i) => {
    if (res.status === "fulfilled") Object.assign(S, res.value);
    else if (String(res.reason && res.reason.message) === "NEED_KEY") needKey = true;
    else if (String(res.reason && res.reason.message) === "BLS_QUOTA") { blsQuota = true; fails.push(G.jobs[i][0] + ": BLS 일일 무료한도 초과"); }
    else fails.push(G.jobs[i][0] + ": " + (res.reason && res.reason.message));
  });
  const fb = (window.MACRO_DATA || {}).series || {};
  Object.keys(fb).forEach(k => { if (!S[k]) S[k] = fb[k]; });

  // 수동 보정치(macro_manual.json) 오버레이 — 같은 월은 수동값이 우선, 없는 월은 추가
  try {
    const mr = await fetch("macro_manual.json", { cache: "no-store" });
    if (mr.ok) {
      const man = await mr.json();
      Object.keys(man).forEach(k => {
        const mobs = (man[k] && man[k].obs) || [];
        if (!mobs.length) return;
        const merged = {};
        ((S[k] && S[k].obs) || []).forEach(o => { merged[o[0]] = o[1]; });
        mobs.forEach(o => { if (o && o.length === 2 && !isNaN(o[1])) merged[o[0]] = o[1]; });
        S[k] = { obs: Object.keys(merged).sort().map(p => [p, merged[p]]) };
      });
    }
  } catch (e) {}

  wrap.innerHTML = "";
  const now = new Date();
  const ts = now.getFullYear() + "-" + String(now.getMonth() + 1).padStart(2, "0") + "-" +
    String(now.getDate()).padStart(2, "0") + " " + String(now.getHours()).padStart(2, "0") + ":" +
    String(now.getMinutes()).padStart(2, "0");
  wrap.appendChild(el("div", "updated-chip", "조회시각 " + ts + " · 브라우저 직접 조회" +
    (fails.length ? " · 실패: " + esc(fails.join(" / ")) : "")));

  if (needKey) {
    const bar = el("div", "run-bar");
    const kb = el("button", "run-btn", "🔑 BEA API 키 등록");
    kb.onclick = () => { if (getBeaKey(true)) renderMacroInto(wrap, groupKey); };
    bar.appendChild(kb);
    bar.appendChild(el("span", "run-status",
      "PCE·미국 GDP는 BEA API 키가 필요합니다 (무료 — apps.bea.gov/api/signup). 키는 이 브라우저에만 저장됩니다."));
    wrap.appendChild(bar);
  }
  if (blsQuota) {
    const bar = el("div", "run-bar");
    const kb = el("button", "run-btn", "🔑 BLS API 키 등록");
    kb.onclick = () => { if (getBlsKey(true)) renderMacroInto(wrap, groupKey); };
    bar.appendChild(kb);
    bar.appendChild(el("span", "run-status",
      "BLS 무료 한도(IP당 일 25회) 초과 — 사내망은 IP를 공유해 팀 조회량에 따라 소진됩니다. 미 동부 자정에 리셋되며, 무료 키를 등록하면 일 500회로 늘어납니다 (data.bls.gov/registrationEngine)."));
    wrap.appendChild(bar);
  }
  const keyNote = "BEA API 키 등록 필요 — 상단 🔑 버튼으로 등록하면 표시됩니다.";
  const BLUE = "#1f5fd6", ORANGE = "#e67e22";

  if (groupKey === "labor") {
    macroSection(wrap, 1, "미국 비농업 취업자 증감 (Nonfarm Payrolls)", "출처: <b>BLS API</b> · 월간 · 전월대비 증감, 계절조정",
      [{ key: "nfp_chg", name: "취업자 증감", color: BLUE }], S,
      { unit: "천명", dec: 0, baseline: 0, baselineLabel: "0" });
    macroSection(wrap, 2, "미국 실업률", "출처: <b>BLS API</b> · 월간 · 계절조정",
      [{ key: "unemp_rate", name: "실업률", color: BLUE }], S, { unit: "%" });
    macroSection(wrap, 3, "미국 경제활동참가율", "출처: <b>BLS API</b> · 월간 · 계절조정",
      [{ key: "part_rate", name: "경제활동참가율", color: BLUE }], S, { unit: "%" });
  } else if (groupKey === "activity") {
    macroSection(wrap, 1, "ISM 제조업 PMI",
      "출처: <b>ISM (DBnomics)</b> · 월간 · 50 초과 = 확장 / 미만 = 수축 · 무료 미러 결측 구간은 공식 발표치로 수동 보정 (갱신: Claude에 새 발표치 전달)",
      [{ key: "ism_pmi", name: "제조업 PMI", color: BLUE }], S,
      { unit: "", baseline: 50, baselineLabel: "기준선 50" });
    macroSection(wrap, 2, "실질 GDP 성장률 (분기)",
      "출처: 미국 <b>BEA API</b>(전기비 연율) · 한국 <b>IMF IFS/DBnomics</b>(전기비, 미러 수분기 지연) · 분기 라벨은 분기말 월로 표시",
      [{ key: "us_gdp", name: "미국 (전기비 연율)", color: BLUE },
       { key: "kr_gdp", name: "한국 (전기비)", color: ORANGE }], S,
      { unit: "%", baseline: 0, baselineLabel: "0", ranges: [
        { n: "10년", f: n => 0 }, { n: "5년", f: n => n - 20 }, { n: "3년", f: n => n - 12 }], initIdx: 1, emptyHtml: needKey ? keyNote : null });
    macroSection(wrap, 3, "미국 광공업생산 (전년동월비)", "출처: <b>연준 G.17 (DBnomics)</b> · 월간 · 총지수 기준",
      [{ key: "ip_yoy", name: "광공업생산 YoY", color: BLUE }], S, { unit: "%", baseline: 0, baselineLabel: "0" });
    macroSection(wrap, 4, "미시간대 소비자심리지수",
      "출처: <b>미시간대 서베이 (DBnomics)</b> · 월간 · 미러 특성상 약 1년 지연될 수 있음 · 컨퍼런스보드 소비자신뢰지수는 유료라 제공 불가",
      [{ key: "mich_ics", name: "소비자심리지수", color: BLUE }], S, { unit: "" });
  } else {
    macroSection(wrap, 1, "미국 CPI 상승률 (전년동월비)", "출처: <b>미국 노동통계국 BLS API</b> · 월간 · 계절조정 지수 기준",
      [{ key: "cpi_yoy", name: "헤드라인", color: BLUE },
       { key: "cpi_core_yoy", name: "코어(식품·에너지 제외)", color: ORANGE }], S, { unit: "%" });
    macroSection(wrap, 2, "미국 PCE 물가 상승률 (전년동월비)", "출처: <b>미국 상무부 BEA API</b> · 월간 · 연준이 목표(2%)로 삼는 물가지표",
      [{ key: "pce_yoy", name: "헤드라인", color: BLUE },
       { key: "pce_core_yoy", name: "코어(식품·에너지 제외)", color: ORANGE }], S,
      { unit: "%", baseline: 2, baselineLabel: "연준 목표 2%", emptyHtml: needKey ? keyNote : null });
    macroSection(wrap, 3, "미 국채 장단기 금리차 (일별)",
      "출처: <b>미 재무부 일별 수익률곡선</b> · 10년-2년(시장 관습) · 10년-3개월(연준 침체확률 모형이 쓰는 지표) · 마이너스 = 역전",
      [{ key: "spread_2s10s", name: "10년-2년", color: BLUE },
       { key: "spread_3m10s", name: "10년-3개월", color: ORANGE }], S,
      { unit: "%p", dec: 2, baseline: 0, baselineLabel: "0 (역전 경계)", ranges: [
        { n: "3년(전체)", f: n => 0 }, { n: "1년", f: n => n - 252 }, { n: "6개월", f: n => n - 126 }, { n: "3개월", f: n => n - 63 }], initIdx: 1 });
  }

  wrap.appendChild(el("div", "hint", G.hint));
}

function macroSection(wrap, num, title, srcHtml, lines, S, opts) {
  const sec = el("div", "section");
  const head = el("div", "section-head");
  head.appendChild(el("div", "num", String(num)));
  const hmeta = el("div");
  hmeta.appendChild(el("h3", null, title));
  hmeta.appendChild(el("p", "src", srcHtml));
  head.appendChild(hmeta);
  sec.appendChild(head);
  const body = el("div", "pce-body");
  sec.appendChild(body);
  wrap.appendChild(sec);

  const avail = lines.filter(l => S[l.key] && (S[l.key].obs || []).length);
  if (!avail.length) {
    body.appendChild(el("div", "pce-empty", opts.emptyHtml ||
      "이 지표는 조회에 실패했습니다. 상단 🔄 새로고침으로 재시도하거나, 상단 칩의 실패 사유를 확인하세요."));
    return;
  }
  const unit = opts.unit || "";
  const dec = opts.dec == null ? 1 : opts.dec;
  const fmt = v => (v == null || isNaN(v) ? "·" : v.toFixed(dec) + unit);
  const bad = v => v == null || isNaN(v);

  // 통계 카드
  let statHtml = "";
  avail.forEach(l => {
    const obs = S[l.key].obs;
    const last = obs[obs.length - 1], prev = obs.length > 1 ? obs[obs.length - 2] : last;
    const d = last[1] - prev[1];
    statHtml += "<div class='pce-stat'>" + esc(l.name) + " (" + last[0] + ")" +
      "<b style='color:" + l.color + "'>" + fmt(last[1]) + "</b>" +
      "직전대비 " + (d >= 0 ? "+" : "") + d.toFixed(dec) + (unit === "%" ? "p" : "") + "</div>";
  });
  body.appendChild(el("div", "pce-stats", statHtml));

  // 기간 축 (시리즈 합집합, 문자열 정렬)
  const pset = new Set();
  avail.forEach(l => S[l.key].obs.forEach(o => pset.add(o[0])));
  const periods = Array.from(pset).sort();
  const N = periods.length;
  const maps = avail.map(l => { const m = {}; S[l.key].obs.forEach(o => { m[o[0]] = o[1]; }); return m; });

  const headRow = el("div", "pce-head-row");
  let legend = "";
  avail.forEach(l => {
    legend += "<span style='display:inline-flex;align-items:center;gap:5px;margin-right:12px'>" +
      "<span style='width:14px;height:3px;border-radius:2px;background:" + l.color + "'></span>" + esc(l.name) + "</span>";
  });
  headRow.appendChild(el("div", "pce-ctitle", legend));
  const ranges = el("div", "pce-ranges");
  headRow.appendChild(ranges);
  body.appendChild(headRow);

  const box = el("div", "pce-chartbox");
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("role", "img");
  svg.setAttribute("aria-label", title + " 추이");
  const tip = el("div", "pce-tip");
  box.appendChild(svg); box.appendChild(tip);
  body.appendChild(box);

  const GRID = "#f0f2f6", MUTE = "#8a96a6", BASEC = "#b9c2cf";
  const RANGES = (opts.ranges || [
    { n: "전체", f: n => 0 }, { n: "5년", f: n => n - 60 }, { n: "3년", f: n => n - 36 }, { n: "1년", f: n => n - 12 }
  ]).map(r => ({ n: r.n, from: Math.max(0, r.f(N)) }));
  const initIdx = opts.initIdx == null ? (opts.ranges ? 0 : 2) : opts.initIdx;
  let from = RANGES[Math.min(initIdx, RANGES.length - 1)].from;

  function draw() {
    const W = box.clientWidth, H = 300;
    if (!W) return;
    const M = { l: 44, r: 14, t: 14, b: 24 };
    const pw = W - M.l - M.r, ph = H - M.t - M.b;
    let lo = Infinity, hi = -Infinity;
    for (let i = from; i < N; i++) maps.forEach(m => {
      const v = m[periods[i]];
      if (!bad(v)) { if (v < lo) lo = v; if (v > hi) hi = v; }
    });
    if (opts.baseline != null) { lo = Math.min(lo, opts.baseline); hi = Math.max(hi, opts.baseline); }
    const pad = Math.max((hi - lo) * 0.12, dec >= 2 ? 0.05 : 0.5);
    lo -= pad; hi += pad;
    const stepC = [0.05, 0.1, 0.25, 0.5, 1, 2, 2.5, 5, 10, 20, 50, 100, 200, 500];
    let st = stepC[stepC.length - 1];
    for (const c of stepC) { if ((hi - lo) / c <= 8) { st = c; break; } }
    lo = Math.floor(lo / st) * st; hi = Math.ceil(hi / st) * st;
    const X = i => M.l + (N - 1 === from ? 0 : (i - from) / (N - 1 - from)) * pw;
    const Y = v => M.t + (1 - (v - lo) / (hi - lo)) * ph;
    let s = "";
    for (let g = lo; g <= hi + 1e-9; g += st) {
      s += "<line x1='" + M.l + "' y1='" + Y(g) + "' x2='" + (W - M.r) + "' y2='" + Y(g) + "' stroke='" + GRID + "' stroke-width='1'/>";
      s += "<text x='" + (M.l - 7) + "' y='" + (Y(g) + 4) + "' text-anchor='end' font-size='10.5' fill='" + MUTE + "'>" + (st < 1 ? g.toFixed(st < 0.1 ? 2 : 1) : g) + "</text>";
    }
    // x축 눈금: 창 안의 서로 다른 '연-월'을 균등 간격으로 최대 ~7개 표시 (월간/분기/일별 공통)
    const monthsArr = [];
    const seenM = new Set();
    for (let i = from; i < N; i++) {
      const key = periods[i].slice(0, 7);
      if (!seenM.has(key)) { seenM.add(key); monthsArr.push({ key, i }); }
    }
    const stepT = Math.max(1, Math.ceil(monthsArr.length / 7));
    monthsArr.forEach((mo, idx) => {
      if (idx % stepT !== 0) return;
      const yy = mo.key.slice(2, 4), mm = mo.key.slice(5, 7);
      s += "<text x='" + X(mo.i) + "' y='" + (H - 7) + "' text-anchor='middle' font-size='10.5' fill='" + MUTE + "'>" +
        (stepT >= 12 ? yy + "년" : yy + "." + mm) + "</text>";
    });
    if (opts.baseline != null) {
      s += "<line x1='" + M.l + "' y1='" + Y(opts.baseline) + "' x2='" + (W - M.r) + "' y2='" + Y(opts.baseline) + "' stroke='" + BASEC + "' stroke-width='1.4' stroke-dasharray='5 4'/>";
      s += "<text x='" + (M.l + 6) + "' y='" + (Y(opts.baseline) - 6) + "' font-size='10.5' fill='" + MUTE + "'>" + (opts.baselineLabel || opts.baseline) + "</text>";
    }
    avail.forEach((l, li) => {
      let p = "", pen = false;
      for (let i = from; i < N; i++) {
        const v = maps[li][periods[i]];
        if (bad(v)) { pen = false; continue; }
        p += (pen ? "L" : "M") + X(i).toFixed(1) + " " + Y(v).toFixed(1) + " ";
        pen = true;
      }
      s += "<path d='" + p + "' fill='none' stroke='" + l.color + "' stroke-width='2' stroke-linejoin='round' stroke-linecap='round'/>";
      for (let i = N - 1; i >= from; i--) {
        const v = maps[li][periods[i]];
        if (!bad(v)) {
          s += "<circle cx='" + X(i) + "' cy='" + Y(v) + "' r='3.5' fill='" + l.color + "' stroke='#fff' stroke-width='1.5'/>";
          s += "<text x='" + (X(i) - 6) + "' y='" + (Y(v) - 9) + "' text-anchor='end' font-size='11.5' font-weight='700' fill='" + l.color + "'>" + fmt(v) + "</text>";
          break;
        }
      }
    });
    s += "<line class='xh' x1='0' y1='" + M.t + "' x2='0' y2='" + (M.t + ph) + "' stroke='" + MUTE + "' stroke-width='1' stroke-dasharray='3 3' visibility='hidden'/>";
    svg.setAttribute("height", H);
    svg.innerHTML = s;
    svg._map = { X, Y, M, pw };
  }
  svg.addEventListener("mousemove", ev => {
    if (!svg._map) return;
    const r = svg.getBoundingClientRect();
    const { X, Y, M, pw } = svg._map;
    let i = from + Math.round((ev.clientX - r.left - M.l) / pw * (N - 1 - from));
    i = Math.max(from, Math.min(N - 1, i));
    const xh = svg.querySelector(".xh");
    xh.setAttribute("x1", X(i)); xh.setAttribute("x2", X(i)); xh.setAttribute("visibility", "visible");
    let html = periods[i], firstV = null;
    avail.forEach((l, li) => {
      const v = maps[li][periods[i]];
      if (!bad(v) && firstV == null) firstV = v;
      html += "<br><span style='color:" + l.color + "'>●</span> " + esc(l.name) + " <b>" + fmt(v) + "</b>";
    });
    tip.style.display = "block";
    tip.style.left = X(i) + "px";
    tip.style.top = (firstV != null ? Y(firstV) : 60) + "px";
    tip.innerHTML = html;
  });
  svg.addEventListener("mouseleave", () => {
    tip.style.display = "none";
    const a = svg.querySelector(".xh");
    if (a) a.setAttribute("visibility", "hidden");
  });
  RANGES.forEach(rg => {
    const b = el("button", rg.from === from ? "on" : null, rg.n);
    b.onclick = () => {
      from = rg.from;
      ranges.querySelectorAll("button").forEach(x => x.classList.toggle("on", x === b));
      draw();
    };
    ranges.appendChild(b);
  });
  if (window.ResizeObserver) { new ResizeObserver(() => draw()).observe(box); }
  else window.addEventListener("resize", draw);
  draw();
}
