#!/usr/bin/env python3
"""여러 제품을 한 지도에 넣었을 때 호출이 맞는 서버에 붙는지 본다.

  python3 tests/link_check.py

시험용 프로젝트 셋(fixtures/shop · fixtures/booking · fixtures/backoffice)을 임시 작업 공간에 넣고 분석한다.
  · 쇼핑 주문 서버와 예약 서버에는 같은 경로(GET /api/orders/{id})가 있다. 각 제품의 웹은 자기 서버에 붙어야 한다.
  · 관리자 화면은 그 경로만 부른다. 근거가 없으니 선을 긋지 않고 '확인이 필요한 호출'로 남아야 한다. 설정에 적으면 풀린다.
  · 결제 서버는 두 제품이 함께 쓴다. 지도에는 하나만 있어야 한다.
  · 레거시 ERP 는 소스가 없다. 등록하기 전에는 '지도에 없는 주소'로, 등록한 뒤에는 시스템 하나로 나와야 한다.
"""
import json
import os
import subprocess
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
fails = []


def check(name, ok, detail=''):
    print(('  통과  ' if ok else '  실패  ') + name + (f'  ← {detail}' if detail and not ok else ''))
    if not ok:
        fails.append(name)


def atlas(cfg, *args):
    r = subprocess.run([sys.executable, os.path.join(REPO, 'atlas', 'atlas.py'), '--config', cfg, *args], capture_output=True, text=True)
    if r.returncode:
        raise SystemExit(f"atlas.py {' '.join(args)} 실패\n{r.stdout}\n{r.stderr}")
    return r.stdout


def load(ws):
    recs = json.load(open(os.path.join(ws, 'atlas-data.json'), encoding='utf-8'))
    units = [r for r in recs if r['rt'] == 'unit']
    http = {}
    for e in recs:
        if e['rt'] == 'edge' and e['k'] == 'http':
            http.setdefault((units[e['s']]['sys'], units[e['t']]['sys']), []).extend(e['routes'])
    return recs[0], units, http


def edit(cfg, fn):
    doc = json.load(open(cfg, encoding='utf-8'))
    fn(doc)
    json.dump(doc, open(cfg, 'w', encoding='utf-8'), ensure_ascii=False, indent=2)


