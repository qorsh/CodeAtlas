// 뷰어 연기 시험. 브라우저 없이 가짜 DOM·캔버스로 code-atlas.html 을 띄워, 데이터 폴더 하나를 끝까지 그려 본다.
//   node tests/viewer-smoke.js code-atlas.html <atlas-data.json 이 있는 폴더>
// 예외가 나면 0 이 아닌 코드로 끝난다. 화면 모양은 보지 않는다(그건 실제 브라우저에서 확인한다).
const _si = setInterval; global.setInterval = (f, ms) => { const t = _si(f, ms); t.unref(); return t; };
const fs = require('fs'), path = require('path');
const [htmlPath, dataDir] = process.argv.slice(2);
if (!htmlPath || !dataDir){ console.error('사용법: node tests/viewer-smoke.js code-atlas.html <데이터 폴더>'); process.exit(2); }
let js = fs.readFileSync(htmlPath, 'utf8').match(/<script>\n([\s\S]*)<\/script>/)[1];
js = js.replace(/\n\}\)\(\);\s*$/, `\nglobalThis.__T = {cam, state, load, runScenario, runFreeSearch, selectUnit, runImpact, fitAll, search, draw, clearAll,
  get SCENARIOS(){return SCENARIOS}, get NOTES(){return NOTES}, get UNITS(){return UNITS}, get EDGES(){return EDGES}, get SYSL(){return SYSL}, get MODS(){return MODS},
  get WORLD(){return WORLD}, get fitZ(){return fitZ}, get vis(){return vis}};\n})();`);
const ctxStub = () => new Proxy({}, { get(t, k){ if (k in t) return t[k]; if (k === 'measureText') return s => ({width: String(s).length*6});
  if (k === 'createRadialGradient' || k === 'createLinearGradient') return () => ({addColorStop(){}}); return () => {}; }, set(t, k, v){ t[k] = v; return true; } });
const els = {};
const mkEl = sel => { const h = {}; return { sel, hidden:false, innerHTML:'', value:'', textContent:'', dataset:{}, style:{}, width:408, height:256,
  classList:{toggle(){},add(){},remove(){}}, addEventListener(t,f){ (h[t]=h[t]||[]).push(f); }, getContext: () => ctxStub(), setPointerCapture(){},
  getBoundingClientRect: () => ({left:0,top:0,width:204,height:128}), remove(){}, querySelectorAll: () => [], querySelector: () => null, scrollIntoView(){}, focus(){}, blur(){}, select(){}, setAttribute(){}, closest: () => null }; };
global.document = { querySelector: s => els[s] || (els[s] = mkEl(s)), querySelectorAll: () => [], addEventListener(){}, fonts:{ready:Promise.resolve()}, title:'' };
global.innerWidth = 1440; global.innerHeight = 900; global.devicePixelRatio = 2;
global.matchMedia = () => ({matches:false}); global.addEventListener = () => {};
global.location = {hash:''}; global.localStorage = {getItem: () => null, setItem(){}};
const raf = []; global.requestAnimationFrame = f => { raf.push(f); };
global.fetch = async url => { const f = path.join(dataDir, String(url).split('?')[0]);
  if (!fs.existsSync(f)) return {ok:false, status:404, text: async () => '', json: async () => { throw new Error('404'); }};
  const body = fs.readFileSync(f, 'utf8'); return {ok:true, status:200, text: async () => body, json: async () => JSON.parse(body)}; };
let clock = 0; const step = (n=1) => { for (let i = 0; i < n; i++){ clock += 16; const q = raf.splice(0); for (const f of q) f(clock); } };
eval(js);
(async () => {
  await new Promise(r => setTimeout(r, 200));
  const T = globalThis.__T; if (!T || !T.UNITS.length) throw new Error('시작 실패: ' + (els['#boot'] ? els['#boot'].textContent : ''));
  step(3);
  console.log(`제목 "${document.title}" · 표지 ${els['#brand-name'].textContent} · 시스템 ${T.SYSL.length} · 모듈 ${T.MODS.length} · 유닛 ${T.UNITS.length} · 연결 ${T.EDGES.length}`);
  console.log('시스템 색 ' + T.SYSL.map(s => `${s.id}=${s.color}`).join(' '));
  if (T.SYSL.some(s => !/^#[0-9A-Fa-f]{6}$/.test(s.color))) throw new Error('색이 정해지지 않은 시스템이 있다');
  for (const s of T.SYSL) for (const z of [T.fitZ*0.6, T.fitZ, 0.12, 0.33, 0.85, 3]){ T.cam.z = z; T.cam.x = s.x + s.w*0.4; T.cam.y = s.y + s.h*0.4; T.draw(clock += 16); }
  console.log(`예시 문의 ${T.SCENARIOS.length}건 · 사람이 쓴 설명 ${Object.keys(T.NOTES).length}건`);
  for (const sc of T.SCENARIOS){ T.runScenario(sc); step(70); const tr = T.state.trace;
    if (!tr || !tr.steps.length) throw new Error('예시 문의가 풀리지 않는다: ' + sc.id);
    console.log(`  ${sc.id}: ${tr.steps.length}/${sc.steps.length} 단계가 데이터의 유닛과 맞음`);
    for (const st of tr.steps){ T.selectUnit(st.ui, st.mi, true); step(2); } T.draw(clock += 16); }
  T.clearAll(); step(2);
  let maxHtml = 0;
  for (const u of T.UNITS){ T.selectUnit(u.i, u.m.length ? 0 : null, false); maxHtml = Math.max(maxHtml, els['#insp'].innerHTML.length); if (u.i % 40 === 0) T.draw(clock += 16); }
  const top = T.UNITS.slice().sort((a,b) => (b.in.length + b.out.length) - (a.in.length + a.out.length))[0];
  T.selectUnit(top.i, null, true); T.runImpact(top.i); step(40); T.draw(clock += 16);
  console.log(`유닛 ${T.UNITS.length}개 선택·설명 패널 통과 (가장 긴 패널 ${maxHtml}자) · 영향 범위: ${top.name}`);
  T.clearAll();
  const words = [...new Set(T.UNITS.filter(u => u.file).slice(0, 400).map(u => u.name))].filter((_, i) => i % 97 === 0).slice(0, 5);
  for (const w of words){ const h = T.search(w, 8); if (!h.some(x => T.UNITS[x.u].name === w)) throw new Error('이름으로 찾지 못했다: ' + w + ' → ' + h.map(x => T.UNITS[x.u].name).join(', ')); }
  T.runFreeSearch('로그인 오류'); step(40); T.draw(clock += 16); T.clearAll();
  console.log('이름 검색 ' + words.length + '건 일치 · 자유 검색 통과');
  const t0 = Date.now(); T.load(10); step(3); T.cam.z = T.fitZ; T.draw(clock += 16);
  console.log(`규모 시험 ×10: 유닛 ${T.UNITS.length} · 연결 ${T.EDGES.length} · ${Date.now() - t0}ms`);
  T.load(1); step(2);
  console.log('통과');
})().catch(e => { console.error('실패:', e.stack || e); process.exit(1); });
