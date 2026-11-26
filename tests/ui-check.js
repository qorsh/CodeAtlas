#!/usr/bin/env node
/* 실제 Chrome 으로 화면을 눌러 보는 시험. 로컬 서버(atlas.py open)를 띄우고 진짜 마우스 입력을 넣는다.

     node tests/ui-check.js [그림을 둘 폴더]

   시험용 프로젝트 셋(shop · booking · backoffice)을 임시 작업 공간에 넣고 다음을 본다.
     · 시스템 목록에서 눌러 이동 · 배치 편집으로 시스템을 끌어 옮기고 저장 · 확인이 필요한 호출 목록
     · 설정 패널에서 레거시 등록, 부르는 서버 지정, 다시 분석, 폴더 추가
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
setTimeout(() => { console.log(out.join('\n')); console.log('시간 초과'); done(2); }, 150000);

(async () => {
  atlas('init', 'ui-check');
  for (const n of ['shop', 'booking', 'backoffice']) atlas('add', path.join(REPO, 'fixtures', n));
  atlas('sync', '--no-fetch');
  // 나중에 화면에서 추가해 볼 폴더
  const extra = path.join(tmp, 'more', 'extra-web'); fs.mkdirSync(path.join(extra, 'src'), {recursive:true});
  fs.writeFileSync(path.join(extra, 'package.json'), '{"name": "extra-web", "dependencies": {"react": "18.3.1"}}');
  fs.copyFileSync(path.join(REPO, 'fixtures', 'booking', 'booking-web', 'src', 'api.ts'), path.join(extra, 'src', 'api.ts'));

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
  await send('Runtime.enable'); await send('Page.enable');

  // 1) 열기
  ok('열린다: 유닛이 있고 카메라 값이 정상', await ready('__T.UNITS.length > 20 && isFinite(__T.cam.z) && isFinite(__T.cam.x)'), await ev('JSON.stringify(window.__T ? __T.cam : null)'));
  ok('로컬 서버로 열면 설정 기능이 켜진다', await ev('__T.API.ok'));
  const pb = await text('#pendbox');
  ok('확인이 필요한 호출과 지도에 없는 주소를 알린다', pb.includes('확인이 필요한 호출 1') && pb.includes('지도에 없는 주소'), pb);
  await snap('1-overview');

  // 2) 시스템 목록에서 눌러 이동
  await tap('#level .lvnav');
  ok('시스템 목록이 열린다', (await text('#trace h2')).startsWith('시스템 ') && await ev(`document.querySelectorAll('#navlist .srow').length`) >= 7, await text('#trace h2'));
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
  await drag(p[0], p[1], p[0] - 90, p[1] + 150);
  const moved = await ev(`(() => { const T = __T, s = T.SYS.web, u = T.UNITS.find(u => u.sys === 'web'); return [s.x, s.y, s.moved, T.cam.x, u.x, u.x > s.x && u.x < s.x + s.w]; })()`);
  ok('배치 편집 중에는 끌면 시스템이 움직인다', moved[2] === true && (moved[0] !== p[2] || moved[1] !== p[3]) && moved[3] === p[4], JSON.stringify([p, moved]));
  ok('안에 든 유닛도 함께 움직인다', moved[4] !== p[5] && moved[5] === true);
  await snap('3-arrange');
  await tap('#walk [data-act="arrange"]'); await sleep(900);
  const notes = JSON.parse(fs.readFileSync(path.join(ws, 'atlas-notes.json'), 'utf8'));
  ok('옮긴 자리가 작업 공간에 저장된다', !!notes.positions && notes.positions.web && notes.positions.web[0] === moved[0] && notes.positions.web[1] === moved[1], JSON.stringify(notes));
  await send('Page.reload'); await ready('__T.UNITS.length > 20');
  ok('다시 열어도 옮긴 자리에 있다', await ev(`__T.SYS.web.x === ${moved[0]} && __T.SYS.web.y === ${moved[1]} && __T.SYS.web.moved === true`));

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
  await tap('#cfg [data-c="ext-from"]'); await sleep(300);
  ok('레거시로 등록을 누르면 주소가 채워진다', await ev(`document.querySelector('#x-hosts').value`) === 'erp.legacy.example');
  await ev(`document.querySelector('#x-name').focus()`); await send('Input.insertText', {text:'레거시 ERP'});
  await tap('#cfg [data-c="ext-add"]'); await sleep(500);
  const c1 = await text('#cfg');
  ok('레거시가 등록되고 다시 분석하라고 알린다', c1.includes('레거시 ERP') && c1.includes('설정이 바뀌었습니다'), c1.slice(0, 300));
  await tap('#cfg [data-c="link"][data-to="booking-server"]'); await sleep(500);
  ok('부르는 서버를 정하면 목록에 남는다', (await text('#cfg')).includes('애매하면 이 서버로 본다'));
  await snap('5-settings');
  await tap('#cfg-status [data-c="sync"]');
  ok('다시 분석이 끝나면 지도가 새로 열린다', await ready(`__T.META.systems.some(s => s.ext)`, 40000), await text('#cfg').catch(() => ''));
  ok('레거시가 지도에 시스템으로 나온다', await ev(`(() => { const s = __T.SYS.erp; return !!s && s.ext === true && s.w > 0 && isFinite(s.x) && s.units.length === 1 && s.units[0].m.length === 4; })()`));
  ok('두 시스템이 같은 레거시로 이어진다', await ev(`(() => { const T = __T, from = new Set(T.EDGES.filter(e => e.g === 'http' && T.UNITS[e.t].sys === 'erp').map(e => T.UNITS[e.s].sys)); return from.has('booking-server') && from.has('booking-web'); })()`));
  ok('확인이 필요한 호출이 없어지고 관리자 화면이 예약 서버로 이어진다', await ev(`!document.querySelector('#pendbox').innerText.includes('확인이 필요한 호출') && __T.EDGES.some(e => e.g === 'http' && __T.UNITS[e.s].sys === 'backoffice' && __T.UNITS[e.t].sys === 'booking-server')`), await text('#pendbox'));
  ok('옮겨 둔 자리는 분석 뒤에도 그대로', await ev(`__T.SYS.web.x === ${moved[0]} && __T.SYS.web.moved === true`));
  await send('Input.dispatchKeyEvent', {type:'keyDown', key:'Escape', code:'Escape', windowsVirtualKeyCode:27}); await send('Input.dispatchKeyEvent', {type:'keyUp', key:'Escape', code:'Escape', windowsVirtualKeyCode:27});
  await tap('#level [data-lv="0"]'); await sleep(1400);
  await snap('6-legacy');

  // 7) 설정 패널: 폴더 추가
  await tap('#level .lvnav'); await tap('#trace [data-act="cfg"]'); await sleep(500);
  await tap('#cfg [data-c="open-add"]'); await sleep(500);
  await ev(`document.querySelector('#add-path').value = ${JSON.stringify(path.dirname(extra))}`);
  await tap('#cfg [data-c="ls-go"]'); await sleep(500);
  ok('폴더 목록이 나온다', (await text('#cfg .dirs')).includes('extra-web'), await text('#cfg .dirs'));
  await tap('#cfg .dirs [data-v="extra-web"]'); await sleep(500);
  await tap('#cfg [data-c="preview"]'); await sleep(600);
  ok('미리보기가 시스템을 알아본다', (await text('#cfg')).includes('extra-web') && (await text('#cfg')).includes('추가하고 분석'), (await text('#cfg')).slice(0, 300));
  await tap('#cfg [data-c="add"]');
  ok('추가하면 분석까지 하고 새 시스템이 지도에 나온다', await ready(`!!__T.SYS['extra-web'] && __T.SYS['extra-web'].w > 0`, 40000), await text('#cfg').catch(() => ''));
  ok('새 시스템은 옮겨 둔 시스템과 겹치지 않는다', await ev(`(() => { const T = __T, a = T.SYS['extra-web'], b = T.SYS.web; return !(a.x < b.x + b.w && a.x + a.w > b.x && a.y < b.y + b.h && a.y + a.h > b.y); })()`));
  ok('경로가 같은 호출은 틀린 서버에 붙이지 않는다', await ev(`!__T.EDGES.some(e => e.g === 'http' && __T.UNITS[e.s].sys === 'extra-web' && __T.UNITS[e.t].sys === 'order-service')`));

  // 8) 자동 정렬로 되돌리기
  await tap('#level .lvnav'); await sleep(300);
  await tap('#trace [data-act="arrange-reset"]'); await sleep(1200);
  ok('자동 정렬로 되돌리면 저장한 자리도 지워진다', await ev(`!__T.SYS.web.moved && !__T.POS`) && !JSON.parse(fs.readFileSync(path.join(ws, 'atlas-notes.json'), 'utf8')).positions);

  // 9) 휴대폰 폭
  await send('Emulation.setDeviceMetricsOverride', {width:390, height:844, deviceScaleFactor:2, mobile:true});
  await send('Page.reload'); await ready('__T.UNITS.length > 20');
  await tap('#level .lvnav'); await sleep(400);
  ok('휴대폰 폭에서도 시스템 목록이 열린다', (await text('#trace h2')).startsWith('시스템 ') && await ev(`document.querySelector('#insp').hidden`));
  await snap('7-phone-list');
  await tap('#trace [data-act="cfg"]'); await sleep(500);
  ok('휴대폰 폭에서 설정 패널이 화면 안에 들어온다', await ev(`(() => { const r = document.querySelector('#cfgbox').getBoundingClientRect(); return r.left >= 0 && r.right <= innerWidth + 1; })()`));
  await snap('8-phone-settings');

  ok('화면에서 난 오류가 없다', errors.length === 0, errors.slice(0, 3).join(' | '));
  console.log(out.join('\n'));
  const bad = out.filter(l => l.includes('실패')).length;
  console.log(bad ? `실패 ${bad}건` : '통과');
  console.log('그림: ' + shots);
  if (!process.argv[2]){ /* 그림 폴더를 따로 주지 않았으면 임시 폴더와 함께 지운다 */ }
  done(bad ? 1 : 0);
})().catch(e => { console.log(out.join('\n')); console.log('중단: ' + e.message); done(1); });
