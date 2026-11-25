#!/usr/bin/env python3
"""코드 아틀라스 — 여러 저장소의 코드를 분석해 하나의 구성도 데이터로 만든다.

  python3 atlas/atlas.py init <이름>                       설정 파일(atlas.config.json) 만들기
  python3 atlas/atlas.py add <폴더 | git 주소> [--name N] [--branch B] [--dry-run]
  python3 atlas/atlas.py sync [--no-fetch] [--pull] [--force]  바뀐 묶음만 다시 분석하고 결과를 교체
  python3 atlas/atlas.py status                            소스별 기준 커밋과 마지막 결과
  python3 atlas/atlas.py site <뷰어 html> <출력 폴더>          사내 웹 서버에 올릴 정적 파일 묶음 만들기

구조 (층마다 따로 늘릴 수 있다)
  langs/*.py     언어 어댑터: 문법만 안다. 유닛(클래스·파일)과 멤버(메서드)의 범위를 낸다.
  engine/*.py    공통 엔진: 이름 풀기, 사실(어노테이션·문자열 인자 호출) 추출, 규칙 적용.
  rules/*.json   규칙 팩: 프레임워크 지식. 사실 → 접점(제공/사용하는 API·테이블·토픽).
  이 파일          설정·소스 관리, 묶음별 캐시, 접점끼리 잇는 연결기, 결과 게시.
묶음(같은 이름공간을 쓰는 시스템들)마다 파일 내용 해시를 기록해, 바뀐 묶음만 다시 분석한다.
연결 단계는 저장된 접점만 보고 다시 맞추므로 소스 코드를 읽지 않는다.
"""
import argparse
import datetime
import glob
import hashlib
import json
import os
import re
import socket
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ENGINE_PRE = ['engine/prelude.py', 'engine/text.py', 'engine/store.py']
ENGINE_POST = ['engine/scan.py', 'engine/facts.py', 'engine/rules.py', 'engine/extras.py', 'engine/export.py']
SKIP_DIRS = {'node_modules', '.git', 'build', '.build', 'dist', '.next', '.spm', 'vendor', '.vinext', '.wrangler', '.sites-runtime',
             'outputs', '.local', '.openai', '.gradle', '.kotlin', 'DerivedData', 'Pods', '.claude', '__pycache__', '.venv', 'venv', 'target', '.idea'}
PSEUDO = [dict(id='device', name='기기 저장소 · OS', sub='단말 안의 저장소와 시스템 서비스'), dict(id='cloud', name='외부 서비스', sub='서드파티 API · 클라우드')]


# ───────────────────────────── 엔진 실행 ─────────────────────────────
def engine_files():
    return ENGINE_PRE + sorted(os.path.relpath(p, HERE) for p in glob.glob(os.path.join(HERE, 'langs', '*.py'))) + ENGINE_POST


def rule_files(cfg=None):
    """내장 규칙 팩 뒤에 작업 공간 규칙 팩(설정 파일 옆 rules/*.json, 또는 설정의 rules_dir)을 붙인다."""
    files = sorted(glob.glob(os.path.join(HERE, 'rules', '*.json')))
    if cfg:
        d = os.path.join(cfg['_dir'], cfg.get('rules_dir', 'rules'))
        if os.path.abspath(d) != os.path.join(HERE, 'rules'):
            files += sorted(glob.glob(os.path.join(d, '*.json')))
    return files


def load_rules(cfg=None):
    """규칙 팩을 읽는다. 팩 id 가 같으면 작업 공간 팩이 내장 팩을 대신하고, 서비스 규칙은 id 가 같으면 나중 것이 이긴다."""
    packs = {}
    for p in rule_files(cfg):
        pack = json.load(open(p, encoding='utf-8'))
        packs.pop(pack['id'], None)
        packs[pack['id']] = pack
    seen = set()
    for pack in reversed(list(packs.values())):
        keep = []
        for R in reversed(pack.get('rules', [])):
            if R.get('type') == 'service':
                if R['id'] in seen:
                    continue
                seen.add(R['id'])
            keep.append(R)
        pack['rules'] = keep[::-1]
    return list(packs.values())


def engine_version(cfg=None):
    h = hashlib.sha1()
    for path in [os.path.join(HERE, rel) for rel in engine_files()] + rule_files(cfg):
        h.update(open(path, 'rb').read())
    return h.hexdigest()[:10]


def run_group(root, systems, opts):
    """묶음 하나를 분석한다. 엔진·어댑터 파일들을 새 이름공간에서 차례로 실행하고 RESULT 를 돌려준다."""
    ns = {'__name__': 'atlas_group', 'ROOT': root, 'SYSTEMS': systems, 'OPTS': opts}
    for rel in engine_files():
        path = os.path.join(HERE, rel)
        exec(compile(open(path, encoding='utf-8').read(), path, 'exec'), ns)
    return ns['RESULT']