with tempfile.TemporaryDirectory() as ws:
    cfg = os.path.join(ws, 'atlas.config.json')
    atlas(cfg, 'init', 'link-check')
    for name in ('shop', 'booking', 'backoffice'):
        atlas(cfg, 'add', os.path.join(REPO, 'fixtures', name))
    dup = subprocess.run([sys.executable, os.path.join(REPO, 'atlas', 'atlas.py'), '--config', cfg, 'add', os.path.join(REPO, 'fixtures', 'shop'), '--name', 'again'], capture_output=True, text=True)
    check('같은 폴더를 두 번 넣으면 막는다', dup.returncode != 0 and '이미 추가된' in dup.stderr + dup.stdout)

    print('1) 설정 없이')
    atlas(cfg, 'sync')
    meta, units, http = load(ws)
    check('쇼핑 웹 → 주문 서버 (겹치는 경로 포함)', 'GET /api/orders/{id}' in http.get(('web', 'order-service'), []), http.get(('web', 'order-service')))
    check('쇼핑 웹은 예약 서버에 붙지 않는다', ('web', 'booking-server') not in http)
    check('예약 웹 → 예약 서버 (겹치는 경로 포함)', 'GET /api/orders/{order_id}' in http.get(('booking-web', 'booking-server'), []), http.get(('booking-web', 'booking-server')))
    check('예약 웹은 주문 서버에 붙지 않는다', ('booking-web', 'order-service') not in http)
    check('주소를 고르는 식으로 시작하는 호출도 잡는다 (`${base()}/…`)', 'DELETE /api/bookings/{booking_id}' in http.get(('booking-web', 'booking-server'), []), http.get(('booking-web', 'booking-server')))
    check('관리자 화면은 어느 서버에도 붙지 않는다', not any(a == 'backoffice' for a, _ in http), [k for k in http if k[0] == 'backoffice'])
    pend = [p for p in meta.get('pending', []) if units[p['u']]['sys'] == 'backoffice']
    check('관리자 화면의 호출이 확인 필요로 남는다', len(pend) == 1 and sorted(c[0] for c in pend[0]['cands']) == ['booking-server', 'order-service'], meta.get('pending'))
    check('결제 서버는 하나이고 두 제품이 부른다', ('order-service', 'payment-service') in http and ('booking-server', 'payment-service') in http
          and sum(1 for s in meta['systems'] if s['id'].endswith('payment-service')) == 1)
    off = {o['host']: o for o in meta.get('offmap', [])}
    check('레거시 주소가 지도에 없는 주소로 잡힌다', 'erp.legacy.example' in off and sorted(off['erp.legacy.example']['systems']) == ['booking-server', 'booking-web'], meta.get('offmap'))

    print('2) 레거시 등록 + 관리자 화면이 부르는 서버 지정')
    edit(cfg, lambda d: (d.update(external=[dict(id='erp', name='레거시 ERP', hosts=['erp.legacy.example'])]), d['link'].update(calls={'backoffice': ['booking-server']})))
    out = atlas(cfg, 'sync')
    meta, units, http = load(ws)
    erp = [u for u in units if u['sys'] == 'erp']
    check('레거시가 시스템 하나, 유닛 하나로 나온다', len(erp) == 1 and any(s['id'] == 'erp' and s.get('ext') for s in meta['systems']))
    apis = sorted(m['route'] for m in (erp[0].get('m', []) if erp else []))
    check('레거시 유닛에 우리가 부르는 API 가 모인다', apis == ['GET /erp/notices', 'GET /erp/rooms', 'GET /erp/rooms/{room_id}', 'POST /erp/reservations'], apis)
    check('예약 서버와 예약 웹이 같은 레거시로 이어진다', ('booking-server', 'erp') in http and ('booking-web', 'erp') in http, [k for k in http if k[1] == 'erp'])
    check('등록한 주소는 지도에 없는 주소에서 빠진다', 'erp.legacy.example' not in [o['host'] for o in meta.get('offmap', [])], meta.get('offmap'))    # tree-sitter 가 없으면 건너뛴 Go 서버의 주소가 남는 것은 맞는 동작이다
    check('관리자 화면 → 예약 서버 (설정)', http.get(('backoffice', 'booking-server')) == ['GET /api/orders/{order_id}'] and not meta.get('pending'), (http.get(('backoffice', 'booking-server')), meta.get('pending')))
    check('실행 결과에 레거시 호출 수가 나온다', '레거시 레거시 ERP: 이쪽으로 보내는 호출 4개' in out, out[-400:])

    print('3) 잇지 않을 서버 지정')
    edit(cfg, lambda d: d['link'].update(calls={}, never={'backoffice': ['booking-server']}))
    atlas(cfg, 'sync')
    meta, units, http = load(ws)
    check('관리자 화면 → 주문 서버 (예약 서버를 뺐으므로)', ('backoffice', 'order-service') in http and ('backoffice', 'booking-server') not in http, [k for k in http if k[0] == 'backoffice'])

    print('4) 서버 주소 지정: 개발·운영 서버는 코드가 같고 주소만 다르다')
    edit(cfg, lambda d: (d['link'].update(never={}), [s.update(hosts=['payment-service']) for s in d['systems'] if s['id'] == 'payment-service'],
                         [s.update(hosts=['api.booking.example', 'dev-api.booking.example']) for s in d['systems'] if s['id'] == 'booking-server']))
    out = atlas(cfg, 'sync')
    meta, units, http = load(ws)
    seen = {h['host']: h for h in meta.get('hosts_seen', [])}
    check('주소의 주인이 표시된다 (포트가 달라도)', seen.get('payment-service:8000', {}).get('owner') == 'payment-service', seen)
    check('개발 주소와 운영 주소가 같은 서버 하나를 가리킨다', seen.get('dev-api.booking.example', {}).get('owner') == 'booking-server' and seen.get('api.booking.example', {}).get('owner') == 'booking-server'
          and sum(1 for s in meta['systems'] if s['id'] == 'booking-server') == 1, seen)

with tempfile.TemporaryDirectory() as ws:
    print('5) 웹과 서버가 저마다 다른 폴더(저장소)일 때')
    cfg = os.path.join(ws, 'atlas.config.json')
    atlas(cfg, 'init', 'link-check-split')
    for path in ('shop/web', 'shop/order-service', 'booking/booking-web', 'booking/booking-server'):
        atlas(cfg, 'add', os.path.join(REPO, 'fixtures', path))
    out = atlas(cfg, 'sync')
    meta, units, http = load(ws)
    check('쇼핑 웹 → 주문 서버 (주로 부르는 서버로 판단)', 'GET /api/orders/{id}' in http.get(('web', 'order-service'), []) and ('web', 'booking-server') not in http, sorted(http))
    check('예약 웹 → 예약 서버 (주로 부르는 서버로 판단)', 'GET /api/orders/{order_id}' in http.get(('booking-web', 'booking-server'), []) and ('booking-web', 'order-service') not in http, sorted(http))
    check('판단 근거가 실행 결과에 나온다', '주로 부르는 서버(추정) 2' in out, out[-500:])

