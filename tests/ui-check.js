#!/usr/bin/env node
/* 실제 Chrome 으로 화면을 눌러 보는 시험. 로컬 서버(atlas.py open)를 띄우고 진짜 마우스 입력을 넣는다.

     node tests/ui-check.js [그림을 둘 폴더]

   시험용 프로젝트 셋(shop · booking · backoffice)을 임시 작업 공간에 넣고 다음을 본다.
     · 시스템 목록에서 눌러 이동 · 배치 편집으로 시스템을 끌어 옮기고 저장 · 확인이 필요한 호출 목록
     · 설정 패널에서 레거시 등록, 부르는 서버 지정, 다시 분석, 폴더 추가
     · 서버 구성: 웹서버를 적어 넣고 주소를 한 서버로 묶기 · AI 연결(이 컴퓨터에 띄운 모의 엔드포인트로)과 TRACE 추적
   가짜 DOM 시험(viewer-smoke.js)이 못 보는 것(실제 클릭, 서버와 주고받기, 다시 그리기)을 여기서 확인한다.
   tree-sitter 가 없는 환경에서는 Go 서버를 건너뛰므로, 시스템·유닛 수는 그 경우에도 맞는 값으로 본다.
   분석에 쓸 파이썬은 ATLAS_PYTHON 으로 바꿀 수 있다. */
const { spawn, execFileSync } = require('child_process');
const fs = require('fs'), os = require('os'), path = require('path');
const REPO = path.dirname(__dirname), PY = process.env.ATLAS_PYTHON || 'python3';
const CHROME = ['/Applications/Google Chrome.app/Contents/MacOS/Google Chrome', '/Applications/Chromium.app/Contents/MacOS/Chromium', '/usr/bin/google-chrome', '/usr/bin/chromium', '/usr/bin/chromium-browser'].find(p => fs.existsSync(p));
if (!CHROME){ console.log('Chrome 을 찾지 못했습니다.'); process.exit(2); }
const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'atlas-ui-')), ws = path.join(tmp, 'ws'), cfg = path.join(ws, 'atlas.config.json');
const shots = process.argv[2] ? path.resolve(process.argv[2]) : path.join(tmp, 'shots');
fs.mkdirSync(ws); fs.mkdirSync(shots, {recursive:true});
const atlas = (...a) => execFileSync(PY, [path.join(REPO, 'atlas', 'atlas.py'), '--config', cfg, ...a], {encoding:'utf8'});
const sleep = ms => new Promise(r => setTimeout(r, ms));
const kids = [], out = [];
const ok = (name, cond, info='') => { out.push(`  ${cond ? '통과' : '실패'}  ${name}${info !== '' && !cond ? '  ← ' + info : ''}`); };
const done = code => { for (const k of kids){ try { k.kill('SIGKILL'); } catch (e){} } try { fs.rmSync(tmp, {recursive:true, force:true}); } catch (e){} process.exit(code); };
setTimeout(() => { console.log(out.join('\n')); console.log('시간 초과'); done(2); }, 280000);