# ───────────────────────────── 설정 · 소스 ─────────────────────────────
def load_config(path):
    cfg = json.load(open(path, encoding='utf-8'))
    cfg['_path'] = os.path.abspath(path)
    cfg['_dir'] = os.path.dirname(cfg['_path'])
    cfg.setdefault('sources', [])
    cfg.setdefault('systems', [])
    cfg.setdefault('link', {})
    return cfg


def save_config(cfg):
    out = {k: v for k, v in cfg.items() if not k.startswith('_')}
    tmp = cfg['_path'] + '.tmp'
    json.dump(out, open(tmp, 'w', encoding='utf-8'), ensure_ascii=False, indent=2)
    os.replace(tmp, cfg['_path'])


def store_dir(cfg):
    d = os.path.join(cfg['_dir'], cfg.get('store', 'atlas-store'))
    os.makedirs(os.path.join(d, 'groups'), exist_ok=True)
    return d


def git(root, *args, check=False):
    env = dict(os.environ, GIT_TERMINAL_PROMPT='0')     # 서버 작업에서 비밀번호 입력을 기다리며 멈추지 않게 한다
    r = subprocess.run(['git', '-C', root, *args], capture_output=True, text=True, timeout=600, env=env)
    if check and r.returncode:
        raise SystemExit(f'git {" ".join(args)} 실패 ({root}): {r.stderr.strip()[:300]}')
    return r.stdout.strip()


def source_root(cfg, src):
    if src.get('git'):
        return os.path.join(store_dir(cfg), 'sources', src['name'])
    return os.path.abspath(os.path.expanduser(src['path']))


def prepare_source(cfg, src, fetch=True, pull=False):
    """소스를 분석할 수 있는 상태로 만들고 기준 커밋을 돌려준다. git 주소 소스는 아틀라스 전용 복제본을 쓴다."""
    root, branch = source_root(cfg, src), src.get('branch')
    if src.get('git'):
        if not os.path.isdir(os.path.join(root, '.git')):
            os.makedirs(os.path.dirname(root), exist_ok=True)
            cmd = ['git', 'clone', '--quiet'] + (['--branch', branch] if branch else []) + [src['git'], root]
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=1800, env=dict(os.environ, GIT_TERMINAL_PROMPT='0'))
            if r.returncode:
                raise SystemExit(f'복제 실패 ({src["git"]}): {r.stderr.strip()[:300]}')
        elif fetch:
            git(root, 'fetch', '--quiet', '--prune', 'origin', check=True)
            target = 'origin/' + (branch or git(root, 'rev-parse', '--abbrev-ref', 'origin/HEAD').replace('origin/', '') or 'main')
            git(root, 'checkout', '--quiet', '--detach', target, check=True)      # 전용 복제본이라 원격 끝으로 그대로 옮긴다
    elif pull:
        git(root, 'pull', '--ff-only', '--quiet', check=True)                      # 개인 작업 폴더는 --pull 을 줬을 때만 건드린다
    if not os.path.isdir(root):
        raise SystemExit(f'소스 폴더가 없습니다: {root}')
    is_repo = bool(git(root, 'rev-parse', '--git-dir'))
    return dict(name=src['name'], kind='git' if src.get('git') else 'path',
                branch=(branch or git(root, 'rev-parse', '--abbrev-ref', 'HEAD')) if is_repo else '',
                commit=git(root, 'rev-parse', '--short', 'HEAD') if is_repo else '',
                commit_date=git(root, 'log', '-1', '--format=%cs') if is_repo else '',
                dirty=bool(git(root, 'status', '--porcelain', '--untracked-files=no')) if is_repo else False)


def groups_of(cfg):
    """시스템을 (소스, 이름공간) 묶음으로 나눈다. 같은 묶음은 이름을 서로 풀 수 있어 함께 분석해야 한다."""
    out, index = [], {}
    for s in cfg['systems']:
        key = (s['source'], s.get('ns', s['id']))
        if key not in index:
            index[key] = dict(id=f'{key[0]}--{key[1]}', source=key[0], ns=key[1], systems=[])
            out.append(index[key])
        index[key]['systems'].append(s)
    return out


def group_key(root, group, opts, version):
    """묶음의 지문. 엔진·규칙 버전, 설정, 묶음에 속한 파일들의 내용이 하나라도 바뀌면 달라진다."""
    h = hashlib.sha1((version + json.dumps([group['systems'], {k: v for k, v in opts.items() if k != 'rules'}], sort_keys=True, ensure_ascii=False)).encode())
    paths = set()
    for s in group['systems']:
        for r in list(s['roots']) + [t[0] for t in s.get('tests', [])]:
            paths.add(r)
    for r in opts.get('strings_xml', []):
        paths.add(r)
    n = 0
    for r in sorted(paths):
        p = os.path.join(root, r)
        files = [p] if os.path.isfile(p) else []
        for d, dirs, fs in os.walk(p):
            dirs[:] = sorted(x for x in dirs if x not in SKIP_DIRS)
            files += [os.path.join(d, f) for f in sorted(fs)]
        for f in files:
            try:
                if os.path.getsize(f) > 3_000_000:
                    continue
                h.update(os.path.relpath(f, root).encode() + b'\0' + hashlib.sha1(open(f, 'rb').read()).digest())
                n += 1
            except OSError:
                pass
    return h.hexdigest(), n


