#!/usr/bin/env python3
"""로컬 서버의 설정용 주소(/api/…)를 본다: 토큰·Host 확인, 폴더 추가, 레거시 등록, 부르는 서버 지정, 배치 저장, 다시 분석.

  python3 tests/server_check.py

임시 작업 공간에 시험용 프로젝트를 넣고 서버를 이 프로세스 안에서 띄워 요청을 보낸다. 화면은 띄우지 않는다.
"""
import importlib.util
import json
import os
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
spec = importlib.util.spec_from_file_location('atlas_main', os.path.join(REPO, 'atlas', 'atlas.py'))
atlas = importlib.util.module_from_spec(spec)
spec.loader.exec_module(atlas)
fails = []


def check(name, ok, detail=''):
    print(('  통과  ' if ok else '  실패  ') + name + (f'  ← {detail}' if detail and not ok else ''))
    if not ok:
        fails.append(name)


def put(path, text=''):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    open(path, 'w', encoding='utf-8').write(text)


print('모바일 앱 이름')
with tempfile.TemporaryDirectory() as top:
    # iOS: 표시 이름이 프로젝트 설정에 있다
    put(os.path.join(top, 'a', 'MyShop.xcodeproj', 'project.pbxproj'), 'INFOPLIST_KEY_CFBundleDisplayName = "마이숍";\n')
    put(os.path.join(top, 'a', 'MyShop', 'App.swift'), 'struct App {}\n')
    # iOS: 표시 이름이 다른 값을 가리키면 프로젝트 이름을 쓴다
    put(os.path.join(top, 'b', 'Rider.xcodeproj', 'project.pbxproj'), 'INFOPLIST_KEY_CFBundleDisplayName = "$(PRODUCT_NAME)";\n')
    put(os.path.join(top, 'b', 'Rider', 'App.swift'), 'struct App {}\n')
    # Android: strings.xml 의 app_name
    put(os.path.join(top, 'c', 'build.gradle.kts'))
    put(os.path.join(top, 'c', 'settings.gradle.kts'), 'rootProject.name = "shop-android"\n')
    put(os.path.join(top, 'c', 'app', 'src', 'main', 'AndroidManifest.xml'), '<manifest/>')
    put(os.path.join(top, 'c', 'app', 'src', 'main', 'kotlin', 'shop', 'Main.kt'), 'class Main\n')
    put(os.path.join(top, 'c', 'app', 'src', 'main', 'res', 'values', 'strings.xml'), '<resources><string name="app_name">마이숍</string></resources>')
    # Android: app_name 이 없으면 프로젝트 이름
    put(os.path.join(top, 'd', 'build.gradle'))
    put(os.path.join(top, 'd', 'settings.gradle'), "rootProject.name = 'Courier'\n")
    put(os.path.join(top, 'd', 'app', 'src', 'main', 'AndroidManifest.xml'), '<manifest/>')
    put(os.path.join(top, 'd', 'app', 'src', 'main', 'java', 'x', 'Main.kt'), 'class Main\n')
    names = {k: [y['name'] for y in atlas.detect(os.path.join(top, k), k)] for k in 'abcd'}
    check('iOS: 화면에 보이는 이름 + 플랫폼', names['a'] == ['마이숍 iOS'], names['a'])
    check('iOS: 표시 이름이 변수면 프로젝트 이름', names['b'] == ['Rider iOS'], names['b'])
    check('Android: app_name + 플랫폼', names['c'] == ['마이숍 Android'], names['c'])
    check('Android: app_name 이 없으면 프로젝트 이름', names['d'] == ['Courier Android'], names['d'])
    old = dict(_path=os.path.join(top, 'cfg.json'), _dir=top, sources=[dict(name='a', path=os.path.join(top, 'a')), dict(name='c', path=os.path.join(top, 'c'))],
               systems=[dict(id='ios', name='iOS 앱', source='a', roots=['MyShop']), dict(id='android', name='Android 앱', source='c', roots=['app/src/main/kotlin']),
                        dict(id='a-ios', name='내가 고친 이름', source='a', roots=['MyShop'])])
    check('예전 이름으로 등록된 것은 앱 이름으로 바뀌고, 사람이 고친 이름은 그대로', [x[2] for x in atlas.upgrade_names(old)] == ['마이숍 iOS', '마이숍 Android'] and old['systems'][2]['name'] == '내가 고친 이름', old['systems'])

