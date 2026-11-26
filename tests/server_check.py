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
    check('미리보기: 시스템을 알아보고 설정은 그대로 둔다', code == 200 and [y['id'] for y in pre['systems']] == ['booking-server', 'booking-web'] and not atlas.load_config(cfg_path)['sources'], pre)
    for name in ('shop', 'booking', 'backoffice'):
        code, out = post('add', dict(path=os.path.join(REPO, 'fixtures', name)))
        check(f'폴더 추가: {name}', code == 200 and out.get('added'), out)
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

    code, out = post('remove', dict(source='backoffice'))
    c = atlas.load_config(cfg_path)
    check('소스 빼기: 설정에서만 빠지고 딸린 연결 지정도 정리된다', code == 200 and out['removed'] == ['backoffice'] and 'backoffice' not in c['link'].get('calls', {})
          and os.path.isdir(os.path.join(REPO, 'fixtures', 'backoffice')), (out, c['link']))
    check('레거시 빼기', post('external', dict(remove='erp'))[0] == 200 and not atlas.load_config(cfg_path)['external'])
    httpd.shutdown()

print('실패 %d건: %s' % (len(fails), ', '.join(fails)) if fails else '통과')
sys.exit(1 if fails else 0)