class Lock:
    """현행화가 두 번 겹치지 않게 하는 잠금 파일. 30분 넘게 남아 있으면 죽은 작업으로 보고 넘겨받는다."""

    def __init__(self, store):
        self.path = os.path.join(store, '.lock')

    def __enter__(self):
        for attempt in (1, 2):
            try:
                fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.write(fd, f'{socket.gethostname()} pid {os.getpid()} {datetime.datetime.now().isoformat(timespec="seconds")}'.encode())
                os.close(fd)
                return self
            except FileExistsError:
                age = time.time() - os.path.getmtime(self.path)
                who = open(self.path, encoding='utf-8', errors='replace').read()
                if age < 1800 or attempt == 2:
                    raise SystemExit(f'다른 현행화가 진행 중입니다 ({who}, {int(age)}초 전 시작). 끝난 뒤 다시 실행하세요.')
                print(f'오래된 잠금을 넘겨받습니다: {who}')
                os.remove(self.path)

    def __exit__(self, *exc):
        try:
            os.remove(self.path)
        except OSError:
            pass


# ───────────────────────────── 연결기 ─────────────────────────────
# 접점(port)끼리 맞춘다. 어느 언어·프레임워크에서 나온 접점인지는 보지 않는다.
def norm_path(p):
    p = re.sub(r'^https?://[^/]+', '', p.split('?')[0].strip())
    return [':' + s.strip('{}<>:$') if re.match(r'[:{<$]', s) else s for s in p.strip('/').split('/') if s != '']


def match_route(routes, method, segs):
    best, best_score = None, -1
    for r in routes:
        rs = r['segs']
        if len(rs) != len(segs):
            continue
        score, ok = 0, True
        for a, b in zip(rs, segs):
            if a.startswith(':'):
                score += 1 if b.startswith(':') else 0
            elif b.startswith(':') or a != b:
                ok = False
                break
            else:
                score += 2
        if not ok:
            continue
        score += 3 if (method and r['method'] in (method, 'ALL')) else 0
        if score > best_score:
            best, best_score = r, score
    return best


def norm_table(name):
    return re.sub(r'[^a-z0-9]', '', name.lower())


