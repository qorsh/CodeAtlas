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

    print('4) 서버 주소 지정')
    edit(cfg, lambda d: (d['link'].update(never={}), [s.update(hosts=['payment-service']) for s in d['systems'] if s['id'] == 'payment-service']))
    atlas(cfg, 'sync')
    meta, units, http = load(ws)
    seen = {h['host']: h for h in meta.get('hosts_seen', [])}
    check('주소의 주인이 표시된다 (포트가 달라도)', seen.get('payment-service:8000', {}).get('owner') == 'payment-service', seen)

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

print('실패 %d건: %s' % (len(fails), ', '.join(fails)) if fails else '통과')
sys.exit(1 if fails else 0)