def web(folder, name, body):
    os.makedirs(os.path.join(folder, name, 'src'))
    open(os.path.join(folder, name, 'package.json'), 'w').write('{"name": "%s", "dependencies": {"react": "18.3.1"}}' % name)
    open(os.path.join(folder, name, 'src', 'api.ts'), 'w', encoding='utf-8').write(body)


with tempfile.TemporaryDirectory() as ws, tempfile.TemporaryDirectory() as src:
    print('6) 연결 진단과 접두어 맞춤')
    # 게이트웨이가 경로 앞에 /gw 를 붙인다: 호출이 API 보다 길다
    web(src, 'gw-web', """
export const a = (id: number) => fetch(`/gw/api/orders/${id}`);
export const b = () => fetch('/gw/api/orders', { method: 'POST' });
export const c = (id: number) => fetch(`/gw/api/orders/${id}/cancel`, { method: 'POST' });
""")
    # 기준 주소에 /api 가 들어 있다: 호출이 API 보다 짧다
    web(src, 'short-web', """
import axios from 'axios';
const api = axios.create({ baseURL: process.env.API });
export const a = (id: number) => api.get(`/orders/${id}`);
export const b = () => api.post('/orders');
export const c = (id: number) => api.post(`/orders/${id}/cancel`);
""")
    # 어느 API 와도 맞지 않는다
    web(src, 'lost-web', """
import axios from 'axios';
export const a = () => axios.get('/v9/alpha/list');
export const b = () => axios.get('/v9/beta/list');
export const c = () => axios.post('/v9/gamma/save');
""")
    # 조각 하나짜리 경로들: /payments/{id} 처럼 무엇이든 받는 라우트에 접두어를 붙여 맞춰서는 안 된다
    web(src, 'junk-web', """
import axios from 'axios';
export const a = () => axios.get('/aaa');
export const b = () => axios.get('/bbb');
export const c = () => axios.get('/ccc');
""")
    # 서버 주소는 적혀 있는데 호출 방식을 읽지 못하는 앱
    os.makedirs(os.path.join(src, 'quiet-app', 'src', 'main', 'kotlin', 'quiet'))
    open(os.path.join(src, 'quiet-app', 'build.gradle.kts'), 'w').write('')
    open(os.path.join(src, 'quiet-app', 'src', 'main', 'kotlin', 'quiet', 'Net.kt'), 'w', encoding='utf-8').write(
        'package quiet\n\nclass Net {\n    val base = "https://api.shop.example"\n    fun orders() = Request.Builder().url(base + path()).build()\n    fun path() = listOf("orders").joinToString("/")\n}\n')
    cfg = os.path.join(ws, 'atlas.config.json')
    atlas(cfg, 'init', 'link-check-diag')
    atlas(cfg, 'add', os.path.join(REPO, 'fixtures', 'shop', 'order-service'))
    atlas(cfg, 'add', os.path.join(REPO, 'fixtures', 'shop', 'payment-service'))
    for name in ('gw-web', 'short-web', 'lost-web', 'junk-web', 'quiet-app'):
        atlas(cfg, 'add', os.path.join(src, name))
    out = atlas(cfg, 'sync')
    meta, units, http = load(ws)
    diag = json.load(open(os.path.join(ws, 'atlas-manifest.json'), encoding='utf-8'))['summary']['diag']
    by = {x['id']: x for x in diag['systems']}
    pf = {(p['frm'], p['kind'], p['prefix']): p['n'] for p in diag['prefixes']}
    check('호출 앞에 붙은 접두어를 떼고 잇는다 (/gw)', len(http.get(('gw-web', 'order-service'), [])) == 3 and pf.get(('gw-web', 'strip', '/gw')) == 3, (http.get(('gw-web', 'order-service')), pf))
    check('호출에 빠진 접두어를 붙여 잇는다 (/api)', len(http.get(('short-web', 'order-service'), [])) == 3 and pf.get(('short-web', 'add', '/api')) == 3, (http.get(('short-web', 'order-service')), pf))
    check('맞지 않는 호출은 잇지 않고 진단에 남긴다', not any(a == 'lost-web' for a, _ in http) and by['lost-web']['calls'] == 3 and by['lost-web']['linked'] == 0 and by['lost-web']['unmatched'] == 3
          and 'GET /v9/alpha/list' in by['lost-web']['call_ex'], by.get('lost-web'))
    check('무엇이든 받는 라우트에는 접두어를 붙여 맞추지 않는다', not any(a == 'junk-web' for a, _ in http) and not any(p['frm'] == 'junk-web' for p in diag['prefixes']), ([k for k in http if k[0] == 'junk-web'], diag['prefixes']))
    check('주소는 적혀 있는데 호출이 안 잡힌 앱을 알린다', by['quiet-app']['calls'] == 0 and by['quiet-app']['hosts'] == 1 and '호출 방식을 아직 읽지 못하는 것으로 보입니다' in out, (by.get('quiet-app'), out[-600:]))
    check('시스템별 집계가 맞다', by['order-service']['routes'] == 3 and by['gw-web']['linked'] == 3 and by['short-web']['linked'] == 3, by)
    check('실행 결과에 진단이 나온다', '연결 진단' in out and '어느 API 와도 경로가 맞지 않습니다' in out and '/gw 를 떼면' in out, out[-700:])
    full = atlas(cfg, 'diagnose')
    check('diagnose 명령이 같은 내용을 보여 준다', '못 이은 호출 예' in full and 'API 예' in full, full[-400:])