def link(cfg, results, sources, rules):
    """묶음 결과들을 하나로 합치고 시스템 사이의 선을 만든다. results: [(묶음, 결과)] (설정 순서)."""
    L = cfg.get('link', {})
    sysmap = {s['id']: s for s in cfg['systems']}
    lang_of = lambda u: sysmap[u['sys']]['lang'] if u['sys'] in sysmap else '-'
    ns_of = lambda u: sysmap[u['sys']].get('ns', u['sys']) if u['sys'] in sysmap else u['sys']
    units, edges, routes, http, dbacc, tables, others, svc, mtok = [], [], [], [], [], [], [], {}, {}
    for group, res in results:
        off = len(units)
        for u in res['units']:
            r = dict(rt='unit', i=len(units), **u)
            r['src'] = group['source']
            for m in r.get('m', []):
                if m.get('c'):
                    m['c'] = [[t + off, tm] for t, tm in m['c']]
            units.append(r)
        edges += [dict(e, s=e['s'] + off, t=e['t'] + off) for e in res['edges']]
        P = res['ports']
        routes += [dict(method=m, path=p, segs=norm_path(p), unit=u + off, member=mi, rule=rule) for m, p, u, mi, rule in P['routes']]
        http += [[u + off, mi, m, p, rule, pos] for u, mi, m, p, rule, pos in P['http']]
        dbacc += [[u + off, mi, style, name, op, rule] for u, mi, style, name, op, rule in P['db']]
        tables += [[u + off, name, rule] for u, name, rule in P.get('tables', [])]
        others += [[k, role, key, u + off, mi, rule] for k, role, key, u, mi, rule in P.get('other', [])]
        for sid, hits in P['svc'].items():
            svc.setdefault(sid, {}).update({u + off: n for u, n in hits})
        mtok.update({int(u) + off: t for u, t in P['mtok'].items()})
    n_code = len(units)

    # API: 호출 후보 ↔ 제공 라우트
    agg, seen = {}, set()
    for u, mi, method, path, rule, pos in http:
        if (u, pos) in seen:          # 같은 자리를 여러 규칙이 잡았으면 한 번만 센다
            continue
        seen.add((u, pos))
        r = match_route(routes, method, norm_path(path))
        if not r or r['unit'] == u:
            continue
        a = agg.setdefault((u, r['unit']), dict(w=0, routes=[], rules=[]))
        a['w'] += 1
        label = f"{r['method']} {r['path']}"
        if label not in a['routes']:
            a['routes'].append(label)
        if rule not in a['rules']:
            a['rules'].append(rule)
        if mi is not None:
            lst = units[u]['m'][mi].setdefault('h', [])
            if [r['unit'], r['member'], label] not in lst and r['member'] is not None:
                lst.append([r['unit'], r['member'], label])
    # DB: 접근 ↔ 테이블 (스키마 어댑터가 만든 테이블 유닛 + 규칙이 낸 테이블 제공 접점)
    table_idx = {}
    for i, u in enumerate(units):
        if u['kind'] == 'table':
            table_idx.setdefault((u['sys'], norm_table(u['name'])), i)
    for i, name, rule in tables:
        table_idx.setdefault((units[i]['sys'], norm_table(name)), i)
    db_edges = {}
    for u, mi, style, name, op, rule in dbacc:
        want = sysmap.get(units[u]['sys'], {}).get('db')
        key = norm_table(name)
        cand = [t for (sid, k), t in table_idx.items() if (k == key or k == key.rstrip('s') or k.rstrip('s') == key) and (not want or sid == want)]
        if not cand or cand[0] == u:
            continue
        t = cand[0]
        d = db_edges.setdefault((u, t), dict(r=0, w=0, rules=[]))
        if rule not in d['rules']:
            d['rules'].append(rule)
        for ch in op:                 # 'rw' 는 읽기와 쓰기 둘 다
            d[ch] += 1
            if mi is not None:
                lst = units[u]['m'][mi].setdefault('db', [])
                if [t, ch] not in lst:
                    lst.append([t, ch])
    # 메시지 토픽 등: 같은 종류·같은 키의 제공 ↔ 사용
    msg = {}
    prov = {}
    for kind, role, key, u, mi, rule in others:
        if role == 'provide':
            prov.setdefault((kind, key), []).append((u, mi, rule))
    for kind, role, key, u, mi, rule in others:
        if role != 'consume':
            continue
        for pu, pmi, prule in prov.get((kind, key), []):
            if pu != u:
                d = msg.setdefault((pu, u), dict(w=0, keys=[], kind=kind, rules=[prule, rule]))     # 방향: 발행 → 구독
                d['w'] += 1
                if key not in d['keys']:
                    d['keys'].append(key)
    # 외부·기기 서비스
    catalog = [R for p in rules for R in p['rules'] if R['type'] == 'service']
    name_rules = L.get('service_units', {})
    svc_edges = {}
    for S in catalog:
        hits = []
        for i in range(n_code):
            u = units[i]
            if not u['file'] or u['kind'] in ('table', 'enum'):
                continue
            n = svc.get(S['id'], {}).get(i, 0) + (1 if S['id'] in name_rules and re.search(name_rules[S['id']], u['name']) else 0)
            if n:
                hits.append((i, n))
        if hits:
            ui = len(units)
            units.append(dict(rt='unit', i=ui, id=f"{S['sys']}:{S['name']}", sys=S['sys'], mod='서비스', name=S['name'], kind='service', decl='external',
                              file='', line=0, loc=0, doc=S.get('desc', ''), src=''))
            for i, n in hits:
                svc_edges[(i, ui)] = n
    # 플랫폼 간 대응 구현: 주석이 다른 묶음의 이름을 가리키거나, 지정한 시스템 쌍에서 타입 이름이 같을 때
    MS = set(L.get('mirror_systems') or [s['id'] for s in cfg['systems'] if s['lang'] not in ('prisma',)])
    mirror, all_names = {}, {}
    for i in range(n_code):
        u = units[i]
        if u['sys'] in MS:
            all_names.setdefault(u['name'], []).append(i)
            if u.get('decl') == 'file' and lang_of(u) == 'ts':
                all_names.setdefault(u['name'] + '.ts', []).append(i)
    for i in range(n_code):
        u = units[i]
        if u['sys'] not in MS or not u['file']:
            continue
        for tok in mtok.get(i, []):
            for cand in dict.fromkeys((tok, tok.split('.')[0], re.sub(r'\.ts$', '', tok))):
                found = all_names.get(cand, [])
                if any(ns_of(units[t]) == ns_of(u) for t in found):
                    continue
                for t in found:
                    if (t, i) not in mirror:
                        mirror[(i, t)] = 'doc'
    for a_sys, b_sys in L.get('twins', []):
        b_names = {}
        for i in range(n_code):
            if units[i]['sys'] == b_sys:
                b_names.setdefault(units[i]['name'], []).append(i)
        for i in range(n_code):
            u = units[i]
            if u['sys'] == a_sys and len(u['name']) >= 9:
                for t in b_names.get(u['name'], []):
                    if (i, t) not in mirror and (t, i) not in mirror:
                        mirror[(i, t)] = 'name'

    for (s, t), a in agg.items():
        edges.append(dict(s=s, t=t, k='http', w=a['w'], routes=a['routes'], by=a['rules']))
    for (s, t), d in db_edges.items():
        edges.append(dict(s=s, t=t, k='db', w=d['r'] + d['w'], ops=('r' if d['r'] else '') + ('w' if d['w'] else ''), by=d['rules']))
    for (s, t), d in msg.items():
        edges.append(dict(s=s, t=t, k='msg', w=d['w'], keys=d['keys'], topic=d['kind'], by=d['rules']))
    for (s, t), w in svc_edges.items():
        edges.append(dict(s=s, t=t, k='svc', w=w))
    for (s, t), how in mirror.items():
        edges.append(dict(s=s, t=t, k='mirror', w=1, how=how))

    used = set(u['sys'] for u in units)
    systems = [dict(id=s['id'], name=s['name'], sub=s.get('sub', ''), source=s['source']) for s in cfg['systems']] + [p for p in PSEUDO]
    first = sources[0] if sources else {}
    meta = dict(rt='meta', repo=cfg.get('name', ''), systems=systems, sources=sources, analyzed=datetime.date.today().isoformat(),
                commit=first.get('commit', ''), branch=first.get('branch', ''), commit_date=first.get('commit_date', ''), dirty=first.get('dirty', False))
    open_routes = [r for r in routes if not any(e['k'] == 'http' and e['t'] == r['unit'] and f"{r['method']} {r['path']}" in e['routes'] for e in edges)]
    summary = dict(units=len(units), members=sum(len(u.get('m', [])) for u in units), edges=len(edges),
                   by_kind={k: sum(1 for e in edges if e['k'] == k) for k in sorted(set(e['k'] for e in edges))},
                   routes=len(routes), open_routes=len(open_routes), http_candidates=len(http),
                   unmatched_db=sum(1 for x in dbacc if not any(k[1] == norm_table(x[3]) for k in table_idx)))
    return [meta] + units + [dict(rt='edge', **e) for e in edges], summary