print('로컬 서버')
with tempfile.TemporaryDirectory() as ws:
    cfg_path = os.path.join(ws, 'atlas.config.json')
    json.dump(dict(name='server-check', out='.', store='atlas-store', api_hosts=[], sources=[], systems=[], link={}), open(cfg_path, 'w', encoding='utf-8'))
    os.makedirs(os.path.join(ws, 'site'))
    open(os.path.join(ws, 'site', 'index.html'), 'w').write('<!doctype html><title>t</title>')
    httpd, url = atlas.make_server(atlas.load_config(cfg_path))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()

    def req(path, body=None, token=None, host=None):
        r = urllib.request.Request(url + path, data=None if body is None else json.dumps(body).encode(), method='GET' if body is None else 'POST')
        r.add_header('Content-Type', 'application/json')
        if token:
            r.add_header('X-Atlas-Token', token)
        if host:
            r.add_header('Host', host)
        try:
            with urllib.request.urlopen(r, timeout=20) as f:
                return f.status, json.loads(f.read() or b'{}')
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b'{}')

    code, st = req('api/state')
    token = st.get('token', '')
    check('상태를 내주고 토큰이 들어 있다', code == 200 and len(token) > 16)
    check('토큰 없이 보낸 요청은 막는다', req('api/ls', {})[0] == 403)
    check('틀린 토큰은 막는다', req('api/ls', {}, token='x' * 24)[0] == 403)
    check('다른 Host 로 온 요청은 막는다 (DNS 리바인딩)', req('api/state', host='evil.example')[0] == 403 and req('api/ls', {}, token=token, host='evil.example:80')[0] == 403)
    post = lambda name, body=None: req('api/' + name, body or {}, token=token)

    code, ls = post('ls', dict(path=os.path.join(REPO, 'fixtures')))
    check('폴더 목록: 이름만 내준다', code == 200 and ls['dirs'] == ['backoffice', 'booking', 'shop'] and 'files' not in ls, ls)
    check('폴더 목록: 하위 폴더로 들어간다', post('ls', dict(path=os.path.join(REPO, 'fixtures'), child='shop'))[1].get('path', '').endswith('fixtures/shop'))
    check('없는 폴더는 이유를 알려 준다', post('ls', dict(path='/no/such/folder'))[0] == 400)

    code, pre = post('add', dict(path=os.path.join(REPO, 'fixtures', 'booking'), dry_run=True))
    check('미리보기: 시스템을 알아보고 설정은 그대로 둔다', code == 200 and [y['id'] for y in pre['systems']] == ['booking-server', 'booking-web', 'booking_app'] and not atlas.load_config(cfg_path)['sources'], pre)
    for name in ('shop', 'booking', 'backoffice'):
        code, out = post('add', dict(path=os.path.join(REPO, 'fixtures', name)))
        check(f'폴더 추가: {name}', code == 200 and out.get('added'), out)
    ls = post('ls', dict(path=os.path.join(REPO, 'fixtures')))[1]
    check('폴더 목록: 이미 추가한 폴더를 표시한다', ls.get('added') == ['backoffice', 'booking', 'shop'] and post('ls', dict(path=os.path.join(REPO, 'fixtures', 'shop')))[1].get('here') == 'shop', ls)
    check('폴더 목록: 윗폴더로 간다', post('ls', dict(path=os.path.join(REPO, 'fixtures', 'shop'), parent=True))[1].get('path', '').endswith('fixtures'))
    code, out = post('add', dict(path=os.path.join(REPO, 'fixtures', 'shop')))
    check('같은 폴더를 두 번 넣으면 막는다', code == 400 and '이미 추가된' in out.get('error', ''), out)

    code, out = post('external', dict(name='레거시 ERP', hosts='erp.legacy.example'))
    check('레거시 등록', code == 200 and out.get('id') == 'erp', out)
    code, out = post('external', dict(name='옛 회원 시스템', paths='/members/**'))
    check('레거시: 한글뿐인 이름이면 id 를 만들어 준다', code == 200 and out.get('id') == 'legacy' and post('external', dict(remove='legacy'))[0] == 200, out)
    check('레거시: 아무 단서도 없으면 막는다', post('external', dict(name='빈 것'))[0] == 400)
    check('레거시: 읽을 수 없는 정규식은 막는다', post('external', dict(name='x', clients='(['))[0] == 400)
    check('부르는 서버 지정', post('link', dict(**{'from': 'backoffice'}, to='booking-server', mode='calls'))[0] == 200
          and atlas.load_config(cfg_path)['link']['calls'] == {'backoffice': ['booking-server']})
    check('없는 시스템은 막는다', post('link', dict(**{'from': 'nope'}, to='booking-server', mode='calls'))[0] == 400)
    check('서버 주소 저장', post('system', dict(id='payment-service', hosts='payment-service, pay.example.com'))[0] == 200
          and next(y for y in atlas.load_config(cfg_path)['systems'] if y['id'] == 'payment-service')['hosts'] == ['payment-service', 'pay.example.com'])
    check('시스템 이름 고치기', post('system', dict(id='mobile', name='쇼핑 앱'))[0] == 200 and next(y for y in atlas.load_config(cfg_path)['systems'] if y['id'] == 'mobile')['name'] == '쇼핑 앱')
    check('배치 저장: 작업 공간 메모에 적힌다', post('layout', dict(positions={'web': [100, -200]}))[1].get('saved') == 1
          and json.load(open(os.path.join(ws, 'atlas-notes.json'), encoding='utf-8'))['positions'] == {'web': [100.0, -200.0]})
    check('배치 지우기', post('layout', dict(positions={}))[1].get('saved') == 0 and 'positions' not in json.load(open(os.path.join(ws, 'atlas-notes.json'), encoding='utf-8')))

    check('다시 분석을 시작한다', post('sync')[1].get('started') is True)
    check('실행 중에는 또 시작하지 않는다', post('sync')[0] == 400 or not post('job')[1]['running'])
    for _ in range(200):
        job = post('job')[1]
        if not job['running']:
            break
        time.sleep(0.1)
    check('분석이 끝난다', job['rc'] == 0, job['log'][-6:])
    recs = json.load(open(os.path.join(ws, 'atlas-data.json'), encoding='utf-8'))
    meta, units = recs[0], [r for r in recs if r['rt'] == 'unit']
    http = {(units[e['s']]['sys'], units[e['t']]['sys']) for e in recs if e['rt'] == 'edge' and e['k'] == 'http'}
    check('결과: 레거시가 지도에 있고 관리자 화면이 예약 서버로 이어진다', any(s.get('ext') for s in meta['systems']) and ('backoffice', 'booking-server') in http and not meta.get('pending'), sorted(http))
    st = req('api/state')[1]
    check('상태에 요약과 레거시 호출 수가 나온다', st['summary'].get('external', {}).get('erp', {}).get('calls') == 4 and len(st['sources']) == 3, st.get('summary'))

    # 읽지 못한 꼴을 규칙으로 만들기
    more = os.path.join(ws, 'more')
    put(os.path.join(more, 'push-api', 'pom.xml'), '<project/>')
    put(os.path.join(more, 'push-api', 'src', 'main', 'java', 'demo', 'PushHandlers.java'),
        'package demo;\n\npublic class PushHandlers {\n' + ''.join(f'    @Endpoint("/push/{n}")\n    public String {n}() {{ return ""; }}\n\n' for n in ('list', 'send', 'read')) + '}\n')
    put(os.path.join(more, 'push-web', 'package.json'), '{"name": "push-web", "dependencies": {"react": "18.3.1"}}')
    put(os.path.join(more, 'push-web', 'src', 'api.ts'), "export const a = () => fetch('/push/list');\nexport const b = () => fetch('/push/send');\nexport const c = () => fetch('/push/read');\n")
    for name in ('push-api', 'push-web'):
        post('add', dict(path=os.path.join(more, name)))

    def resync():
        post('sync')
        for _ in range(300):
            if not post('job')[1]['running']:
                break
            time.sleep(0.1)
        recs = json.load(open(os.path.join(ws, 'atlas-data.json'), encoding='utf-8'))
        us = [r for r in recs if r['rt'] == 'unit']
        return {(us[e['s']]['sys'], us[e['t']]['sys']) for e in recs if e['rt'] == 'edge' and e['k'] == 'http'}

    http = resync()
    st = req('api/state')[1]
    auto = [a for a in st['summary'].get('auto', []) if a['sys'] == 'push-api']
    check('자가 수리: 맞아떨어지는 꼴은 스스로 읽어 잇는다', len(auto) == 1 and auto[0]['name'] == 'Endpoint' and auto[0]['role'] == 'provide' and ('push-web', 'push-api') in http, (st['summary'].get('auto'), sorted(http)))
    check('스스로 읽은 꼴을 끈다', post('auto', dict(id=auto[0]['id'], act='off'))[0] == 200 and req('api/state')[1]['link']['auto_off'] == [auto[0]['id']] and post('auto', dict(id='x', act='off'))[0] == 400)
    http = resync()
    st = req('api/state')[1]
    un = [x for x in st['summary'].get('unread', []) if x['sys'] == 'push-api']
    check('끄면 읽지 못한 꼴로 상태에 나온다', len(un) == 1 and un[0]['kind'] == 'ann' and un[0]['name'] == 'Endpoint' and ('push-web', 'push-api') not in http, st['summary'].get('unread'))
    check('틀린 요청으로는 규칙을 만들지 않는다', post('rule', dict(sys='nope', kind='ann', name='Endpoint', role='provide'))[0] == 400 and post('rule', dict(sys='push-api', kind='ann', name='a b', role='provide'))[0] == 400)
    code, out = post('rule', dict(sys='push-api', kind='ann', name='Endpoint', argi=0, role='provide'))
    made = req('api/state')[1]['custom']
    check('꼴을 골라 규칙을 만든다 (작업 공간의 rules/custom.json)', code == 200 and len(made) == 1 and made[0]['name'] == 'Endpoint' and made[0]['role'] == 'provide' and os.path.exists(os.path.join(ws, 'rules', 'custom.json')), (out, made))
    http = resync()
    st = req('api/state')[1]
    check('다시 분석하면 그 꼴이 API 로 읽혀 이어지고, 읽지 못한 꼴에서 빠진다', ('push-web', 'push-api') in http and not [x for x in st['summary'].get('unread', []) if x['sys'] == 'push-api'], (sorted(http), st['summary'].get('unread')))
    check('만든 규칙을 지운다', post('rule', dict(remove=made[0]['id']))[0] == 200 and not req('api/state')[1]['custom'] and not os.path.exists(os.path.join(ws, 'rules', 'custom.json')))
    post('remove', dict(source='push-api'))
    post('remove', dict(source='push-web'))

    code, out = post('remove', dict(source='backoffice'))
    c = atlas.load_config(cfg_path)
    check('소스 빼기: 설정에서만 빠지고 딸린 연결 지정도 정리된다', code == 200 and out['removed'] == ['backoffice'] and 'backoffice' not in c['link'].get('calls', {})
          and os.path.isdir(os.path.join(REPO, 'fixtures', 'backoffice')), (out, c['link']))
    check('레거시 빼기', post('external', dict(remove='erp'))[0] == 200 and not atlas.load_config(cfg_path)['external'])

    print('서버 구성: 웹서버 · WAS · 주소 묶기')
    conf = lambda: atlas.load_config(cfg_path)
    code, out = post('infra', dict(kind='web', name='앞단 웹서버', product='Nginx', to=['order-service'], path='shop',
                                   instances=[dict(env='운영', host='https://M.Shop.Example/api'), dict(env='개발', host='dev-m.shop.example:8443', note='사내망'), dict(host='')]))
    web_id = out.get('id')
    n = conf()['infra'][0]
    check('웹서버 등록: 붙여 넣은 주소를 정리해 담고 환경·메모를 함께 둔다', code == 200 and n['kind'] == 'web' and n['hosts'] == ['m.shop.example', 'dev-m.shop.example:8443']
          and n['host_info'] == {'m.shop.example': {'env': '운영'}, 'dev-m.shop.example:8443': {'env': '개발', 'note': '사내망'}} and n['path'] == ['/shop'] and n['to'] == ['order-service'], (out, n))
    check('종류가 틀리거나 이름이 없으면 받지 않는다', post('infra', dict(kind='x', name='a'))[0] == 400 and post('infra', dict(kind='web', name=''))[0] == 400 and len(conf()['infra']) == 1)
    check('주소로 읽을 수 없는 글자는 받지 않는다', post('infra', dict(kind='web', name='나쁜 주소', instances=[dict(host='a b<c>')]))[0] == 400 and len(conf()['infra']) == 1)
    code, out = post('infra', dict(kind='was', name='주문 WAS', product='Tomcat', instances=[dict(host='10.0.0.5:8080', note='1호기'), dict(host='10.0.0.6:8080', note='2호기')], to=[web_id, 'no-such-id']))
    was_id = out.get('id')
    check('WAS 등록: 없는 id 는 뒤에 있는 것에서 버린다', code == 200 and was_id and was_id != web_id and conf()['infra'][1]['to'] == [web_id], (out, conf()['infra']))
    code, out = post('infra', dict(id=web_id, kind='web', name='앞단 웹서버', to=[was_id]))
    check('돌고 도는 구성은 받지 않고 그대로 둔다', code == 400 and '돌아옵니다' in out.get('error', '') and conf()['infra'][0]['to'] == ['order-service'], (out, conf()['infra'][0]))
    post('infra', dict(id=was_id, kind='was', name='주문 WAS', product='Tomcat', instances=[dict(host='10.0.0.5:8080', note='1호기'), dict(host='10.0.0.6:8080', note='2호기')], to=['order-service']))
    code, out = post('infra', dict(id=web_id, kind='web', name='앞단 웹서버', product='Nginx', to=[was_id], path='/shop',
                                   instances=[dict(env='운영', host='m.shop.example'), dict(env='개발', host='dev-m.shop.example:8443', note='사내망')]))
    st = req('api/state')[1]
    check('고치기: 웹서버 → WAS → 서버로 잇고, 상태에 서버 구성이 나온다', code == 200 and [x['id'] for x in st['infra']] == [web_id, was_id] and st['infra'][0]['to'] == [was_id] and st['infra_kinds'].get('was') == 'WAS', st.get('infra'))

    code, a1 = post('group', dict(hosts=['pay-a.legacy.example'], name='정산 시스템'))
    code2, a2 = post('group', dict(hosts='pay-b.legacy.example', name='정산 개발'))
    check('주소 묶기: 새 서버(레거시)로 등록한다', code == 200 and code2 == 200 and a1['id'] != a2['id'] and [x['name'] for x in conf()['external']] == ['정산 시스템', '정산 개발'], (a1, a2))
    post('link', {'from': 'web', 'to': a2['id'], 'mode': 'calls'})
    code, out = post('group', dict(instances=[dict(host='pay-b.legacy.example', env='개발'), dict(host='PAY-A.legacy.example', env='운영')], to=a1['id']))
    c = conf()
    check('주소 묶기: 인스턴스를 한 서버로 묶으면 주소가 옮겨 가고, 비게 된 레거시는 합쳐진다', code == 200 and out['merged'] == [a2['id']] and len(c['external']) == 1
          and c['external'][0]['hosts'] == ['pay-a.legacy.example', 'pay-b.legacy.example'] and c['external'][0]['host_info'] == {'pay-a.legacy.example': {'env': '운영'}, 'pay-b.legacy.example': {'env': '개발'}}, (out, c['external']))
    check('합쳐진 레거시를 가리키던 설정은 묶인 서버로 바뀐다', c['link'].get('calls', {}).get('web') == [a1['id']], c['link'])
    code, out = post('group', dict(hosts=['m.shop.example', 'api.shop.example'], to='order-service', env='운영'))
    c = conf()
    osv = [y for y in c['systems'] if y['id'] == 'order-service'][0]
    check('주소 묶기: 지도 안 서버로 묶으면 다른 곳(웹서버)에 있던 주소는 옮겨 온다', code == 200 and out['moved'] == 1 and osv['hosts'] == ['m.shop.example', 'api.shop.example']
          and c['infra'][0]['hosts'] == ['dev-m.shop.example:8443'] and 'm.shop.example' not in c['infra'][0].get('host_info', {}), (out, osv, c['infra'][0]))
    check('주소 묶기: 고른 주소가 없거나 대상이 틀리면 받지 않는다', post('group', dict(hosts=[], to='order-service'))[0] == 400 and post('group', dict(hosts=['x.example'], to='no-such'))[0] == 400
          and post('group', dict(hosts=['x.example']))[0] == 400)
    code, out = post('system', dict(id='order-service', name='주문 서버', instances=[dict(host='api.shop.example', env='운영', note='L4 뒤')]))
    osv = [y for y in conf()['systems'] if y['id'] == 'order-service'][0]
    check('시스템 주소를 인스턴스 줄로 고친다 (환경·메모 포함)', code == 200 and osv['hosts'] == ['api.shop.example'] and osv['host_info'] == {'api.shop.example': {'env': '운영', 'note': 'L4 뒤'}}, osv)
    post('system', dict(id='order-service', name='주문 서버', hosts=''))
    post('infra', dict(id=web_id, kind='web', name='앞단 웹서버', product='Nginx', to=[was_id], path='/shop', instances=[dict(env='운영', host='api.shop.example'), dict(env='개발', host='dev-m.shop.example:8443')]))
    http = resync()
    recs = json.load(open(os.path.join(ws, 'atlas-data.json'), encoding='utf-8'))
    meta = recs[0]
    check('다시 분석하면 웹서버 · WAS 가 지도에 오르고, 들어가는 길이 기록된다', [s['id'] for s in meta['systems'] if s.get('infra')] == [web_id, was_id] and [web_id, was_id, 'order-service'] in meta.get('chains', [])
          and any(v[1] == 'order-service' and v[2] == [web_id, was_id] for v in meta.get('via', [])), (meta.get('chains'), meta.get('via')))
    code, out = post('infra', dict(remove=was_id))
    check('WAS 를 빼면 그것을 가리키던 웹서버의 설정도 정리된다', code == 200 and [x['id'] for x in conf()['infra']] == [web_id] and not conf()['infra'][0].get('to'), conf()['infra'])
    post('external', dict(remove=a1['id']))
    check('레거시를 빼면 그것을 가리키던 설정도 정리된다', not conf().get('external') and 'web' not in conf()['link'].get('calls', {}), conf()['link'])
    post('infra', dict(remove=web_id))

    print('AI 연결')
    import http.server
    import importlib.util
    import stat
    os.environ.pop('ATLAS_AI_KEY', None)
    seen = []

    class Mock(http.server.BaseHTTPRequestHandler):
        """사내 엔드포인트 흉내. /v1/chat/completions (OpenAI 호환) 와 /v1/messages (Claude 형식, 스트리밍) 를 받는다."""

        def log_message(self, *a):
            pass

        def send(self, code, body, kind='application/json'):
            self.send_response(code)
            self.send_header('Content-Type', kind)
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers.get('Content-Length') or 0)) or b'{}')
            seen.append(dict(path=self.path, bearer=self.headers.get('Authorization'), apikey=self.headers.get('api-key'), body=body))
            model = body.get('model')
            if model == 'deny':
                return self.send(401, json.dumps(dict(error=dict(message='bad key'))).encode())
            if self.path.endswith('/messages'):
                if 'thinking' in body and model == 'plain-only':
                    return self.send(400, json.dumps(dict(type='error', error=dict(type='invalid_request_error', message='thinking: Extra inputs are not permitted'))).encode())
                ev = [dict(type='message_start', message=dict(id='msg_1', type='message', role='assistant', model=model, content=[], stop_reason=None, stop_sequence=None, usage=dict(input_tokens=9, output_tokens=0))),
                      dict(type='content_block_start', index=0, content_block=dict(type='text', text='')),
                      dict(type='content_block_delta', index=0, delta=dict(type='text_delta', text='{"units":')), dict(type='content_block_delta', index=0, delta=dict(type='text_delta', text='[1,2]}')),
                      dict(type='content_block_stop', index=0), dict(type='message_delta', delta=dict(stop_reason='refusal' if model == 'filter' else 'end_turn', stop_sequence=None), usage=dict(output_tokens=4)),
                      dict(type='message_stop')]
                return self.send(200, ''.join(f"event: {e['type']}\ndata: {json.dumps(e)}\n\n" for e in ev).encode(), 'text/event-stream')
            if model == 'filter':
                return self.send(200, json.dumps(dict(choices=[dict(message=dict(content=''), finish_reason='content_filter')])).encode())
            self.send(200, json.dumps(dict(model=model, choices=[dict(message=dict(role='assistant', content='{"units":[1,2]}'), finish_reason='stop')], usage=dict(prompt_tokens=5, completion_tokens=3))).encode())

    mock = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Mock)
    threading.Thread(target=mock.serve_forever, daemon=True).start()
    base = f'http://127.0.0.1:{mock.server_address[1]}'
    st = req('api/state')[1]
    check('처음에는 꺼져 있고, 요청을 보내면 설정하라고 알린다', st['ai']['provider'] == '' and not st['ai']['on'] and post('ai', dict(prompt='hi'))[0] == 409 and post('ai', dict(prompt='hi'))[1].get('code') == 'off', st.get('ai'))
    check('모르는 방식·틀린 주소는 받지 않는다', post('ai-config', dict(provider='nope'))[0] == 400 and post('ai-config', dict(provider='openai', base_url='ftp://x'))[0] == 400)
    code, out = post('ai-config', dict(provider='openai', base_url=base + '/v1', model='house-large', quick_model='house-small', key='test-key-abc'))
    st = req('api/state')[1]
    check('사내 엔드포인트 설정: 켜지고, 키는 있다는 것만 알려 준다', code == 200 and out['ai']['on'] and st['ai']['has_key'] and st['ai']['key_from'] == 'file' and 'test-key-abc' not in json.dumps(st) + json.dumps(out), out)
    ai_file = os.path.join(ws, 'atlas-ai.json')
    check('키는 작업 공간 파일에만, 이 사용자만 읽게 둔다', json.load(open(ai_file))['key'] == 'test-key-abc' and (os.name == 'nt' or stat.S_IMODE(os.stat(ai_file).st_mode) == 0o600), oct(os.stat(ai_file).st_mode))
    try:
        urllib.request.urlopen(url + 'atlas-ai.json', timeout=10)
        leaked = True
    except urllib.error.HTTPError as e:
        leaked = e.code != 404
    check('키가 든 설정 파일은 화면 쪽 주소로 내주지 않는다', not leaked)
    code, out = post('ai', dict(prompt='구조 정보와 문의', tier='quick'))
    check('요청을 엔드포인트로 넘기고 답을 돌려준다 (빠른 모델, Bearer 키)', code == 200 and out['text'] == '{"units":[1,2]}' and seen[-1]['path'] == '/v1/chat/completions' and seen[-1]['bearer'] == 'Bearer test-key-abc'
          and seen[-1]['body']['model'] == 'house-small' and seen[-1]['body']['messages'] == [dict(role='user', content='구조 정보와 문의')], (out, seen[-1:]))
    post('ai', dict(prompt='x'))
    check('기본 요청은 기본 모델로 간다', seen[-1]['body']['model'] == 'house-large', seen[-1])
    post('ai-config', dict(auth='api-key'))
    post('ai', dict(prompt='x'))
    check('키를 api-key 헤더로 보내게 바꿀 수 있다', seen[-1]['apikey'] == 'test-key-abc' and not seen[-1]['bearer'], seen[-1])
    code, out = post('ai-test')
    check('연결 시험', code == 200 and out.get('ok') and out.get('model') == 'house-small', out)
    post('ai-config', dict(model='deny'))
    code, out = post('ai', dict(prompt='x'))
    check('키가 틀리면 그렇게 알린다', code == 401 and out.get('code') == 'auth', (code, out))
    post('ai-config', dict(model='filter'))
    code, out = post('ai', dict(prompt='x'))
    check('거절된 답은 거절로 알린다', code == 422 and out.get('code') == 'refused', (code, out))
    check('빈 요청은 보내지 않는다', post('ai', dict(prompt='  '))[0] == 400 and seen[-1]['body']['model'] == 'filter')
    check('토큰 없이는 AI 요청을 받지 않는다', req('api/ai', dict(prompt='x'))[0] == 403)
    post('ai-config', dict(model='house-large', enabled=False))
    n_before = len(seen)
    check('끄면 요청을 보내지 않는다', post('ai', dict(prompt='x'))[1].get('code') == 'off' and len(seen) == n_before and not req('api/state')[1]['ai']['on'])
    post('ai-config', dict(enabled=True, clear_key=True))
    post('ai', dict(prompt='x'))
    check('키를 지우면 키 없이 보낸다', not req('api/state')[1]['ai']['has_key'] and 'key' not in json.load(open(ai_file)) and not seen[-1]['bearer'] and not seen[-1]['apikey'], seen[-1])
    if importlib.util.find_spec('anthropic'):
        post('ai-config', dict(provider='claude', base_url=base, model='claude-opus-5-5', quick_model='', key='test-key-abc'))
        code, out = post('ai', dict(prompt='구조 정보와 문의'))
        b = seen[-1]['body']
        check('Claude 형식(공식 SDK): 스트리밍으로 받고 생각 깊이를 정해 보낸다', code == 200 and out['text'] == '{"units":[1,2]}' and seen[-1]['path'] == '/v1/messages' and b.get('stream') is True
              and b.get('thinking') == {'type': 'adaptive'} and b.get('output_config') == {'effort': 'medium'} and b['max_tokens'] == 64000, (code, out, b))
        post('ai', dict(prompt='x', tier='quick'))
        check('Claude 형식: 빠른 요청은 생각을 얕게', seen[-1]['body'].get('output_config') == {'effort': 'low'}, seen[-1]['body'])
        post('ai-config', dict(model='plain-only'))
        n_before = len(seen)
        code, out = post('ai', dict(prompt='x'))
        check('Claude 형식: 선택 항목을 거절하는 게이트웨이에는 빼고 다시 보낸다', code == 200 and len(seen) == n_before + 2 and 'thinking' not in seen[-1]['body'], (code, out, len(seen) - n_before))
        post('ai-config', dict(model='filter'))
        check('Claude 형식: 거절(refusal)로 끝난 답은 쓰지 않는다', post('ai', dict(prompt='x'))[1].get('code') == 'refused')
    else:
        code, out = post('ai-config', dict(provider='claude', base_url='', model=''))
        code2, out2 = post('ai', dict(prompt='x'))
        check('Claude 방식인데 SDK 가 없으면 설치 방법을 알린다', code == 200 and not out['ai']['on'] and 'requirements-ai.txt' in out['ai']['why'] and code2 == 409 and out2.get('code') == 'no_sdk', (out, out2))
        print('  (anthropic 패키지가 없어 Claude 형식 요청 시험은 건너뜀)')
    mock.shutdown()
    httpd.shutdown()

print('실패 %d건: %s' % (len(fails), ', '.join(fails)) if fails else '통과')
sys.exit(1 if fails else 0)