with tempfile.TemporaryDirectory() as ws, tempfile.TemporaryDirectory() as src:
    web(src, 'only-web', """
import axios from 'axios';
export const a = () => axios.get('/api/one');
export const b = () => axios.get('/api/two');
export const c = () => axios.get('/api/three');
""")
    cfg = os.path.join(ws, 'atlas.config.json')
    atlas(cfg, 'init', 'link-check-noroutes')
    atlas(cfg, 'add', os.path.join(src, 'only-web'))
    out = atlas(cfg, 'sync')
    check('서버 API 를 하나도 못 읽으면 그렇게 알린다', '서버가 내주는 API 를 하나도 읽지 못했습니다' in out, out[-500:])

def put(path, text=''):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    open(path, 'w', encoding='utf-8').write(text)


with tempfile.TemporaryDirectory() as ws, tempfile.TemporaryDirectory() as src:
    print('7) 한 폴더에 서버·Android·iOS 가 함께 있고, 앱이 경로를 문자열로 들고 있는 경우')
    put(os.path.join(src, 'Server', 'pom.xml'), '<project/>')
    put(os.path.join(src, 'Server', 'src', 'main', 'java', 'demo', 'board', 'BoardController.java'), """package demo.board;

import org.springframework.stereotype.Controller;
import org.springframework.web.bind.annotation.RequestMapping;

@Controller
public class BoardController {
    @RequestMapping("/board/listData.do")
    public String listData() { return "jsonView"; }

    @RequestMapping(value = "/board/viewData.do")
    public String viewData() { return "jsonView"; }

    @RequestMapping("/board/saveAction.do")
    public String saveAction() { return "jsonView"; }

    @RequestMapping("/user/loginAction.do")
    public String loginAction() { return "jsonView"; }
}
""")
    put(os.path.join(src, 'Android', 'build.gradle'))
    put(os.path.join(src, 'Android', 'app', 'src', 'main', 'AndroidManifest.xml'), '<manifest/>')
    put(os.path.join(src, 'Android', 'app', 'src', 'main', 'java', 'demo', 'app', 'BoardApi.java'), """package demo.app;

import java.util.Map;

public class BoardApi {
    static final String SERVER_URL = "https://m.demo.example";
    static final String URL_LOGIN = "/user/loginAction.do";

    String listUrl(Map<String, String> params) {
        String menuId = params.get("menuId");
        String sysId = params.get("sysId");
        String deptId = params.get("deptId");
        return SERVER_URL + "/board/listData.do?menuId=" + menuId + "&sysId=" + sysId + "&deptId=" + deptId;
    }

    String viewUrl(Map<String, String> params) {
        return SERVER_URL + "/board/viewData.do" + "?docId=" + params.get("docId");
    }

    String loginUrl() { return SERVER_URL + URL_LOGIN; }
}
""")
    put(os.path.join(src, 'iOS', 'Demo.xcodeproj', 'project.pbxproj'))
    put(os.path.join(src, 'iOS', 'Demo', 'BoardAPI.swift'), """import Foundation

struct ServerInfo {
    static let baseURL = "https://m.demo.example"
}

enum BoardPath {
    static let list = "board/listData.do"
    static let save = "/board/saveAction.do"
}

final class BoardAPI {
    func loadList(completion: @escaping (Data?) -> Void) {
        let url = URL(string: "\\(ServerInfo.baseURL)/\\(BoardPath.list)")!
        URLSession.shared.dataTask(with: url) { data, _, _ in completion(data) }.resume()
    }

    func login() {
        var request = URLRequest(url: URL(string: ServerInfo.baseURL + "/user/loginAction.do")!)
        request.httpMethod = "POST"
        URLSession.shared.dataTask(with: request).resume()
    }
}
""")
    cfg = os.path.join(ws, 'atlas.config.json')
    atlas(cfg, 'init', 'link-check-mobile')
    atlas(cfg, 'add', src, '--name', 'mono')
    out = atlas(cfg, 'sync')
    meta, units, http = load(ws)
    by = {x['id']: x for x in json.load(open(os.path.join(ws, 'atlas-manifest.json'), encoding='utf-8'))['summary']['diag']['systems']}
    check('Android: 주소에 더해 쓰는 경로와 상수에 둔 경로가 서버 API 에 이어진다', sorted(http.get(('android', 'server'), [])) == ['ALL /board/listData.do', 'ALL /board/viewData.do', 'ALL /user/loginAction.do'], http.get(('android', 'server')))
    check('Android: map.get("menuId") 같은 것은 호출로 세지 않는다', by['android']['calls'] == 3 and by['android']['unmatched'] == 0, by.get('android'))
    check('iOS: 상수에 모아 둔 경로(슬래시 없이 시작하는 것 포함)가 서버 API 에 이어진다', sorted(http.get(('ios', 'server'), [])) == ['ALL /board/listData.do', 'ALL /board/saveAction.do', 'ALL /user/loginAction.do'], http.get(('ios', 'server')))
    check('진단에 문제가 없다고 나온다', '연결 진단' not in out, out[-500:])
    # 예전에 등록된 앱(그물이 꺼져 있던 설정)도 다음 분석 때 켜진다
    edit(cfg, lambda d: [s.update(rules=[r for r in s['rules'] if r != 'client-slash-literal']) for s in d['systems'] if s.get('rules')])
    out = atlas(cfg, 'sync')
    meta, units, http = load(ws)
    check('예전에 등록된 앱에도 다음 분석 때 적용된다', '경로 문자열도 호출 후보로 읽게 했습니다' in out and ('ios', 'server') in http and ('android', 'server') in http, out[-500:])