def fingerprints(records):
    """유닛별 지문. 번호나 줄 위치가 밀린 것은 변경으로 치지 않고, 내용·멤버·나가는 연결이 달라졌을 때만 바뀐다."""
    units = [r for r in records if r['rt'] == 'unit']
    out_edges = {}
    for e in records:
        if e['rt'] == 'edge':
            out_edges.setdefault(e['s'], []).append((e['k'], units[e['t']]['id'], e.get('routes'), e.get('ops')))
    fp = {}
    for u in units:
        body = [u['sys'], u['mod'], u['name'], u['kind'], u.get('file'), u.get('loc'), u.get('doc'), u.get('conf'), u.get('lit'),
                [[m['n'], m['s'], m['e'] - m['l'], m.get('d'), m.get('route'), m.get('lit'),
                  sorted([units[t]['id'], units[t]['m'][tm]['n']] for t, tm in m.get('c', []))] for m in u.get('m', [])],
                sorted(out_edges.get(u['i'], []), key=str)]
        fp[u['id']] = hashlib.sha1(json.dumps(body, ensure_ascii=False, sort_keys=True, default=str).encode()).hexdigest()[:12]
    return fp


def write_atomic(path, text):
    tmp = f'{path}.tmp-{os.getpid()}'
    with open(tmp, 'w', encoding='utf-8') as fh:
        fh.write(text)
    os.replace(tmp, path)        # 같은 폴더 안 이름 바꾸기라 읽는 쪽은 옛 파일이나 새 파일 중 하나만 본다