(async () => {
  atlas('init', 'ui-check');
  for (const n of ['shop', 'booking', 'backoffice']) atlas('add', path.join(REPO, 'fixtures', n));
  atlas('sync', '--no-fetch');
  // 나중에 화면에서 추가해 볼 폴더
  const extra = path.join(tmp, 'more', 'extra-web'); fs.mkdirSync(path.join(extra, 'src'), {recursive:true});
  fs.writeFileSync(path.join(extra, 'package.json'), '{"name": "extra-web", "dependencies": {"react": "18.3.1"}}');
  fs.copyFileSync(path.join(REPO, 'fixtures', 'booking', 'booking-web', 'src', 'api.ts'), path.join(extra, 'src', 'api.ts'));
  // 규칙이 없는 방식으로 API 를 정의한 서버: 읽지 못한 꼴로 잡혀야 하고, 화면에서 규칙으로 만들 수 있어야 한다
  const pushApi = path.join(tmp, 'more', 'push-api'); fs.mkdirSync(path.join(pushApi, 'src', 'main', 'java', 'demo'), {recursive:true});
  fs.writeFileSync(path.join(pushApi, 'pom.xml'), '<project/>');
  fs.writeFileSync(path.join(pushApi, 'src', 'main', 'java', 'demo', 'PushHandlers.java'), 'package demo;\n\npublic class PushHandlers {\n' + ['list', 'send', 'read'].map(n => `    @Endpoint("/push/${n}")\n    public String ${n}() { return ""; }\n`).join('\n') + '}\n');
  const extra2 = path.join(tmp, 'more', 'extra-two'); fs.mkdirSync(path.join(extra2, 'src'), {recursive:true});
  fs.writeFileSync(path.join(extra2, 'package.json'), '{"name": "extra-two", "dependencies": {"react": "18.3.1"}}');
  fs.copyFileSync(path.join(REPO, 'fixtures', 'backoffice', 'src', 'api.ts'), path.join(extra2, 'src', 'api.ts'));

  const server = spawn(PY, [path.join(REPO, 'atlas', 'atlas.py'), '--config', cfg, 'open', '--no-browser'], {env:{...process.env, PYTHONUNBUFFERED:'1'}}); kids.push(server);
  let url = ''; server.stdout.on('data', d => { const m = /(http:\/\/127\.0\.0\.1:\d+\/)/.exec(String(d)); if (m) url = m[1]; });
  for (let i = 0; i < 80 && !url; i++) await sleep(100);
  if (!url) throw new Error('로컬 서버가 뜨지 않았습니다');
  const DP = 9300 + Math.floor(Math.random()*500);
  const chrome = spawn(CHROME, ['--headless=new', '--no-first-run', '--no-default-browser-check', '--disable-gpu', '--hide-scrollbars', `--user-data-dir=${path.join(tmp, 'profile')}`,
    `--remote-debugging-port=${DP}`, '--window-size=1440,900', '--force-device-scale-factor=1', url + '?debug'], {stdio:'ignore'}); kids.push(chrome);
  let sock = null;
  for (let i = 0; i < 60 && !sock; i++){ await sleep(250); try { const tabs = await (await fetch(`http://127.0.0.1:${DP}/json`)).json(); const t = tabs.find(t => t.type === 'page' && t.url.startsWith(url)); if (t) sock = new WebSocket(t.webSocketDebuggerUrl); } catch (e){} }
  if (!sock) throw new Error('Chrome 에 붙지 못했습니다');
  await new Promise(r => sock.addEventListener('open', r));
  let id = 0; const wait = new Map(), errors = [];
  sock.addEventListener('message', m => { const d = JSON.parse(m.data); if (d.id && wait.has(d.id)){ wait.get(d.id)(d); wait.delete(d.id); } else if (d.method === 'Runtime.exceptionThrown') errors.push(d.params.exceptionDetails.exception ? d.params.exceptionDetails.exception.description : d.params.exceptionDetails.text); });
  const send = (method, params={}) => new Promise(r => { const i = ++id; wait.set(i, r); sock.send(JSON.stringify({id:i, method, params})); });
  const ev = async expr => { const r = await send('Runtime.evaluate', {expression:expr, returnByValue:true, awaitPromise:true}); if (r.result.exceptionDetails) throw new Error('eval: ' + (r.result.exceptionDetails.exception ? r.result.exceptionDetails.exception.description : r.result.exceptionDetails.text) + '\n' + expr.slice(0, 200)); return r.result.result.value; };
  const mouse = (type, x, y, extra={}) => send('Input.dispatchMouseEvent', {type, x, y, button:type === 'mouseMoved' ? 'none' : 'left', buttons:type === 'mousePressed' ? 1 : 0, clickCount:type === 'mouseMoved' ? 0 : 1, ...extra});
  const click = async (x, y) => { await mouse('mouseMoved', x, y); await mouse('mousePressed', x, y); await mouse('mouseReleased', x, y); await sleep(140); };
  const drag = async (x0, y0, x1, y1) => { await mouse('mouseMoved', x0, y0); await mouse('mousePressed', x0, y0); for (let i = 1; i <= 8; i++) await mouse('mouseMoved', x0 + (x1-x0)*i/8, y0 + (y1-y0)*i/8, {button:'left', buttons:1}); await mouse('mouseReleased', x1, y1); await sleep(160); };
  const at = sel => ev(`(() => { const e = document.querySelector(${JSON.stringify(sel)}); if (!e) return null; e.scrollIntoView({block:'center'}); const r = e.getBoundingClientRect(); return [r.left + r.width/2, r.top + r.height/2]; })()`);
  const tap = async sel => { const p = await at(sel); if (!p) throw new Error('화면에 없는 것: ' + sel); await click(p[0], p[1]); };
  const text = sel => ev(`(document.querySelector(${JSON.stringify(sel)}) || {}).innerText || ''`);
  const snap = async name => { await sleep(350); const r = await send('Page.captureScreenshot', {format:'png'}); fs.writeFileSync(path.join(shots, name + '.png'), Buffer.from(r.result.data, 'base64')); };
  const ready = async (cond='true', ms=30000) => { for (let t = 0; t < ms; t += 250){ await sleep(250); try { if (await ev(`!!window.__T && __T.SYSL !== undefined && !document.querySelector('#boot') && (${cond})`)) return true; } catch (e){} } return false; };
  const S = 'const T = __T, sx = wx => (wx - T.cam.x)*T.cam.z + innerWidth/2, sy = wy => (wy - T.cam.y)*T.cam.z + innerHeight/2;';
  const spots = () => ev(`Object.fromEntries(__T.SYSL.filter(s => !s.base).map(s => [s.id, [s.x, s.y]]))`);       // 시스템 자리
  const same = (before, after) => Object.keys(before).filter(id => !after[id] || after[id][0] !== before[id][0] || after[id][1] !== before[id][1]);
  const apart = () => ev(`(() => { const L = __T.SYSL.filter(s => !s.base), bad = []; for (let i = 0; i < L.length; i++) for (let j = i+1; j < L.length; j++){ const a = L[i], b = L[j]; if (a.x < b.x + b.w && a.x + a.w > b.x && a.y < b.y + b.h && a.y + a.h > b.y) bad.push(a.id + '×' + b.id); } return bad; })()`);
  const readNotes = () => { try { return JSON.parse(fs.readFileSync(path.join(ws, 'atlas-notes.json'), 'utf8')); } catch (e){ return {}; } };
  await send('Runtime.enable'); await send('Page.enable');

  // 1) 열기
  ok('열린다: 유닛이 있고 카메라 값이 정상', await ready('__T.UNITS.length > 20 && isFinite(__T.cam.z) && isFinite(__T.cam.x)'), await ev('JSON.stringify(window.__T ? __T.cam : null)'));
  ok('로컬 서버로 열면 설정 기능이 켜진다', await ev('__T.API.ok'));
  const pb = await text('#pendbox');
  ok('확인이 필요한 호출과 지도에 없는 주소를 알린다', pb.includes('확인이 필요한 호출 1') && pb.includes('지도에 없는 주소'), pb);
  ok('설정 단추는 톱니바퀴이고 L1~L4 와 떨어져 있다', await ev(`(() => { const b = document.querySelector('#level .lvnav'), prev = b.previousElementSibling; return !!b.querySelector('svg') && b.innerText.trim() === '설정' && b.getBoundingClientRect().left - prev.getBoundingClientRect().right >= 6; })()`));
  await sleep(700);
  ok('처음 놓인 자리를 기억해 둔다', Object.keys(readNotes().positions || {}).length === await ev(`__T.SYSL.filter(s => !s.base).length`), JSON.stringify(readNotes()));
  await snap('1-overview');

  // 2) 시스템 목록에서 눌러 이동
  await tap('#level .lvnav');
  ok('설정 메뉴가 열리고 시스템 목록이 있다', (await text('#trace')).includes('시스템으로 이동') && (await text('#trace .eyebrow')).trim() === '설정' && await ev(`document.querySelectorAll('#navlist .srow').length`) >= 7, (await text('#trace')).slice(0, 120));
  const z0 = await ev('__T.cam.z');
  await tap('#navlist [data-sys="booking-server"]'); await sleep(1500);
  ok('목록에서 누르면 그 시스템으로 간다', await ev(`(() => { const T = __T, s = T.SYS['booking-server']; return T.cam.z > ${z0}*1.3 && T.cam.x > s.x && T.cam.x < s.x + s.w && T.cam.y > s.y && T.cam.y < s.y + s.h; })()`), await ev('JSON.stringify(__T.cam)'));
  await snap('2-system-list');

  // 3) 평소에는 끌어도 시스템이 움직이지 않는다 (지도가 움직인다)
  await tap('#level [data-lv="0"]'); await sleep(1300);
  let p = await ev(`(() => { ${S} const s = T.SYS.web; return [sx(s.x + s.w/2), sy(s.y + s.h/2), s.x, s.y, T.cam.x]; })()`);
  await drag(p[0], p[1], p[0] + 140, p[1] + 60);
  ok('평소에는 끌면 지도가 움직이고 시스템은 그대로', await ev(`__T.SYS.web.x === ${p[2]} && __T.cam.x !== ${p[4]}`));

  // 4) 배치 편집: 끌어 옮기고 저장
  await tap('#trace [data-act="arrange"]');
  ok('배치 편집이 켜진다', await ev('__T.state.arrange') && (await text('#walk')).includes('배치 편집'), await text('#walk'));
  await tap('#level [data-lv="0"]'); await sleep(1300);
  p = await ev(`(() => { ${S} const s = T.SYS.web; return [sx(s.x + s.w/2), sy(s.y + s.h/2), s.x, s.y, T.cam.x, T.UNITS.find(u => u.sys === 'web').x]; })()`);
  await drag(p[0], p[1], p[0] - 260, p[1] - 40);      // 왼쪽 빈 곳으로
  const moved = await ev(`(() => { const T = __T, s = T.SYS.web, u = T.UNITS.find(u => u.sys === 'web'); return [s.x, s.y, true, T.cam.x, u.x, u.x > s.x && u.x < s.x + s.w]; })()`);
  ok('배치 편집 중에는 끌면 시스템이 움직인다', moved[2] === true && (moved[0] !== p[2] || moved[1] !== p[3]) && moved[3] === p[4], JSON.stringify([p, moved]));
  ok('안에 든 유닛도 함께 움직인다', moved[4] !== p[5] && moved[5] === true);
  await snap('3-arrange');
  await tap('#walk [data-act="arrange"]'); await sleep(900);
  const notes = JSON.parse(fs.readFileSync(path.join(ws, 'atlas-notes.json'), 'utf8'));
  ok('옮긴 자리가 작업 공간에 저장된다', !!notes.positions && notes.positions.web && notes.positions.web[0] === moved[0] && notes.positions.web[1] === moved[1], JSON.stringify(notes));
  await send('Page.reload'); await ready('__T.UNITS.length > 20');
  ok('다시 열어도 옮긴 자리에 있다', await ev(`__T.SYS.web.x === ${moved[0]} && __T.SYS.web.y === ${moved[1]}`));

  // 5) 확인이 필요한 호출
  await tap('#pendbox [data-act="pending"]'); await sleep(900);
  ok('확인이 필요한 호출 목록', await ev('__T.state.hits && __T.state.hits.list.length === 1') && (await text('#trace')).includes('후보'), await text('#trace h2'));
  await tap('#trace .step'); await sleep(900);
  ok('유닛을 고르면 후보 서버가 나온다', (await text('#insp')).includes('확인이 필요한 호출') && await ev(`document.querySelectorAll('#insp .xrow[style*="FFB454"]').length`) === 2, (await text('#insp')).slice(0, 200));
  await snap('4-pending');

  // 6) 설정 패널: 레거시 등록, 부르는 서버 지정, 다시 분석
  await tap('#insp [data-act="cfg"]'); await sleep(500);
  const c0 = await text('#cfg');
  ok('설정 패널이 열리고 폴더·시스템이 보인다', await ev(`!document.querySelector('#cfg').hidden`) && c0.includes('fixtures/shop') && c0.includes('order-service'), c0.slice(0, 300));
  ok('지도에 없는 주소가 레거시 후보로 보인다', c0.includes('erp.legacy.example') && c0.includes('레거시로 등록'));
  ok('연결 진단이 보인다', c0.includes('연결 진단') && c0.includes('눈에 띄는 문제 없음') && /부르는 호출 \d+개 중 이어진 것 \d+개/.test(c0), c0.slice(0, 400));
  await tap('#cfg [data-c="ext-from"]'); await sleep(300);
  ok('레거시로 등록을 누르면 주소가 채워진다', await ev(`document.querySelector('#x-hosts').value`) === 'erp.legacy.example');
  await ev(`document.querySelector('#x-name').focus()`); await send('Input.insertText', {text:'레거시 ERP'});
  await tap('#cfg [data-c="ext-add"]'); await sleep(500);
  const c1 = await text('#cfg');
  ok('레거시가 등록되고 다시 분석하라고 알린다', c1.includes('레거시 ERP') && c1.includes('설정이 바뀌었습니다'), c1.slice(0, 300));
  await tap('#cfg [data-c="link"][data-to="booking-server"]'); await sleep(500);
  ok('부르는 서버를 정하면 목록에 남는다', (await text('#cfg')).includes('애매하면 이 서버로 본다'));
  await snap('5-settings');
  const before6 = await spots();
  await tap('#cfg-status [data-c="sync"]');
  ok('다시 분석이 끝나면 지도가 새로 열린다', await ready(`__T.META.systems.some(s => s.ext)`, 40000), await text('#cfg').catch(() => ''));
  ok('레거시가 지도에 시스템으로 나온다', await ev(`(() => { const s = __T.SYS.erp; return !!s && s.ext === true && s.w > 0 && isFinite(s.x) && s.units.length === 1 && s.units[0].m.length === 4; })()`));
  ok('두 시스템이 같은 레거시로 이어진다', await ev(`(() => { const T = __T, from = new Set(T.EDGES.filter(e => e.g === 'http' && T.UNITS[e.t].sys === 'erp').map(e => T.UNITS[e.s].sys)); return from.has('booking-server') && from.has('booking-web'); })()`));
  ok('확인이 필요한 호출이 없어지고 관리자 화면이 예약 서버로 이어진다', await ev(`!document.querySelector('#pendbox').innerText.includes('확인이 필요한 호출') && __T.EDGES.some(e => e.g === 'http' && __T.UNITS[e.s].sys === 'backoffice' && __T.UNITS[e.t].sys === 'booking-server')`), await text('#pendbox'));
  ok('레거시가 새로 생겨도 기존 시스템 자리는 그대로', same(before6, await spots()).length === 0 && (await spots()).web[0] === moved[0], same(before6, await spots()).join(', '));
  ok('새로 놓인 레거시는 다른 시스템과 겹치지 않는다', (await apart()).length === 0, (await apart()).join(', '));
  await send('Input.dispatchKeyEvent', {type:'keyDown', key:'Escape', code:'Escape', windowsVirtualKeyCode:27}); await send('Input.dispatchKeyEvent', {type:'keyUp', key:'Escape', code:'Escape', windowsVirtualKeyCode:27});
  await tap('#level [data-lv="0"]'); await sleep(1400);
  await snap('6-legacy');

  // 7) 설정 패널: 폴더를 이어서 추가 (첫 번째는 분석을 미루고, 두 번째에서 한 번에 분석)
  const before7 = await spots();
  await tap('#level .lvnav'); await tap('#trace [data-act="cfg"]'); await sleep(500);
  await tap('#cfg [data-c="remove"][data-v="backoffice"]'); await sleep(400);
  ok('빼기는 한 번 더 눌러야 실행된다', (await text('#cfg')).includes('한 번 더 누르면 뺍니다') && await ev('__T.API.st.sources.length') === 3);
  await tap('#cfg [data-c="open-add"]'); await sleep(500);
  await ev(`document.querySelector('#add-path').value = ${JSON.stringify(path.dirname(extra))}`);
  await tap('#cfg [data-c="ls-go"]'); await sleep(500);
  ok('폴더 목록이 나온다', (await text('#cfg .dirs')).includes('extra-web') && (await text('#cfg .dirs')).includes('extra-two'), await text('#cfg .dirs'));
  await tap('#cfg .dirs [data-v="extra-web"]'); await sleep(500);
  await tap('#cfg [data-c="preview"]'); await sleep(600);
  ok('미리보기가 시스템을 알아본다', (await text('#cfg')).includes('extra-web') && (await text('#cfg')).includes('추가하고 분석'), (await text('#cfg')).slice(0, 300));
  await tap('#cfg [data-c="add-more"]'); await sleep(700);
  const c7 = await text('#cfg');
  ok('추가만 하면 폴더 고르는 화면에 남고 넣은 폴더가 표시된다', c7.includes('extra-web 을(를) 추가했습니다') && (await text('#cfg .dirs')).includes('추가됨') && c7.includes('다시 분석'), c7.slice(0, 300));
  await tap('#cfg .dirs [data-v="push-api"]'); await sleep(500);
  await tap('#cfg [data-c="preview"]'); await sleep(600);
  await tap('#cfg [data-c="add-more"]'); await sleep(700);
  await tap('#cfg .dirs [data-v="extra-two"]'); await sleep(500);
  await tap('#cfg [data-c="preview"]'); await sleep(600);
  await tap('#cfg [data-c="add"]');
  ok('추가하고 분석하면 넣어 둔 폴더까지 함께 지도에 나온다', await ready(`!!__T.SYS['extra-web'] && __T.SYS['extra-web'].w > 0 && !!__T.SYS['extra-two'] && __T.SYS['extra-two'].w > 0`, 40000), await text('#cfg').catch(() => ''));
  ok('폴더를 추가해도 기존 시스템 자리는 그대로', same(before7, await spots()).length === 0, same(before7, await spots()).join(', '));
  ok('새 시스템은 다른 시스템과 겹치지 않는다', (await apart()).length === 0, (await apart()).join(', '));
  ok('경로가 같은 호출은 틀린 서버에 붙이지 않는다', await ev(`!__T.EDGES.some(e => e.g === 'http' && __T.UNITS[e.s].sys === 'extra-web' && __T.UNITS[e.t].sys === 'order-service')`));
  await tap('#level [data-lv="0"]'); await sleep(1400);
  await snap('6b-after-add');

  // 7b) 읽지 못한 꼴을 골라 규칙으로 만들기
  ok('규칙이 없는 방식을 왼쪽 위 상자가 알린다', (await text('#pendbox')).includes('읽지 못한 꼴 1가지'), await text('#pendbox'));
  await tap('#pendbox [data-act="cfg"]'); await sleep(600);
  const c8 = await text('#cfg');
  ok('설정 패널에 읽지 못한 꼴이 예와 함께 나온다', c8.includes('읽지 못한 꼴') && c8.includes('어노테이션 @Endpoint') && c8.includes('/push/list'), c8.slice(0, 400));
  await tap('#cfg [data-c="rule"][data-role="provide"]'); await sleep(600);
  ok('API 정의로 읽기를 누르면 규칙이 만들어진다', (await text('#cfg')).includes('API 정의로 읽게 했습니다') && fs.existsSync(path.join(ws, 'rules', 'custom.json')), (await text('#cfg')).slice(0, 400));
  await tap('#cfg-status [data-c="sync"]');
  ok('다시 분석하면 그 꼴이 API 로 읽힌다', await ready(`!!__T.SYS['push-api'] && __T.SYS['push-api'].routes === 3`, 40000), await ev(`JSON.stringify(__T.SYS['push-api'] ? __T.SYS['push-api'].routes : null)`));
  ok('읽지 못한 꼴 알림이 사라진다', !(await text('#pendbox')).includes('읽지 못한 꼴'), await text('#pendbox'));

  // 7c) 서버 구성: 웹서버를 적어 넣고, 주소(인스턴스)를 한 서버로 묶는다
  const before9 = await spots();
  await tap('#level .lvnav'); await sleep(300);
  ok('설정 메뉴에 웹서버 · WAS 추가와 주소 묶기가 있다', (t => ['웹서버 추가', 'WAS 추가', '서버 주소 묶기', 'AI 연결 설정'].every(w => t.includes(w)))(await text('#trace')), (await text('#trace')).slice(0, 300));
  await tap('#trace [data-act="cfg-web"]'); await sleep(600);
  ok('웹서버 추가를 누르면 입력 칸이 열리고 이름 칸에 커서가 간다', await ev(`!document.querySelector('#cfg').hidden && document.activeElement && document.activeElement.id === 'n-name' && document.querySelector('#n-kind').value === 'web'`), (await text('#cfg')).slice(0, 200));
  await ev(`(() => { const set = (sel, v) => { document.querySelector(sel).value = v; }; set('#n-name', '예약 웹서버'); set('#n-product', 'Nginx'); set('#n-rows .irow [data-f=host]', 'https://api.booking.example/v1'); set('#n-rows .irow [data-f=env]', '운영'); })()`);
  await tap('#cfg [data-c="n-row"]'); await sleep(300);
  ok('줄을 추가해도 적던 내용이 남는다', await ev(`document.querySelector('#n-name').value === '예약 웹서버' && document.querySelectorAll('#n-rows .irow').length === 2 && document.querySelector('#n-rows .irow [data-f=host]').value.includes('api.booking.example')`));
  await ev(`(() => { const r = document.querySelectorAll('#n-rows .irow')[1]; r.querySelector('[data-f=host]').value = '10.9.0.5:8443'; r.querySelector('[data-f=env]').value = '개발'; r.querySelector('[data-f=note]').value = '사내망'; })()`);
  await tap('#cfg input[data-to="booking-server"]'); await sleep(150);
  await snap('9a-webserver-form');
  await tap('#cfg [data-c="n-save"]'); await sleep(700);
  const c9 = await text('#cfg'), webId = await ev(`(__T.API.st.infra[0] || {}).id || ''`);
  ok('웹서버가 등록되고 다시 분석하라고 알린다', c9.includes('예약 웹서버 을(를) 저장했습니다') && c9.includes('다시 분석해야') && c9.includes('뒤: booking-server') && c9.includes('운영 api.booking.example'), c9.slice(0, 400));
  ok('설정 파일에 서버 구성이 적힌다', (() => { const n = (JSON.parse(fs.readFileSync(cfg, 'utf8')).infra || [])[0] || {}; return n.kind === 'web' && n.product === 'Nginx' && String(n.hosts) === 'api.booking.example,10.9.0.5:8443' && String(n.to) === 'booking-server' && n.host_info['10.9.0.5:8443'].note === '사내망'; })(), fs.readFileSync(cfg, 'utf8').slice(-500));
  ok('주소 묶기에 코드에서 본 주소가 주인과 함께 나온다', c9.includes('payment-service:8000') && c9.includes('어느 서버인지 정하지 않음') && c9.includes('지금: 예약 웹서버'), c9.slice(-900));
  await tap('#cfg input[data-pick="payment-service"]'); await tap('#cfg input[data-pick="payment-service:8000"]');
  await ev(`(() => { document.querySelector('#g-to').value = 'payment-service'; document.querySelector('select[data-env="payment-service:8000"]').value = '개발'; })()`);
  await snap('9b-group-hosts');
  await tap('#cfg [data-c="grp"]'); await sleep(700);
  ok('고른 주소가 한 서버로 묶인다', (await text('#cfg')).includes('주소 2개를 한 서버로 묶었습니다') && (() => { const y = JSON.parse(fs.readFileSync(cfg, 'utf8')).systems.find(y => y.id === 'payment-service'); return String(y.hosts) === 'payment-service,payment-service:8000' && y.host_info['payment-service:8000'].env === '개발'; })(), (await text('#cfg')).slice(0, 300));
  await tap('#cfg-status [data-c="sync"]');
  ok('다시 분석하면 웹서버가 지도에 오른다', await ready(`!!__T.SYS[${JSON.stringify(webId)}] && __T.SYS[${JSON.stringify(webId)}].w > 0`, 40000) && await ev(`__T.SYS[${JSON.stringify(webId)}].infra === 'web'`), webId);
  ok('웹서버 주소로 부르는 앱의 호출이 웹서버를 거쳐 서버로 이어진다', await ev(`(__T.META.chains || []).some(c => c.join('>') === ${JSON.stringify(webId + '>booking-server')}) && __T.EDGES.some(e => e.via && e.via[0] === ${JSON.stringify(webId)} && __T.UNITS[e.t].sys === 'booking-server')`), await ev(`JSON.stringify([__T.META.chains, __T.META.via])`));
  ok('웹서버가 생겨도 기존 시스템 자리는 그대로이고 겹치지 않는다', same(before9, await spots()).length === 0 && (await apart()).length === 0, same(before9, await spots()).join(', ') + ' / ' + (await apart()).join(', '));
  await tap('#level [data-lv="0"]'); await sleep(1400);
  await snap('9c-map-with-webserver');
  await ev(`location.hash = ${JSON.stringify('#' + webId + '-node')}`); await sleep(900);
  const i9 = await text('#insp');
  ok('웹서버를 고르면 적어 둔 정보와 앞뒤가 보인다', i9.includes('서버 구성') && i9.includes('Nginx') && i9.includes('운영 · api.booking.example') && i9.includes('개발 · 10.9.0.5:8443 · 사내망') && i9.includes('booking-web') && i9.includes('뒷단'), i9.slice(0, 500));
  await snap('9d-webserver-inspector');
  await ev(`(() => { const e = __T.EDGES.find(e => e.via); location.hash = ''; document.querySelector('#insp [data-act="close-insp"]').click(); return e.i; })()`);

  // 7d) AI 연결: 이 컴퓨터에 띄운 모의 엔드포인트(OpenAI 호환)로 붙여, TRACE 가 경로를 추적하는지 본다. 실제 AI 서비스로는 아무것도 보내지 않는다
  const prompts = [], heads = [];
  const mock = require('http').createServer((rq, rs) => { let b = ''; rq.on('data', d => { b += d; }); rq.on('end', () => {
    const body = JSON.parse(b || '{}'), prompt = ((body.messages || [])[0] || {}).content || ''; prompts.push(prompt); heads.push(rq.headers.authorization || '');
    const nums = re => [...new Set([...prompt.matchAll(re)].map(m => +m[1]))];
    const reply = prompt.includes('연결 시험') ? 'OK'
      : prompt.includes('모듈(폴더·패키지) 목록') ? JSON.stringify({modules:nums(/^(\d+)\|/gm).slice(0, 60)})
      : prompt.includes('코드 유닛 목록') ? '후보입니다.\n```json\n' + JSON.stringify({type:'q', units:nums(/^(\d+)\|/gm).slice(0, 14), keywords:['booking']}) + '\n```'
      : prompt.includes('분석가다') ? JSON.stringify({type:'q', summary:'모의 엔드포인트가 만든 요약입니다.', causes:[{title:'먼저 볼 곳', detail:'모의 응답', unit:nums(/^#(\d+) \[/gm)[0], member:null}], steps:nums(/^#(\d+) \[/gm).slice(0, 5).map(i => ({unit:i, member:null, note:'모의 단계'}))})
      : '{}';
    rs.writeHead(200, {'Content-Type':'application/json'}); rs.end(JSON.stringify({model:body.model, choices:[{message:{role:'assistant', content:reply}, finish_reason:'stop'}], usage:{prompt_tokens:1, completion_tokens:1}}));
  }); });
  await new Promise(r => mock.listen(0, '127.0.0.1', r));
  const mockUrl = `http://127.0.0.1:${mock.address().port}/v1`;
  ok('AI 가 꺼져 있을 때는 검색창이 키워드 검색이라고 알린다', !(await ev(`document.querySelector('#q').placeholder`)).includes('AI') && await ev(`__T.AI.state === 'off'`));
  await tap('#level .lvnav'); await sleep(300);
  await tap('#trace [data-act="cfg-ai"]'); await sleep(600);
  ok('AI 연결 화면이 열리고 아직 정하지 않았다고 나온다', (await text('#cfg')).includes('AI 연결') && (await text('#cfg')).includes('아직 정하지 않았습니다'), (await text('#cfg')).slice(0, 300));
  await ev(`(() => { const s = document.querySelector('#ai-provider'); s.value = 'openai'; s.dispatchEvent(new Event('change', {bubbles:true})); })()`); await sleep(300);
  ok('방식을 고르면 그 방식의 칸이 나온다', await ev(`!!document.querySelector('#ai-url') && !!document.querySelector('#ai-auth') && !!document.querySelector('#ai-key') && document.querySelector('#ai-key').type === 'password'`));
  await ev(`(() => { const set = (sel, v) => { document.querySelector(sel).value = v; }; set('#ai-url', ${JSON.stringify(mockUrl)}); set('#ai-model', 'house-model'); set('#ai-key', 'test-key-ui'); })()`);
  await snap('10a-ai-settings');
  await tap('#cfg [data-c="ai-test"]');
  let c10 = ''; for (let i = 0; i < 40; i++){ await sleep(250); c10 = await text('#cfg'); if (c10.includes('연결되었습니다') || c10.includes('aibar err')) break; }
  ok('저장하고 연결 시험을 누르면 엔드포인트에 닿는다', c10.includes('연결되었습니다 · 모델 house-model') && c10.includes('켜져 있습니다') && heads[0] === 'Bearer test-key-ui', c10.slice(0, 400));
  ok('키는 화면으로 돌아오지 않는다', await ev(`!JSON.stringify(__T.API.st).includes('test-key-ui') && document.querySelector('#ai-key').value === '' && __T.API.st.ai.has_key === true`));
  ok('AI 가 켜지면 검색창이 경로 추적을 알린다', await ev(`__T.AI.state === 'on' && __T.AI.local === true`) && (await ev(`document.querySelector('#q').placeholder`)).includes('AI가 경로를 추적'));
  await tap('#cfg [data-c="close"]'); await sleep(300);
  const ask = async q => { await ev(`(() => { const q = document.querySelector('#q'); q.focus(); q.value = ${JSON.stringify(q)}; q.dispatchEvent(new KeyboardEvent('keydown', {key:'Enter', bubbles:true})); })()`);
    for (let i = 0; i < 60; i++){ await sleep(250); if (await ev(`!!(__T.state.trace && __T.state.trace.sc.ai && __T.state.trace.sc.q === ${JSON.stringify(q)}) || !!(__T.state.hits && __T.state.hits.aiMsg)`)) break; } };
  let p0 = prompts.length;
  await ask('예약 취소가 안 된다는 문의');
  ok('TRACE 에 적은 문의를 AI 가 추적해 경로로 보여 준다', await ev(`!!(__T.state.trace && __T.state.trace.sc.ai) && __T.state.trace.steps.length >= 2`) && (await text('#trace')).includes('모의 엔드포인트가 만든 요약입니다') && prompts.length === p0 + 2,
     (await text('#trace')).slice(0, 300) + ' / 요청 ' + (prompts.length - p0));
  ok('보낸 글에 문의와 구조 정보가 들어 있고, 서버 구성에만 적은 주소는 없다', prompts.slice(p0).every(p => p.includes('예약 취소가 안 된다는 문의')) && prompts[p0].includes('코드 유닛 목록') && !prompts.some(p => p.includes('10.9.0.5')), prompts.slice(p0).map(p => p.length).join(','));
  await snap('10b-ai-trace');
  await ev(`__T.AI.max = 1500`);          // 지도가 한 번에 보낼 크기보다 큰 경우를 흉내 낸다
  p0 = prompts.length;
  await ask('결제가 두 번 됐다는 문의');
  ok('지도가 크면 모듈 단계로 먼저 좁혀 나눠 보낸다', prompts.length === p0 + 3 && prompts[p0].includes('모듈(폴더·패키지) 목록') && prompts[p0 + 1].includes('코드 유닛 목록') && prompts.slice(p0).every(p => p.length < 6000) && await ev(`!!(__T.state.trace && __T.state.trace.sc.ai)`),
     prompts.slice(p0).map(p => p.length).join(',') + ' / ' + (await text('#trace')).slice(0, 200));
  await ev(`document.querySelector('#trace [data-act="clear"]').click()`); await sleep(300);
  if (await ev('__T.state.nav')){ await tap('#level .lvnav'); await sleep(300); }       // 다음 단계가 설정 메뉴를 새로 열 수 있게 닫아 둔다
  mock.close();

  // 8) 자동 정렬로 되돌리기
  await tap('#level .lvnav'); await sleep(300);
  await tap('#trace [data-act="arrange-reset"]'); await sleep(400);
  ok('자동 정렬은 한 번 더 눌러야 실행된다', (await text('#trace')).includes('한 번 더 누르면') && (await spots()).web[0] === moved[0]);
  await tap('#trace [data-act="arrange-reset"]'); await sleep(1300);
  ok('자동 정렬을 하면 전체를 다시 놓고 그 자리를 기억한다', await ev(`__T.SYSL.filter(s => !s.base).every(s => s.x === s.ax && s.y === s.ay)`) && (readNotes().positions || {}).web[0] === await ev('Math.round(__T.SYS.web.ax)') && (await apart()).length === 0);

  // 9) 휴대폰 폭
  await send('Emulation.setDeviceMetricsOverride', {width:390, height:844, deviceScaleFactor:2, mobile:true});
  await send('Page.reload'); await ready('__T.UNITS.length > 20');
  await tap('#level .lvnav'); await sleep(400);
  ok('휴대폰 폭에서도 설정 메뉴가 열린다', (await text('#trace')).includes('시스템으로 이동') && await ev(`document.querySelector('#insp').hidden`));
  await snap('7-phone-list');
  await tap('#trace [data-act="cfg"]'); await sleep(500);
  ok('휴대폰 폭에서 설정 패널이 화면 안에 들어온다', await ev(`(() => { const r = document.querySelector('#cfgbox').getBoundingClientRect(); return r.left >= 0 && r.right <= innerWidth + 1; })()`));
  await snap('8-phone-settings');

  ok('부르는 곳이 없는 서버에만 끊김 표시가 붙는다', await ev(`__T.SYSL.filter(s => s.cut).map(s => s.id).join(',') === 'push-api' && __T.SYS['push-api'].cut.kind === 'no-callers'`), await ev(`__T.SYSL.filter(s => s.cut).map(s => s.id).join(',')`));
  ok('화면에서 난 오류가 없다', errors.length === 0, errors.slice(0, 3).join(' | '));
  console.log(out.join('\n'));
  const bad = out.filter(l => l.includes('실패')).length;
  console.log(bad ? `실패 ${bad}건` : '통과');
  console.log('그림: ' + shots);
  if (!process.argv[2]){ /* 그림 폴더를 따로 주지 않았으면 임시 폴더와 함께 지운다 */ }
  done(bad ? 1 : 0);
})().catch(e => { console.log(out.join('\n')); console.log('중단: ' + e.message); done(1); });