with tempfile.TemporaryDirectory() as ws, tempfile.TemporaryDirectory() as src:
    # 앱이 부르는 경로가 서버에 없는 경우: 경로처럼 생긴 문자열을 예로 보여 줘야 한다
    put(os.path.join(src, 'Server', 'pom.xml'), '<project/>')
    put(os.path.join(src, 'Server', 'src', 'main', 'java', 'demo', 'A.java'), 'package demo;\nimport org.springframework.web.bind.annotation.*;\n@RestController\npublic class A {\n    @GetMapping("/admin/list.do")\n    public String list() { return ""; }\n'
        '    @GetMapping("/admin/view.do")\n    public String view() { return ""; }\n    @PostMapping("/admin/save.do")\n    public String save() { return ""; }\n}\n')
    put(os.path.join(src, 'iOS', 'Demo.xcodeproj', 'project.pbxproj'))
    put(os.path.join(src, 'iOS', 'Demo', 'Paths.swift'), 'enum Paths {\n    static let base = "https://m.demo.example"\n    static let a = "/mobile/main.do"\n    static let b = "/mobile/login.do"\n    static let c = "/mobile/notice/list.do"\n}\n')
    cfg = os.path.join(ws, 'atlas.config.json')
    atlas(cfg, 'init', 'link-check-mobile-miss')
    atlas(cfg, 'add', src, '--name', 'mono')
    out = atlas(cfg, 'sync')
    check('앱의 경로가 서버에 없으면 경로 문자열의 예를 보여 준다', '경로로 보이는 문자열이 3개 있지만 어느 API 와도 맞지 않습니다' in out and '/mobile/main.do' in out, out[-700:])
    finds = {f['id']: f['kind'] for f in json.load(open(os.path.join(ws, 'atlas-manifest.json'), encoding='utf-8'))['summary']['findings']}
    check('요청을 처리하는 곳은 있는데 부르는 곳이 없는 서버를 알린다', finds.get('server') == 'no-callers' and finds.get('ios') == 'maybe' and 'API 3개를 내주는데 부르는 곳을 하나도 찾지 못했습니다' in out, (finds, out[-500:]))

print('실패 %d건: %s' % (len(fails), ', '.join(fails)) if fails else '통과')
sys.exit(1 if fails else 0)