# ───────────────────────────── 명령 ─────────────────────────────
def cmd_sync(cfg, args):
    store, version, rules = store_dir(cfg), engine_version(cfg), load_rules(cfg)
    out_dir = os.path.join(cfg['_dir'], cfg.get('out', '.'))
    with Lock(store):
        t0 = time.time()
        prev_manifest = {}
        mpath = os.path.join(out_dir, 'atlas-manifest.json')
        if os.path.exists(mpath):
            prev_manifest = json.load(open(mpath, encoding='utf-8'))
        prev_commits = {s['name']: s.get('commit') for s in prev_manifest.get('sources', [])}
        sources = []
        for src in cfg['sources']:
            info = prepare_source(cfg, src, fetch=not args.no_fetch, pull=args.pull)
            info['prev_commit'] = prev_commits.get(src['name'], '')
            sources.append(info)
        roots = {s['name']: source_root(cfg, s) for s in cfg['sources']}
        results, report = [], []
        for g in groups_of(cfg):
            opts = dict(ns=g['ns'], api_hosts=cfg.get('api_hosts', []), rules=rules,
                        strings_xml=[p for s in g['systems'] for p in s.get('strings_xml', [])], skip_dirs=cfg.get('skip_dirs', []))
            key, nfiles = group_key(roots[g['source']], g, opts, version)
            cpath = os.path.join(store, 'groups', re.sub(r'[^\w.-]', '_', g['id']) + '.json')
            cached = json.load(open(cpath, encoding='utf-8')) if os.path.exists(cpath) and not args.force else None
            t1 = time.time()
            if cached and cached['key'] == key:
                res, status = cached['result'], 'reused'
            else:
                res, status = run_group(roots[g['source']], g['systems'], opts), 'analyzed'
                write_atomic(cpath, json.dumps(dict(key=key, result=res), ensure_ascii=False))
            results.append((g, res))
            report.append(dict(id=g['id'], systems=[s['id'] for s in g['systems']], files=nfiles, units=len(res['units']), status=status, ms=int((time.time() - t1) * 1000)))
        # 데이터에는 '어느 커밋인가'만 넣는다. 직전 커밋은 실행할 때마다 달라질 수 있어 목록표에만 둔다
        records, summary = link(cfg, results, [{k: v for k, v in x.items() if k != 'prev_commit'} for x in sources], rules)
        fp = fingerprints(records)
        fpath = os.path.join(store, 'fingerprints.json')
        old = json.load(open(fpath, encoding='utf-8')) if os.path.exists(fpath) else None
        changes = dict(added=[], removed=[], changed=[])
        if old is not None:
            changes = dict(added=sorted(k for k in fp if k not in old), removed=sorted(k for k in old if k not in fp),
                           changed=sorted(k for k in fp if k in old and old[k] != fp[k]))
        body = '[\n' + ',\n'.join(json.dumps(r, ensure_ascii=False, separators=(',', ':')) for r in records) + '\n]\n'
        data_hash = hashlib.sha1(re.sub(r'"analyzed":"[^"]*",?', '', body).encode()).hexdigest()[:10]
        unchanged = prev_manifest.get('data_hash') == data_hash
        manifest = dict(name=cfg.get('name', ''), version=prev_manifest.get('version') if unchanged else datetime.datetime.now().strftime('%Y%m%d-%H%M%S') + '-' + data_hash[:6],
                        built_at=datetime.datetime.now().isoformat(timespec='seconds'), engine=version, data_hash=data_hash, sources=sources, groups=report, summary=summary,
                        changes=prev_manifest.get('changes', changes) if unchanged else {k: v[:400] for k, v in changes.items()},
                        change_counts=prev_manifest.get('change_counts') if unchanged else {k: len(v) for k, v in changes.items()},
                        previous_version=prev_manifest.get('previous_version') if unchanged else prev_manifest.get('version'))
        os.makedirs(out_dir, exist_ok=True)
        if not unchanged:
            write_atomic(os.path.join(out_dir, 'atlas-data.json'), body)
            write_atomic(fpath, json.dumps(fp))
        write_atomic(mpath, json.dumps(manifest, ensure_ascii=False, indent=1))     # 목록표는 마지막에 쓴다: 화면은 이것을 보고 새 버전을 안다
    for s in sources:
        moved = f"{s['prev_commit']} → " if s['prev_commit'] and s['prev_commit'] != s['commit'] else ''
        print(f"소스 {s['name']}: " + (f"{s['branch']}@{moved}{s['commit']}{' (미커밋 수정 있음)' if s['dirty'] else ''}" if s['commit'] else 'git 저장소 아님 (현재 폴더 내용 기준)'))
    for r in report:
        print(f"  묶음 {r['id']:<22} {'다시 분석' if r['status'] == 'analyzed' else '재사용   '} 파일 {r['files']:>4} · 유닛 {r['units']:>4} · {r['ms']}ms")
    print(f"결과: 유닛 {summary['units']} · 메서드 {summary['members']} · 연결 {summary['edges']} {summary['by_kind']}")
    print(f"접점: 제공 API {summary['routes']}개 중 호출자 없는 것 {summary['open_routes']}개 · 호출 후보 {summary['http_candidates']}개")
    if unchanged:
        print(f"변경 없음. 버전 {manifest['version']} 유지 · {time.time() - t0:.1f}초")
    else:
        c = manifest['change_counts']
        print(f"새 버전 {manifest['version']} · 추가 {c['added']} · 삭제 {c['removed']} · 변경 {c['changed']} · {time.time() - t0:.1f}초")
    return manifest


def detect(root, name):
    """폴더를 훑어 시스템 후보를 만든다. 결과는 설정 파일에 적히고, 사람이 고칠 수 있다."""
    found = []

    def rel(p):
        return os.path.relpath(p, root)

    def has(d, ext, depth=4):
        for cur, dirs, fs in os.walk(d):
            dirs[:] = [x for x in dirs if x not in SKIP_DIRS]
            if cur[len(d):].count(os.sep) > depth:
                dirs[:] = []
            if any(f.endswith(ext) for f in fs):
                return True
        return False

    for cur, dirs, fs in os.walk(root):
        dirs[:] = sorted(x for x in dirs if x not in SKIP_DIRS and not x.startswith('.'))
        depth = cur[len(root):].count(os.sep)
        if depth > 3:
            dirs[:] = []
            continue
        base = os.path.basename(cur) or name
        if any(d.endswith('.xcodeproj') for d in dirs) or 'Package.swift' in fs:
            roots = [rel(os.path.join(cur, d)) for d in dirs if not d.endswith(('.xcodeproj', 'Tests', 'UITests')) and has(os.path.join(cur, d), '.swift')]
            tests = [[rel(os.path.join(cur, d)), '.swift'] for d in dirs if d.endswith('Tests') and not d.endswith('UITests')]
            if roots:
                found.append(dict(id='ios', name='iOS 앱', sub='Swift', lang='swift', ns='apple', roots=roots, tests=tests, rules=['client-path-arg']))
            dirs[:] = []
        elif any(f in ('build.gradle', 'build.gradle.kts', 'pom.xml') for f in fs):
            for lang, sub, ext in (('kotlin', 'src/main/kotlin', '.kt'), ('kotlin', 'src/main/java', '.kt'), ('java', 'src/main/java', '.java')):   # Android 는 Kotlin 을 java 폴더에 두기도 한다
                roots = sorted(rel(p) for p in glob.glob(os.path.join(cur, '**', sub), recursive=True) if has(p, ext, 12))
                if roots:
                    android = any(os.path.exists(os.path.join(root, r, '..', 'AndroidManifest.xml')) for r in roots)
                    found.append(dict(id='android' if android else base.lower(), name='Android 앱' if android else base, sub=('Kotlin' if lang == 'kotlin' else 'Java') + (' · Android' if android else ' · JVM'),
                                      lang=lang, roots=roots, tests=[[r.replace('/main/', '/test/'), ext] for r in roots if os.path.isdir(os.path.join(root, r.replace('/main/', '/test/')))],
                                      rules=['client-base-url'] if android else []))
            dirs[:] = []
        elif 'package.json' in fs:
            pkg = open(os.path.join(cur, 'package.json'), encoding='utf-8', errors='replace').read()
            roots = [rel(os.path.join(cur, d)) for d in ('src', 'app', 'lib', 'pages', 'components', 'server') if os.path.isdir(os.path.join(cur, d))] or [rel(cur)]
            server = bool(re.search(r'"(@nestjs/core|express|fastify|koa|hono)"', pkg))
            s = dict(id=base.lower(), name=base, sub='NestJS' if '@nestjs/core' in pkg else 'Next.js' if '"next"' in pkg else 'Node.js' if server else 'Web', lang='ts', roots=roots,
                     rules=[] if '@nestjs/core' in pkg else ['client-slash-literal'])
            if os.path.isdir(os.path.join(cur, 'test')):
                s['tests'] = [[rel(os.path.join(cur, 'test')), '.js']]
            if '"next"' in pkg:
                s['alias_root'] = rel(cur) if rel(cur) != '.' else ''
            found.append(s)
            schema = os.path.join(cur, 'prisma', 'schema.prisma')
            if os.path.exists(schema):
                found.append(dict(id=base.lower() + '-db', name=base + ' DB', sub='Prisma 스키마', lang='prisma', roots=[rel(schema)]))
                s['db'] = base.lower() + '-db'
            dirs[:] = [d for d in dirs if d not in ('src', 'app', 'lib', 'pages', 'components', 'server', 'test', 'tests', 'public', 'prisma')]
        elif any(f in ('pyproject.toml', 'requirements.txt', 'setup.py', 'manage.py') for f in fs) and has(cur, '.py'):
            found.append(dict(id=base.lower(), name=base, sub='Python', lang='python', roots=[rel(cur)]))
            dirs[:] = []
        elif 'go.mod' in fs:
            found.append(dict(id=base.lower(), name=base, sub='Go (어댑터 없음)', lang='go', roots=[rel(cur)]))
            dirs[:] = []
    return found


def cmd_add(cfg, args):
    target = args.target
    is_git = bool(re.match(r'^(https?://|git@|ssh://|file://)', target)) or target.endswith('.git')
    name = args.name or re.sub(r'\.git$', '', os.path.basename(target.rstrip('/'))).lower()
    if any(s['name'] == name for s in cfg['sources']):
        raise SystemExit(f'이미 등록된 소스 이름입니다: {name}')
    src = dict(name=name, git=target, **({'branch': args.branch} if args.branch else {})) if is_git else dict(name=name, path=os.path.abspath(os.path.expanduser(target)))     # 어느 폴더에서 실행해도 같은 곳을 가리키게 절대 경로로 적는다
    if is_git and not args.dry_run:
        prepare_source(cfg, src)
    root = source_root(cfg, src) if not (is_git and args.dry_run) else None
    systems = detect(root, name) if root and os.path.isdir(root) else []
    taken = {s['id'] for s in cfg['systems']}
    for s in systems:
        s['source'] = name
        s.setdefault('ns', s['id'])
        if s['id'] in taken or s['id'] in ('ios', 'android') and cfg['systems']:
            s['id'] = f"{name}-{s['id']}"
            s['ns'] = f"{name}-{s['ns']}"
        taken.add(s['id'])
    known = {os.path.splitext(os.path.basename(p))[0] for p in glob.glob(os.path.join(HERE, 'langs', '*.py'))} | {'ts', 'js'}
    print(f"소스 {name}: {'git ' + target if is_git else root}")
    for s in systems:
        print(f"  시스템 {s['id']:<18} {s['sub']:<20} 언어 {s['lang']:<7} 루트 {', '.join(s['roots'][:3])}{' …' if len(s['roots']) > 3 else ''}{'' if s['lang'] in known or s['lang'] == 'typescript' else '   ← 어댑터 없음: 분석에서 빠짐'}")
    if not systems:
        print('  알아볼 수 있는 시스템이 없습니다. 설정 파일에 직접 적어 주세요.')
    if args.dry_run:
        print('(미리보기라 설정은 바꾸지 않았습니다)')
        return
    cfg['sources'].append(src)
    cfg['systems'] += [s for s in systems if s['lang'] in known]
    save_config(cfg)
    print(f"{cfg['_path']} 에 적었습니다. 이름·루트·모듈 규칙을 손본 뒤 sync 를 실행하세요.")


def cmd_status(cfg, args):
    mpath = os.path.join(cfg['_dir'], cfg.get('out', '.'), 'atlas-manifest.json')
    if not os.path.exists(mpath):
        print('아직 결과가 없습니다. sync 를 먼저 실행하세요.')
        return
    m = json.load(open(mpath, encoding='utf-8'))
    print(f"{m['name']} · 버전 {m['version']} · {m['built_at']} · 엔진 {m['engine']}")
    for s in m['sources']:
        print(f"  소스 {s['name']}: {s['branch']}@{s['commit']} ({s['commit_date']})")
    print(f"  유닛 {m['summary']['units']} · 연결 {m['summary']['edges']} · 지난 버전 대비 {m.get('change_counts')}")


def cmd_site(cfg, args):
    body = open(args.viewer, encoding='utf-8').read()
    os.makedirs(args.out, exist_ok=True)
    head = ('<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">'
            '<style>body{margin:0}[hidden]{display:none!important}</style></head><body>\n')
    write_atomic(os.path.join(args.out, 'index.html'), head + body + '\n</body></html>\n')
    src, made = os.path.join(cfg['_dir'], cfg.get('out', '.')), ['index.html']
    for f in ('atlas-data.json', 'atlas-manifest.json', 'atlas-notes.json'):      # 메모 파일(사람이 쓴 설명·예시 문의)은 있을 때만
        if f == 'atlas-notes.json' and not os.path.exists(os.path.join(src, f)):
            continue
        write_atomic(os.path.join(args.out, f), open(os.path.join(src, f), encoding='utf-8').read())
        made.append(f)
    print(f'{args.out} 에 {", ".join(made)} 을 만들었습니다. 이 폴더를 사내 웹 서버에 올리면 됩니다.')


def main():
    ap = argparse.ArgumentParser(description='코드 아틀라스')
    ap.add_argument('--config', default='atlas.config.json')
    sub = ap.add_subparsers(dest='cmd', required=True)
    p = sub.add_parser('init'); p.add_argument('name')
    p = sub.add_parser('add'); p.add_argument('target'); p.add_argument('--name'); p.add_argument('--branch'); p.add_argument('--dry-run', action='store_true')
    p = sub.add_parser('sync'); p.add_argument('--no-fetch', action='store_true'); p.add_argument('--pull', action='store_true'); p.add_argument('--force', action='store_true')
    sub.add_parser('status')
    p = sub.add_parser('site'); p.add_argument('viewer'); p.add_argument('out')
    args = ap.parse_args()
    if args.cmd == 'init':
        if os.path.exists(args.config):
            raise SystemExit(f'{args.config} 가 이미 있습니다.')
        json.dump(dict(name=args.name, out='.', store='atlas-store', api_hosts=[], sources=[], systems=[], link={}), open(args.config, 'w', encoding='utf-8'), ensure_ascii=False, indent=2)
        print(f'{args.config} 를 만들었습니다. add 로 소스를 추가하세요.')
        return
    cfg = load_config(args.config)
    {'add': cmd_add, 'sync': cmd_sync, 'status': cmd_status, 'site': cmd_site}[args.cmd](cfg, args)


if __name__ == '__main__':
    main()
