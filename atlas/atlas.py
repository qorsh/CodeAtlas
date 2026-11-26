#!/usr/bin/env python3
"""코드 아틀라스 — 여러 저장소의 코드를 분석해 하나의 구성도 데이터로 만든다.

  python3 atlas/atlas.py -w <이름> init                       작업 공간(workspaces/<이름>/) 만들기
  python3 atlas/atlas.py -w <이름> add <폴더 | git 주소> [--name N] [--branch B] [--dry-run]
  python3 atlas/atlas.py -w <이름> sync [--no-fetch] [--pull] [--force]   바뀐 묶음만 다시 분석하고 결과를 교체
  python3 atlas/atlas.py -w <이름> status                     소스별 기준 커밋과 마지막 결과
  python3 atlas/atlas.py -w <이름> diagnose                   연결 진단: 시스템마다 내주는 API·부르는 호출이 얼마나 읽히고 이어졌는지
  python3 atlas/atlas.py -w <이름> open                       이 컴퓨터에서 지도 열기 (로컬 서버 + 브라우저)
  python3 atlas/atlas.py -w <이름> site [뷰어 html] [출력 폴더]   사내 웹 서버에 올릴 정적 파일 묶음 만들기
  python3 atlas/atlas.py -w <이름> ingest <접속 로그 | -> [--follow]   실제 요청·응답을 지도에 얹기 (비정상 응답은 붉게 깜박인다)
-w 대신 --config <설정 파일> 로 다른 위치의 작업 공간을 가리킬 수 있다.

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
import fnmatch
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
ENGINE_POST = ['engine/scan.py', 'engine/facts.py', 'engine/rules.py', 'engine/extras.py', 'engine/status.py', 'engine/export.py']
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


def parser_signature():
    """설치된 tree-sitter 와 문법 패키지의 판. 파서가 바뀌면 결과도 달라질 수 있으므로 캐시 지문에 넣는다."""
    try:
        from importlib import metadata
        found = sorted({f"{d.metadata['Name'].lower().replace('_', '-')}=={d.version}" for d in metadata.distributions()
                        if (d.metadata['Name'] or '').lower().replace('_', '-').startswith('tree-sitter')})
        return ' '.join(found) if any(x.startswith('tree-sitter==') for x in found) else ''
    except Exception:
        return ''


def engine_version(cfg=None):
    h = hashlib.sha1(parser_signature().encode())
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


def language_table(parser='auto'):
    """등록된 언어와 그것을 읽는 파서: {'java': 'tree-sitter', 'swift': '내장', 'go': '문법 없음: tree-sitter-go'}.
    엔진 머리말과 어댑터 파일만 실행해 보고 알아낸다(소스는 읽지 않는다)."""
    ns = {'__name__': 'atlas_probe', 'ROOT': '.', 'SYSTEMS': [], 'OPTS': dict(ns='probe', parser=parser)}
    for rel in [f for f in engine_files() if f not in ENGINE_POST]:
        path = os.path.join(HERE, rel)
        exec(compile(open(path, encoding='utf-8').read(), path, 'exec'), ns)
    used, missing = ns.get('SITTER_USED', {}), ns.get('SITTER_MISSING', {})
    return {name: 'tree-sitter' if name in used else f'문법 없음: {missing[name]}' if name in missing else '내장' for name in ns['LANGUAGES']}


def use_project_venv():
    """저장소의 .venv 에 tree-sitter 가 깔려 있으면 그 파이썬으로 다시 실행한다.
    어떤 파이썬으로 부르든 같은 파서로 분석돼야 결과와 캐시가 흔들리지 않는다. ATLAS_NO_VENV=1 이면 하지 않는다."""
    venv = os.path.join(os.path.dirname(HERE), '.venv')
    py = next((p for p in (os.path.join(venv, 'bin', 'python'), os.path.join(venv, 'Scripts', 'python.exe')) if os.path.exists(p)), None)
    if not py or os.environ.get('ATLAS_NO_VENV') or os.path.realpath(sys.prefix) == os.path.realpath(venv):
        return
    try:
        import tree_sitter  # noqa: F401
        return
    except ImportError:
        pass
    os.execv(py, [py] + sys.argv)


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


_URL = re.compile(r'^\s*https?://([^/?#\s]+)', re.I)
_NOISE_HOST = re.compile(r'^(localhost|127\.0\.0\.1|0\.0\.0\.0|\[::1\]|www\.w3\.org|schemas\..*|xmlns\..*|json-schema\.org|.*\.apache\.org|github\.com|.*\.githubusercontent\.com)(:\d+)?$')


def norm_host(h):
    h = h.lower().rstrip('.')
    return re.sub(r':(80|443)$', '', h.split('@')[-1])


def host_match(host, patterns):
    """설정에 적은 주소와 코드에 적힌 주소가 같은가. 포트를 안 적었으면 어느 포트든 맞고, *.example.com 같은 꼴도 받는다."""
    bare = host.split(':')[0]
    return any(fnmatch.fnmatchcase(host, p) or fnmatch.fnmatchcase(bare, p) for p in (norm_host(x) for x in patterns))


def glob_segs(pattern, segs):
    """/erp/** 같은 경로 꼴이 경로 조각들과 맞는가. * 는 조각 하나, ** 는 나머지 전부."""
    ps = [g for g in pattern.strip('/').split('/') if g != '']
    for i, g in enumerate(ps):
        if g == '**':
            return True
        if i >= len(segs) or (g != '*' and not fnmatch.fnmatchcase(segs[i], g)):
            return False
    return len(ps) == len(segs)


def resolve_calls(cfg, units, routes, http, hosts):
    """API 호출 후보마다 대상을 정한다. 근거가 강한 순서:

      레거시 선언   설정의 external 에 적은 주소·경로 꼴·클라이언트 이름에 맞으면 그 시스템으로 (지도 안 서버와 경로가 겹쳐도 선언이 이긴다)
      주소          호출에 적힌 주소가 어느 서버의 hosts 에 있으면 그 서버 안에서만 찾는다
      하나뿐        경로가 맞는 서버가 하나뿐이다
      설정          link.calls[부르는 시스템] 에 적힌 순서로 고른다 · link.never[부르는 시스템] 은 후보에서 뺀다
      자기 자신     경로만 쓴 호출이고 자기 시스템에 그 라우트가 있다
      언급한 주소   부르는 시스템의 코드에 적힌 주소(기준 주소 상수 등)의 주인이 후보 가운데 하나뿐이다
      같은 저장소   후보 가운데 같은 소스에 든 서버가 하나뿐이다
      주로 부르는 서버   이 시스템의 다른 호출이 몰리는 서버 (추정으로 표시)
    여기까지 와도 못 정하면 선을 긋지 않고 '확인이 필요한 호출'로 남긴다.
    경로가 그대로는 어느 서버와도 맞지 않는 호출은, 같은 접두어를 떼거나 붙였을 때 맞는 호출이 여럿이면 그 접두어로 맞춘다(접두어 추정).
    돌려주는 diag 는 시스템별 집계와 추정한 접두어다(연결 진단)."""
    L = cfg.get('link', {})
    ext_cfg = cfg.get('external', [])
    src_of = {s['id']: s['source'] for s in cfg['systems']}
    owners = [(s['id'], s.get('hosts', [])) for s in cfg['systems'] if s.get('hosts')]
    by_sys = {}
    for r in routes:
        by_sys.setdefault(units[r['unit']]['sys'], []).append(r)
    for s in cfg['systems']:                 # 게이트웨이가 경로 앞에 붙이는 접두어: 붙은 꼴로도 맞춰 본다
        base = by_sys.get(s['id'], [])
        extra = [dict(r, segs=norm_path(pre) + r['segs'], pfx=1) for pre in s.get('prefix', []) for r in base]
        if extra:
            by_sys[s['id']] = base + extra
    owner_of = lambda h: next((sid for sid, pats in owners if host_match(h, pats)), None)

    file_hosts = {}                # (시스템, 파일) → 그 파일에 적힌 주소들. 클라이언트 클래스가 기준 주소를 필드로 들고 경로만 붙여 쓰는 꼴을 위해
    for u, h in hosts:
        file_hosts.setdefault((units[u]['sys'], units[u]['file']), set()).add(norm_host(h))

    def off(host, cs, segs):
        o = offmap.setdefault(host, dict(host=host, n=0, systems=[], paths=[]))
        o['n'] += 1
        if cs not in o['systems']:
            o['systems'].append(cs)
        shown = '/' + '/'.join('{' + g[1:] + '}' if g.startswith(':') else g for g in segs)
        if shown not in o['paths'] and len(o['paths']) < 5:
            o['paths'].append(shown)

    resolved, pending, external, offmap, how_n, seen = [], [], {}, {}, {}, set()
    first, learned, unmatched, stat = [], {}, [], {}
    loose = lambda rule, segs=(0, 0): rule.startswith('client-slash-literal') or (rule.startswith('client-') and len(segs) < 2)      # 넓은 그물, 그리고 get("id") 같은 조각 하나짜리: 안 맞은 것은 대부분 호출이 아니다

    def tally(cs, what):
        t = stat.setdefault(cs, dict(calls=0, linked=0, pending=0, external=0, unmatched=0, ex=[]))
        t['calls'] += 1
        t[what] += 1
        return t
    for u, mi, method, path, rule, pos in http:
        if (u, pos) in seen:          # 같은 자리를 여러 규칙이 잡았으면 한 번만 센다
            continue
        seen.add((u, pos))
        mh = _URL.match(path)
        host, segs, cs = (norm_host(mh.group(1)) if mh else None), norm_path(path), units[u]['sys']
        if host and re.search(r'[{}$%:]', host.split(':')[0]):       # 주소 자리가 변수로 채워진 꼴은 주소로 보지 않는다
            host = None
        x = next((x for x in ext_cfg if host and host_match(host, x.get('hosts', []))), None)
        owner = owner_of(host) if host and not x else None
        if not x and not owner:
            x = next((x for x in ext_cfg if (x.get('clients') and re.search(x['clients'], units[u]['name'])) or any(glob_segs(p, segs) for p in x.get('paths', []))), None)
        if x:
            external.setdefault(x['id'], []).append((u, mi, method, segs, rule))
            how_n['external'] = how_n.get('external', 0) + 1
            tally(cs, 'external')
            continue
        per = {}
        for sid, rs in by_sys.items():
            if (owner and sid != owner) or sid in L.get('never', {}).get(cs, []):
                continue
            r = match_route(rs, method, segs)
            if r and r['unit'] != u:
                per[sid] = r
        if not per:
            if not host and not rule.startswith('client-'):      # 경로만 쓴 호출인데 지도 안에 맞는 서버가 없다: 같은 파일에 적힌 주소가 등록한 레거시 하나의 것이면 그리로 간다
                fh = [h for h in file_hosts.get((cs, units[u]['file']), ()) if not _NOISE_HOST.match(h) and not owner_of(h)]
                xs = [x for x in ext_cfg if any(host_match(h, x.get('hosts', [])) for h in fh)]
                if len(xs) == 1:
                    external.setdefault(xs[0]['id'], []).append((u, mi, method, segs, rule))
                    how_n['external'] = how_n.get('external', 0) + 1
                    tally(cs, 'external')
                    continue
            unmatched.append((u, mi, method, path, rule, host, owner, segs))      # 접두어 추정에서 한 번 더 본다
            continue
        first.append((u, mi, method, path, rule, host, owner, per))
        if len(per) == 1 and host and not owner:
            learned.setdefault(host, set()).add(next(iter(per)))
    learned = {h: next(iter(v)) for h, v in learned.items() if len(v) == 1}       # 주소가 적힌 호출이 한 서버로만 간 주소
    mentioned, seen_hosts = {}, {}
    for u, h in hosts:
        h = norm_host(h)
        sid = owner_of(h) or learned.get(h)
        if sid:
            mentioned.setdefault(units[u]['sys'], set()).add(sid)
        ext = next((x['id'] for x in ext_cfg if host_match(h, x.get('hosts', []))), None)
        if not _NOISE_HOST.match(h):
            e = seen_hosts.setdefault(h, dict(host=h, systems=[], owner=owner_of(h) or ext or ''))
            if units[u]['sys'] not in e['systems']:
                e['systems'].append(units[u]['sys'])
    aff = {}
    for u, mi, method, path, rule, host, owner, per in first:
        if len(per) == 1:
            k = (units[u]['sys'], next(iter(per)))
            aff[k] = aff.get(k, 0) + 1
    for u, mi, method, path, rule, host, owner, per in first:
        cs, pick, how = units[u]['sys'], None, None
        if len(per) == 1:
            pick, how = next(iter(per)), 'host' if owner else 'unique'
        if not pick:
            pick, how = next(((sid, 'calls') for sid in L.get('calls', {}).get(cs, []) if sid in per), (None, None))
        if not pick and not host and cs in per:
            pick, how = cs, 'self'
        if not pick and host and learned.get(host) in per:
            pick, how = learned[host], 'host'
        if not pick:
            m = [sid for sid in per if sid in mentioned.get(cs, ())]
            if len(m) == 1:
                pick, how = m[0], 'mention'
        if not pick:
            m = [sid for sid in per if src_of.get(sid) == src_of.get(cs)]
            if len(m) == 1:
                pick, how = m[0], 'source'
        if not pick:
            rank = sorted(per, key=lambda sid: -aff.get((cs, sid), 0))
            top, second = aff.get((cs, rank[0]), 0), aff.get((cs, rank[1]), 0)
            if top and top >= 3 * second:
                pick, how = rank[0], 'affinity'
        if pick:
            resolved.append((u, mi, rule, per[pick], how))
            how_n[how] = how_n.get(how, 0) + 1
            tally(cs, 'linked')
        else:
            pending.append((u, mi, method, path, rule, per))
            tally(cs, 'pending')

    # 접두어 추정. 경로가 그대로는 어느 서버와도 맞지 않는 호출 가운데, 앞에 무언가 더 붙었거나(게이트웨이·WAS 가 붙이는 컨텍스트 경로)
    # 앞부분이 빠진(기준 주소에 경로가 들어 있는) 꼴을 찾는다. 우연히 맞는 것을 막으려고, 같은 접두어로 맞는 호출이 여럿일 때에만 받아들인다.
    shape = lambda method, segs: (method, tuple(':' if g.startswith(':') else g for g in segs))
    last_lit, last_any = {}, []
    for sid, rs in by_sys.items():
        for r in rs:
            if not r.get('pfx') and r['segs']:
                (last_any if r['segs'][-1].startswith(':') else last_lit.setdefault(r['segs'][-1], [])).append((sid, r))
    votes, real_n = {}, {}
    for i, (u, mi, method, path, rule, host, owner, segs) in enumerate(unmatched):
        cs = units[u]['sys']
        if not loose(rule, segs):
            real_n.setdefault(cs, set()).add(shape(method, segs))
        if not segs or (loose(rule, segs) and len(segs) < 2):
            continue
        for sid, r in last_any + ([] if segs[-1].startswith(':') else last_lit.get(segs[-1], [])):
            if sid == cs or (owner and sid != owner) or sid in L.get('never', {}).get(cs, []) or r['unit'] == u:
                continue
            rsegs, n, m = r['segs'], len(segs), len(r['segs'])
            same = lambda a, b: any(x == y and not x.startswith(':') for x, y in zip(a, b))      # 글자 그대로 같은 조각이 하나는 있어야 한다. /c/{slug} 같은 라우트는 무엇이든 받아 주므로 근거가 못 된다
            if n > m and not any(g.startswith(':') for g in segs[:n - m]) and same(rsegs, segs[n - m:]) and match_route([dict(r, segs=rsegs)], method, segs[n - m:]):
                key = (cs, sid, 'strip', tuple(segs[:n - m]))          # 호출 앞에 접두어가 붙어 있다
            elif m > n and not any(g.startswith(':') for g in rsegs[:m - n]) and same(rsegs[m - n:], segs) and match_route([dict(r, segs=rsegs[m - n:])], method, segs):
                key = (cs, sid, 'add', tuple(rsegs[:m - n]))           # 호출에 접두어가 빠져 있다
            else:
                continue
            votes.setdefault(key, {}).setdefault(i, r)
    support = {k: min(len({shape(unmatched[i][2], unmatched[i][7]) for i in v}), len({id(r) for r in v.values()}) + 1) for k, v in votes.items()}     # 서로 다른 호출이 서로 다른 API 에 맞아야 근거가 된다
    accepted = {k for k, n in support.items() if n >= 3 or (n >= 2 and n * 2 >= len(real_n.get(k[0], ())))}
    used, taken = {}, set()
    for i, (u, mi, method, path, rule, host, owner, segs) in enumerate(unmatched):
        keys = [k for k in accepted if i in votes[k]]
        if keys:
            k = max(keys, key=lambda k: (support[k], -len(k[3])))
            resolved.append((u, mi, rule, votes[k][i], 'prefix'))
            how_n['prefix'] = how_n.get('prefix', 0) + 1
            used[k] = used.get(k, 0) + 1
            taken.add(i)
            tally(units[u]['sys'], 'linked')
    prefixes = [dict(frm=k[0], to=k[1], kind=k[2], prefix='/' + '/'.join(k[3]), n=n) for k, n in sorted(used.items(), key=lambda kv: -kv[1])]
    for i, (u, mi, method, path, rule, host, owner, segs) in enumerate(unmatched):       # 끝내 못 맞춘 호출
        if i in taken:
            continue
        cs = units[u]['sys']
        if host and not owner and not _NOISE_HOST.match(host):
            off(host, cs, segs)
        elif not host and not rule.startswith('client-'):
            fh = [h for h in file_hosts.get((cs, units[u]['file']), ()) if not _NOISE_HOST.match(h) and not owner_of(h)]
            if len(fh) == 1 and not any(host_match(fh[0], x.get('hosts', [])) for x in ext_cfg):
                off(fh[0], cs, segs)
        shown = f"{method} /" + '/'.join('{' + g[1:] + '}' if g.startswith(':') else g for g in segs)
        if not loose(rule, segs):
            t = tally(cs, 'unmatched')
            if shown not in t['ex'] and len(t['ex']) < 6:
                t['ex'].append(shown)
        elif len(segs) >= 2:                  # 넓은 그물에 걸렸지만 어느 API 와도 안 맞은, 경로처럼 생긴 문자열. 호출일 수도 아닐 수도 있어 따로 센다
            t = stat.setdefault(cs, dict(calls=0, linked=0, pending=0, external=0, unmatched=0, ex=[]))
            t['maybe'] = t.get('maybe', 0) + 1
            if shown not in t.setdefault('maybe_ex', []) and len(t['maybe_ex']) < 6:
                t['maybe_ex'].append(shown[shown.index(' ') + 1:])
    # 연결 진단: 시스템마다 내주는 API 와 부르는 호출이 얼마나 읽혔고 얼마나 이어졌는지. 선이 없을 때 어느 쪽이 빠졌는지 가리는 근거다
    n_units, n_hosts = {}, {}
    for x in units:
        n_units[x['sys']] = n_units.get(x['sys'], 0) + 1
    for u, h in hosts:                       # 코드에 서버 주소가 적혀 있는가: 호출을 못 읽은 앱을 가려내는 단서
        if not _NOISE_HOST.match(norm_host(h)):
            n_hosts.setdefault(units[u]['sys'], set()).add(norm_host(h))
    diag, called = [], {(r['unit'], r['member'], r['method'], r['path']) for _, _, _, r, _ in resolved}
    for y in cfg['systems']:
        rs, t = [r for r in by_sys.get(y['id'], []) if not r.get('pfx')], stat.get(y['id'], {})
        diag.append(dict(id=y['id'], lang=y.get('lang', ''), units=n_units.get(y['id'], 0), hosts=len(n_hosts.get(y['id'], ())), routes=len(rs),
                         open=sum(1 for r in rs if (r['unit'], r['member'], r['method'], r['path']) not in called), route_ex=[f"{r['method']} {r['path']}" for r in rs[:5]],
                         calls=t.get('calls', 0), linked=t.get('linked', 0), pending=t.get('pending', 0), external=t.get('external', 0), unmatched=t.get('unmatched', 0), call_ex=t.get('ex', []), maybe=t.get('maybe', 0), maybe_ex=t.get('maybe_ex', [])))
    # 같은 서버를 두 번 넣은 것으로 보이는 쌍: 라우트 목록이 통째로 같다
    sets = {sid: frozenset((r['method'], tuple(':' if g.startswith(':') else g for g in r['segs'])) for r in rs if not r.get('pfx')) for sid, rs in by_sys.items()}
    ids = sorted(sid for sid, v in sets.items() if len(v) >= 3)
    twins = [[a, b] for i, a in enumerate(ids) for b in ids[i + 1:] if sets[a] == sets[b]]
    return dict(resolved=resolved, pending=pending, external=external, how=how_n, twins=twins, diag=dict(systems=diag, prefixes=prefixes),
                offmap=sorted(offmap.values(), key=lambda o: -o['n']), hosts_seen=sorted(seen_hosts.values(), key=lambda e: (not e['owner'], e['host'])))


def link(cfg, results, sources, rules):
    """묶음 결과들을 하나로 합치고 시스템 사이의 선을 만든다. results: [(묶음, 결과)] (설정 순서)."""
    L = cfg.get('link', {})
    sysmap = {s['id']: s for s in cfg['systems']}
    lang_of = lambda u: sysmap[u['sys']]['lang'] if u['sys'] in sysmap else '-'
    ns_of = lambda u: sysmap[u['sys']].get('ns', u['sys']) if u['sys'] in sysmap else u['sys']
    units, edges, routes, http, dbacc, tables, others, svc, mtok, hosts, unread = [], [], [], [], [], [], [], {}, {}, [], []
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
        hosts += [[u + off, h] for u, h in P.get('hosts', [])]
        unread += P.get('unread', [])
    n_code = len(units)

    # API: 호출 후보 ↔ 제공 라우트. 한 지도에 서버가 여럿이면 경로만으로는 대상을 정할 수 없어, 근거가 강한 순서로 가린다 (resolve_calls 설명 참고)
    ext_cfg = cfg.get('external', [])
    api = resolve_calls(cfg, units, routes, http, hosts)
    agg = {}
    for u, mi, rule, r, how in api['resolved']:
        a = agg.setdefault((u, r['unit']), dict(w=0, routes=[], rules=[], sure=0))
        a['w'] += 1
        a['sure'] += how != 'affinity'
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
    # 소스 없는 시스템(레거시 등): 시스템마다 유닛 하나. 우리 코드가 부르는 API 가 멤버가 되고, 부르는 쪽에서 선이 온다
    ext_edges = {}
    for x in ext_cfg:
        ui, members, seen = len(units), [], {}
        for u, mi, method, segs, rule in api['external'].get(x['id'], []):
            shown = f"{method} /" + '/'.join('{' + g[1:] + '}' if g.startswith(':') else g for g in segs)
            key = (method, tuple(':' if g.startswith(':') else g for g in segs))
            if key not in seen:
                seen[key] = len(members)
                members.append(dict(n=shown, s=shown, l=0, e=0, route=shown))
            label = members[seen[key]]['route']
            d = ext_edges.setdefault((u, ui), dict(w=0, routes=[], rules=[]))
            d['w'] += 1
            if label not in d['routes']:
                d['routes'].append(label)
            if rule not in d['rules']:
                d['rules'].append(rule)
            if mi is not None:
                lst = units[u]['m'][mi].setdefault('h', [])
                if [ui, seen[key], label] not in lst:
                    lst.append([ui, seen[key], label])
        rec = dict(rt='unit', i=ui, id=f"{x['id']}:api", sys=x['id'], mod='외부 API', name=x.get('name', x['id']), kind='service', decl='external', file='', line=0, loc=0,
                   doc=x.get('desc') or '소스가 없는 시스템. 우리 코드가 이쪽으로 보내는 요청만 보인다.', src='')
        if members:
            rec['m'] = members[:120]
        units.append(rec)
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
        edges.append(dict(s=s, t=t, k='http', w=a['w'], routes=a['routes'], by=a['rules'], **({} if a['sure'] else {'guess': 1})))
    for (s, t), d in ext_edges.items():
        edges.append(dict(s=s, t=t, k='http', w=d['w'], routes=d['routes'], by=d['rules']))
    for (s, t), d in db_edges.items():
        edges.append(dict(s=s, t=t, k='db', w=d['r'] + d['w'], ops=('r' if d['r'] else '') + ('w' if d['w'] else ''), by=d['rules']))
    for (s, t), d in msg.items():
        edges.append(dict(s=s, t=t, k='msg', w=d['w'], keys=d['keys'], topic=d['kind'], by=d['rules']))
    for (s, t), w in svc_edges.items():
        edges.append(dict(s=s, t=t, k='svc', w=w))
    for (s, t), how in mirror.items():
        edges.append(dict(s=s, t=t, k='mirror', w=1, how=how))

    used = set(u['sys'] for u in units)
    systems = ([dict(id=s['id'], name=s['name'], sub=s.get('sub', ''), source=s['source']) for s in cfg['systems']]
               + [dict(id=x['id'], name=x.get('name', x['id']), sub=x.get('sub') or '소스 없음 · 보내는 요청만 표시', source='', ext=True) for x in ext_cfg] + [p for p in PSEUDO])
    sys_name = {s['id']: s['name'] for s in systems}
    pending = [dict(u=u, m=mi, call=f"{method} /" + '/'.join('{' + g[1:] + '}' if g.startswith(':') else g for g in norm_path(path)),
                    cands=[[sid, r['unit'], r['member'], f"{r['method']} {r['path']}"] for sid, r in per.items()]) for u, mi, method, path, rule, per in api['pending']]
    first = sources[0] if sources else {}
    meta = dict(rt='meta', repo=cfg.get('name', ''), systems=systems, sources=sources, analyzed=datetime.date.today().isoformat(),
                commit=first.get('commit', ''), branch=first.get('branch', ''), commit_date=first.get('commit_date', ''), dirty=first.get('dirty', False))
    if pending:
        meta['pending'] = pending[:300]          # 대상을 정하지 못한 호출. 틀린 선을 긋지 않고 사람이 고르게 둔다
    if api['offmap']:
        meta['offmap'] = api['offmap'][:60]      # 지도에 없는 주소로 가는 호출. 레거시로 등록할 후보
    if api['hosts_seen']:
        meta['hosts_seen'] = api['hosts_seen'][:120]
    open_routes = [r for r in routes if not any(e['k'] == 'http' and e['t'] == r['unit'] and f"{r['method']} {r['path']}" in e['routes'] for e in edges)]
    summary = dict(units=len(units), members=sum(len(u.get('m', [])) for u in units), edges=len(edges),
                   by_kind={k: sum(1 for e in edges if e['k'] == k) for k in sorted(set(e['k'] for e in edges))},
                   routes=len(routes), open_routes=len(open_routes), http_candidates=len(http),
                   resolved=api['how'], pending=len(pending), offmap=len(api['offmap']), twins=api['twins'], diag=api['diag'],
                   unread=unread_patterns(cfg, unread, api['diag'], routes),
                   findings=[dict(kind=k, id=sid, text=t) for k, sid, t in diag_findings(dict(diag=api['diag']), {s['id']: s['name'] for s in cfg['systems']})],
                   external={x['id']: dict(calls=len(api['external'].get(x['id'], [])), name=x.get('name', x['id'])) for x in ext_cfg},
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
        renamed, widened = upgrade_names(cfg), upgrade_rules(cfg)
        if renamed or widened:
            save_config(cfg)
        # 문법 패키지가 없어 읽을 수 없는 시스템은 건너뛰고 알린다. 설정이 "parser": "tree-sitter" 면 건너뛰지 않고 멈춘다.
        table = language_table(cfg.get('parser', 'auto'))
        skipped = [dict(id=s['id'], lang=s['lang'], need=table[s['lang']].split(': ')[1]) for s in cfg['systems']
                   if table.get(s['lang'], '').startswith('문법 없음') and cfg.get('parser') != 'tree-sitter']
        cfg = dict(cfg, systems=[s for s in cfg['systems'] if s['id'] not in {x['id'] for x in skipped}])
        results, report = [], []
        for g in groups_of(cfg):
            opts = dict(ns=g['ns'], api_hosts=cfg.get('api_hosts', []), rules=rules, parser=cfg.get('parser', 'auto'),
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
            parsers = res.get('stats', {}).get('parsers', {})
            report.append(dict(id=g['id'], systems=[s['id'] for s in g['systems']], files=nfiles, units=len(res['units']), status=status, ms=int((time.time() - t1) * 1000),
                               parser='tree-sitter' if parsers and all(v == 'tree-sitter' for v in parsers.values()) else '일부 tree-sitter' if 'tree-sitter' in parsers.values() else '내장'))
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
                        built_at=datetime.datetime.now().isoformat(timespec='seconds'), engine=version, data_hash=data_hash, sources=sources, groups=report, skipped=skipped, summary=summary,
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
    if widened:
        print(f"  모바일 앱 {', '.join(widened)}: 코드에 적힌 경로 문자열도 호출 후보로 읽게 했습니다.")
    for sid, old, new in renamed:
        print(f"  시스템 이름: {old} → {new}  (앱 이름을 찾아 바꿨습니다. 설정에서 고칠 수 있습니다)")
    for r in report:
        print(f"  묶음 {r['id']:<22} {'다시 분석' if r['status'] == 'analyzed' else '재사용   '} 파일 {r['files']:>4} · 유닛 {r['units']:>4} · {r['ms']:>5}ms · 파서 {r.get('parser', '내장')}")
    for x in skipped:
        print(f"  건너뜀 {x['id']}: {x['lang']} 문법이 없습니다. 설치: pip install tree-sitter {x['need']}  (requirements-parsers.txt 참고)")
    print(f"결과: 유닛 {summary['units']} · 메서드 {summary['members']} · 연결 {summary['edges']} {summary['by_kind']}")
    print(f"접점: 제공 API {summary['routes']}개 중 호출자 없는 것 {summary['open_routes']}개 · 호출 후보 {summary['http_candidates']}개")
    how, names = summary.get('resolved', {}), dict(unique='맞는 서버가 하나', host='주소', calls='설정', self='자기 시스템', mention='코드에 적힌 주소', source='같은 저장소', affinity='주로 부르는 서버(추정)', prefix='접두어를 맞춰서', external='레거시')
    if how:
        print('호출 대상: ' + ' · '.join(f'{names[k]} {how[k]}' for k in names if how.get(k)) + (f" · 확인 필요 {summary['pending']}" if summary.get('pending') else ''))
    for sid, x in summary.get('external', {}).items():
        print(f"  레거시 {x['name']}: 이쪽으로 보내는 호출 {x['calls']}개" + ('' if x['calls'] else '  ← 맞는 호출이 없습니다. 주소(hosts)·경로 꼴(paths)을 확인하세요'))
    if summary.get('pending'):
        print(f"  확인 필요 {summary['pending']}개: 경로가 여러 서버에 맞아 선을 긋지 않았습니다. 화면의 '확인이 필요한 호출'에서 고르거나 설정의 link.calls 에 적어 주세요.")
    if summary.get('offmap'):
        print(f"  지도에 없는 주소로 가는 호출이 {summary['offmap']}곳 있습니다. 레거시라면 설정의 external 에 등록하면 지도에 나옵니다.")
    for a, b in summary.get('twins', []):
        print(f"  주의: {a} 와 {b} 의 API 목록이 똑같습니다. 같은 서버를 두 번 넣었다면 하나를 빼세요.")
    print_diag(summary, {s['id']: s['name'] for s in cfg['systems']})
    if unchanged:
        print(f"변경 없음. 버전 {manifest['version']} 유지 · {time.time() - t0:.1f}초")
    else:
        c = manifest['change_counts']
        print(f"새 버전 {manifest['version']} · 추가 {c['added']} · 삭제 {c['removed']} · 변경 {c['changed']} · {time.time() - t0:.1f}초")
    return manifest


def app_name(kind, start, top):
    """모바일 앱의 이름. start(소스 폴더)에서 top(저장소 맨 위)까지 올라가며 찾는다.
    화면에 보이는 이름(iOS 의 표시 이름, Android 의 app_name)을 먼저 쓰고, 없으면 프로젝트 이름을 쓴다. 못 찾으면 빈 문자열."""
    def grab(path, pattern):
        try:
            text = open(path, encoding='utf-8', errors='replace').read(800_000)
        except OSError:
            return ''
        for m in re.finditer(pattern, text):
            v = m.group(1).strip().strip('"').strip()
            if v and not re.search(r'[$@{}]', v):       # $(PRODUCT_NAME) · @string/… 처럼 다른 곳을 가리키는 값은 건너뛴다
                return v
        return ''
    d, top, fallback = os.path.abspath(start), os.path.abspath(top), ''
    while True:
        if kind == 'ios':
            projects = sorted(glob.glob(os.path.join(d, '*.xcodeproj')))
            for proj in projects:
                shown = grab(os.path.join(proj, 'project.pbxproj'), r'INFOPLIST_KEY_CFBundleDisplayName\s*=\s*([^;\n]+);')
                if shown:
                    return shown
                fallback = fallback or os.path.splitext(os.path.basename(proj))[0]
            shown = grab(os.path.join(d, 'Info.plist'), r'<key>CFBundleDisplayName</key>\s*<string>([^<]*)</string>')
            if shown:
                return shown
            fallback = fallback or grab(os.path.join(d, 'Package.swift'), r'\bname:\s*"([^"]+)"')
            if projects:
                break
        else:
            shown = grab(os.path.join(d, 'res', 'values', 'strings.xml'), r'<string\s+name="app_name"[^>]*>([^<]+)</string>')
            if shown:
                return shown
            for f in ('settings.gradle.kts', 'settings.gradle'):
                fallback = fallback or grab(os.path.join(d, f), r'rootProject\.name\s*=\s*["\']([^"\']+)["\']')
        if d == top or os.path.dirname(d) == d or not d.startswith(top):
            break
        d = os.path.dirname(d)
    return fallback


OLD_APP_NAMES = {'iOS 앱': 'ios', 'Android 앱': 'android'}      # 예전에 붙이던 이름. 앱이 여럿이면 구분이 안 된다


def app_label(kind, name):
    """지도에 보일 모바일 시스템 이름: 앱 이름 + 플랫폼. 앱 이름을 못 찾으면 예전처럼 플랫폼만 적는다."""
    plat = 'iOS' if kind == 'ios' else 'Android'
    return (name if plat.lower() in name.lower() else f'{name} {plat}') if name else f'{plat} 앱'


def upgrade_rules(cfg):
    """예전에 등록된 모바일 앱에도 '경로 문자열 그물'을 켠다. 새로 추가하는 앱에는 detect 가 처음부터 넣는다."""
    changed = []
    for s in cfg['systems']:
        rules = s.get('rules') or []
        if ('client-path-arg' in rules or 'client-base-url' in rules) and 'client-slash-literal' not in rules and 'client-slash-literal' not in (s.get('rules_off') or []):
            s['rules'] = rules + ['client-slash-literal']
            changed.append(s['id'])
    return changed


def upgrade_names(cfg):
    """예전 이름('iOS 앱' · 'Android 앱')으로 등록된 모바일 시스템을 앱 이름으로 바꾼다. 사람이 고친 이름은 건드리지 않는다."""
    changed = []
    tops = {s['name']: source_root(cfg, s) for s in cfg['sources']}
    for s in cfg['systems']:
        kind, top = OLD_APP_NAMES.get(s['name']), tops.get(s['source'])
        if kind and top and os.path.isdir(top) and s.get('roots'):
            new = app_label(kind, app_name(kind, os.path.join(top, s['roots'][0]), top))
            if new != s['name']:
                changed.append((s['id'], s['name'], new))
                s['name'] = new
    return changed


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
                found.append(dict(id='ios', name=app_label('ios', app_name('ios', os.path.join(root, roots[0]), cur)), sub='Swift', lang='swift', ns='apple', roots=roots, tests=tests, rules=['client-path-arg', 'client-slash-literal']))
            dirs[:] = []
        elif any(f in ('build.gradle', 'build.gradle.kts', 'pom.xml') for f in fs):
            for lang, sub, ext in (('kotlin', 'src/main/kotlin', '.kt'), ('kotlin', 'src/main/java', '.kt'), ('java', 'src/main/java', '.java')):   # Android 는 Kotlin 을 java 폴더에 두기도 한다
                roots = sorted(rel(p) for p in glob.glob(os.path.join(cur, '**', sub), recursive=True) if has(p, ext, 12))
                if roots:
                    android = any(os.path.exists(os.path.join(root, r, '..', 'AndroidManifest.xml')) for r in roots)
                    found.append(dict(id='android' if android else base.lower(), name=app_label('android', app_name('android', os.path.join(root, roots[0]), cur)) if android else base, sub=('Kotlin' if lang == 'kotlin' else 'Java') + (' · Android' if android else ' · JVM'),
                                      lang=lang, roots=roots, tests=[[r.replace('/main/', '/test/'), ext] for r in roots if os.path.isdir(os.path.join(root, r.replace('/main/', '/test/')))],
                                      rules=['client-base-url', 'client-slash-literal'] if android else []))
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
            found.append(dict(id=base.lower(), name=base, sub='Go', lang='go', roots=[rel(cur)]))
            dirs[:] = []
    return found


def unread_patterns(cfg, raw, diag, routes=()):
    """엔진이 모은 '읽지 못한 꼴'을 화면에 보일 목록으로. 경로처럼 생긴 문자열이 어노테이션·호출 인자로 놓여 있는데 어느 규칙도 읽지 않은 자리다.
    hint 는 그 꼴이 API 정의(provide)로 보이는지 호출(consume)로 보이는지에 대한 어림이다. 화면에서는 둘 다 고를 수 있다."""
    apps = {s['id'] for s in cfg['systems'] if any(r.startswith('client-') for r in s.get('rules') or [])}
    served = {x['id'] for x in diag['systems'] if x['routes']}
    out = []
    for sid, kind, name, argi, n, ex, *more in raw:
        beside = more[0] if more else []
        if n < 3:
            continue
        if sum(1 for v in ex if match_route(routes, None, norm_path(v))) * 2 >= len(ex):      # 그 문자열들이 이미 있는 API 와 같다: 다른 방법(경로 문자열 그물 등)으로 이어진 꼴이라 알릴 것이 없다
            continue
        ex = ex[:3]
        hint = 'consume' if sid in apps else 'provide' if kind == 'ann' or sid in served and re.search(r'(?i)(rout|mapping|handle|register|endpoint|path)', name) else 'consume'
        out.append(dict(sys=sid, kind=kind, name=name, argi=argi, n=n, ex=ex, hint=hint, beside=[[b, k] for b, k in beside]))
    return sorted(out, key=lambda x: -x['n'])[:24]


def custom_rule(sid, kind, name, argi, role):
    """읽지 못한 꼴 하나를 규칙으로. 그 꼴이 나온 시스템에만 걸리게 systems 로 좁힌다."""
    slug = re.sub(r'[^A-Za-z0-9]+', '-', name).strip('-')
    base = dict(id=f"{sid}-{kind}-{slug}-{role}", kind='http', role=role, systems=[sid], unread_only=True, made=dict(sys=sid, kind=kind, name=name, argi=argi))
    if kind == 'ann':            # 어노테이션에 경로가 적힌 꼴. 클래스에 같은 어노테이션이 있으면 접두 경로로 본다
        return dict(base, type='annotation', names={name: '*'}, require_path=True, prefix=dict(names=[name]), unit_kind='controller' if role == 'provide' else 'client')
    last = name.split('.')[-1]
    verb = last.upper() if last.lower() in ('get', 'post', 'put', 'delete', 'patch', 'head') else None
    rule = dict(base, type='call', match=r'(?:^|\.)' + re.escape(name) + '$', arg=argi, lit=r'^(/|https?://|[A-Za-z{$])')
    rule.update(method=verb) if verb else rule.update(default_method='ALL' if role == 'provide' else 'GET')
    return rule


def custom_pack_path(cfg):
    return os.path.join(cfg['_dir'], cfg.get('rules_dir', 'rules'), 'custom.json')


def load_custom(cfg):
    p = custom_pack_path(cfg)
    return json.load(open(p, encoding='utf-8')) if os.path.exists(p) else dict(id='custom', title='화면에서 만든 규칙 (읽지 못한 꼴을 골라 만든 것)', rules=[])


def save_custom(cfg, pack):
    p = custom_pack_path(cfg)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    if pack['rules']:
        write_atomic(p, json.dumps(pack, ensure_ascii=False, indent=2) + '\n')
    elif os.path.exists(p):
        os.remove(p)


APP_LANGS = ('swift', 'kotlin', 'java', 'ts', 'js')
READS = '읽을 수 있는 서버 API: Spring MVC 어노테이션 · JAX-RS(@Path) · NestJS · Express · FastAPI·Flask · Go.  앱 호출: Retrofit 어노테이션 · axios·fetch · requests · 모바일 앱 코드에 적힌 경로 문자열("/a/b.do" · "a/b" · "https://주소/a/b").'


def diag_findings(summary, names):
    """연결 진단에서 사람이 봐야 할 것만 문장으로. (종류, 시스템 id, 문장) 목록. 화면의 설정 패널도 같은 판단을 쓴다."""
    d = summary.get('diag') or {}
    S, out = d.get('systems', []), []
    nm = lambda sid: names.get(sid, sid)
    if not S:
        return out
    if not sum(x['routes'] for x in S) and any(x['calls'] for x in S):
        out.append(('no-routes', '', '서버가 내주는 API 를 하나도 읽지 못했습니다. 서버 프레임워크의 규칙이 없거나 서버 소스가 빠져 있습니다.'))
    for p in d.get('prefixes', []):
        out.append(('prefix', p['frm'], f"{nm(p['frm'])} → {nm(p['to'])}: " + (f"호출 경로 앞의 {p['prefix']} 를 떼면 맞아서 {p['n']}개를 이었습니다." if p['kind'] == 'strip'
                                                                              else f"호출에는 없는 {p['prefix']} 가 API 경로 앞에 붙어 있어, 붙여서 {p['n']}개를 이었습니다.")))
    for x in S:
        if x['routes'] >= 3 and x.get('open') == x['routes'] and len(S) >= 2:      # 요청을 처리하는 곳은 있는데 거기로 들어오는 연결이 하나도 없다
            out.append(('no-callers', x['id'], f"{nm(x['id'])}: API {x['routes']}개를 내주는데 부르는 곳을 하나도 찾지 못했습니다. 이 서버를 부르는 앱의 호출을 읽지 못했거나 그 앱이 지도에 없습니다."))
        if x['calls'] >= 3 and not x['linked'] and not x['pending'] and not x['external']:
            out.append(('mismatch', x['id'], f"{nm(x['id'])}: 호출 {x['calls']}개를 읽었지만 어느 API 와도 경로가 맞지 않습니다. 호출 예와 API 예를 견줘 보세요."))
        elif not x['routes'] and not x['linked'] and not x['pending'] and not x['external'] and x.get('maybe', 0) >= 3 and x['lang'] in APP_LANGS and sum(y['routes'] for y in S):
            out.append(('maybe', x['id'], f"{nm(x['id'])}: 경로로 보이는 문자열이 {x['maybe']}개 있지만 어느 API 와도 맞지 않습니다. 이 앱이 부르는 서버가 지도에 없거나 경로가 다를 수 있습니다. 문자열 예와 API 예를 견줘 보세요."))
        elif not x['routes'] and not x['calls'] and x.get('hosts') and x['lang'] in APP_LANGS:      # 코드에 서버 주소는 적혀 있는데 호출이 하나도 안 잡혔다
            out.append(('silent', x['id'], f"{nm(x['id'])}: 코드에 서버 주소가 {x['hosts']}개 적혀 있는데 API 호출은 하나도 잡히지 않았습니다. 호출 방식을 아직 읽지 못하는 것으로 보입니다."))
    return out


def print_diag(summary, names, full=False):
    d, finds, un = summary.get('diag') or {}, diag_findings(summary, names), summary.get('unread') or []
    if not d.get('systems') or not (full or finds or un):
        return
    print('연결 진단')
    flagged = {sid for _, sid, _ in finds}
    for x in d['systems']:
        if not (x['routes'] or x['calls'] or full or x['id'] in flagged):
            continue
        parts = ([f"내주는 API {x['routes']}개" + (f" (부르는 곳 없는 것 {x['open']})" if x.get('open') else '')] if x['routes'] else []) + ([f"부르는 호출 {x['calls']}개 중 이어진 것 {x['linked']}" + (f" · 확인 필요 {x['pending']}" if x['pending'] else '')
                 + (f" · 레거시 {x['external']}" if x['external'] else '') + (f" · 못 이은 것 {x['unmatched']}" if x['unmatched'] else '')] if x['calls'] else [])
        print(f"  {names.get(x['id'], x['id'])}: " + (' · '.join(parts) or '내주는 API 도, 부르는 호출도 없음'))
        if x['routes'] and (full or any(k in ('mismatch', 'maybe') for k, _, _ in finds)):
            print('      API 예: ' + ', '.join(x['route_ex']))
        if x['unmatched'] and (full or x['id'] in flagged):
            print('      못 이은 호출 예: ' + ', '.join(x['call_ex']))
        if x.get('maybe') and (full or x['id'] in flagged) and not x['linked']:
            print(f"      경로로 보이는 문자열 {x['maybe']}개 · 예: " + ', '.join(x['maybe_ex']))
    for kind, sid, text in finds:
        print('  → ' + text)
    if any(k in ('no-routes', 'silent') for k, _, _ in finds):
        print('  ' + READS)
    if un:
        print('읽지 못한 꼴  (경로처럼 생긴 문자열이 놓여 있지만 어느 규칙도 읽지 않은 자리)')
        for x in un[:12]:
            what = f"어노테이션 @{x['name']}" if x['kind'] == 'ann' else f"호출 {x['name']}(…)" + (f" {x['argi'] + 1}번째 인자" if x['argi'] else '')
            print(f"  {names.get(x['sys'], x['sys'])}: {what} · {x['n']}개 · 예: {', '.join(x['ex'])}" + (' · 함께 붙은 것: ' + ', '.join(f'@{b} {k}' for b, k in x['beside']) if x.get('beside') else ''))
        print('  설정 패널의 연결 진단에서 꼴을 골라 "API 정의로 읽기"나 "API 호출로 읽기"를 누르면 규칙이 만들어집니다.')


def cmd_diagnose(cfg, args):
    mpath = os.path.join(cfg['_dir'], cfg.get('out', '.'), 'atlas-manifest.json')
    if not os.path.exists(mpath):
        raise SystemExit('아직 결과가 없습니다. sync 를 먼저 실행하세요.')
    summary = json.load(open(mpath, encoding='utf-8')).get('summary', {})
    if not summary.get('diag'):
        raise SystemExit('이 결과에는 진단 정보가 없습니다. sync 를 한 번 다시 실행하세요.')
    print_diag(summary, {s['id']: s['name'] for s in cfg['systems']}, full=True)
    if not diag_findings(summary, {}) and not summary.get('unread'):
        print('  눈에 띄는 문제는 없습니다.')


def adopt_systems(cfg, name, systems):
    """새 소스에서 찾은 시스템에 소스 이름을 적고, 이미 있는 시스템과 id 가 겹치면 소스 이름을 앞에 붙인다."""
    taken = {s['id'] for s in cfg['systems']} | {x['id'] for x in cfg.get('external', [])}
    for s in systems:
        s['source'] = name
        s.setdefault('ns', s['id'])
        if s['id'] in taken or s['id'] in ('ios', 'android') and cfg['systems']:
            s['id'] = f"{name}-{s['id']}"
            s['ns'] = f"{name}-{s['ns']}"
        taken.add(s['id'])
    return systems


def cmd_add(cfg, args):
    target = args.target
    is_git = bool(re.match(r'^(https?://|git@|ssh://|file://)', target)) or target.endswith('.git')
    name = args.name or re.sub(r'\.git$', '', os.path.basename(target.rstrip('/'))).lower()
    if any(s['name'] == name for s in cfg['sources']):
        raise SystemExit(f'이미 등록된 소스 이름입니다: {name}')
    src = dict(name=name, git=target, **({'branch': args.branch} if args.branch else {})) if is_git else dict(name=name, path=os.path.abspath(os.path.expanduser(target)))     # 어느 폴더에서 실행해도 같은 곳을 가리키게 절대 경로로 적는다
    same = next((s['name'] for s in cfg['sources'] if (is_git and s.get('git') == target) or (not is_git and s.get('path') and os.path.realpath(s['path']) == os.path.realpath(src['path']))), None)
    if same:
        raise SystemExit(f'이미 추가된 소스입니다: {same}. 같은 서버는 한 번만 넣어야 지도에 하나로 나옵니다.')
    if is_git and not args.dry_run:
        prepare_source(cfg, src)
    root = source_root(cfg, src) if not (is_git and args.dry_run) else None
    systems = adopt_systems(cfg, name, detect(root, name) if root and os.path.isdir(root) else [])
    table = language_table(cfg.get('parser', 'auto'))
    note = lambda lang: '   ← 어댑터 없음: 분석에서 빠짐' if lang not in table else f"   ← 문법 없음: pip install tree-sitter {table[lang].split(': ')[1]} 전에는 건너뜀" if table[lang].startswith('문법 없음') else ''
    print(f"소스 {name}: {'git ' + target if is_git else root}")
    for s in systems:
        print(f"  시스템 {s['id']:<18} {s['sub']:<20} 언어 {s['lang']:<7} 루트 {', '.join(s['roots'][:3])}{' …' if len(s['roots']) > 3 else ''}{note(s['lang'])}")
    if not systems:
        print('  알아볼 수 있는 시스템이 없습니다. 설정 파일에 직접 적어 주세요.')
    if args.dry_run:
        print('(미리보기라 설정은 바꾸지 않았습니다)')
        return
    cfg['sources'].append(src)
    cfg['systems'] += [s for s in systems if s['lang'] in table]
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
    args.viewer = args.viewer or os.path.join(os.path.dirname(HERE), 'code-atlas.html')
    args.out = args.out or os.path.join(cfg['_dir'], 'site')
    body = open(args.viewer, encoding='utf-8').read()
    os.makedirs(args.out, exist_ok=True)
    head = ('<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">'
            '<style>body{margin:0}[hidden]{display:none!important}</style></head><body>\n')
    write_atomic(os.path.join(args.out, 'index.html'), head + body + '\n</body></html>\n')
    src, made = os.path.join(cfg['_dir'], cfg.get('out', '.')), ['index.html']
    for f in ('atlas-data.json', 'atlas-manifest.json', 'atlas-notes.json', 'atlas-runtime.json'):      # 메모(사람이 쓴 설명)와 실측 응답은 있을 때만
        if f in ('atlas-notes.json', 'atlas-runtime.json') and not os.path.exists(os.path.join(src, f)):
            continue
        write_atomic(os.path.join(args.out, f), open(os.path.join(src, f), encoding='utf-8').read())
        made.append(f)
    print(f'{args.out} 에 {", ".join(made)} 을 만들었습니다. 이 폴더를 사내 웹 서버에 올리면 됩니다.')


# ───────────────────────────── 실측 응답 (접속 로그) ─────────────────────────────
LOG_KEYS = dict(method=('method', 'verb', 'http_method', 'request_method', 'requestmethod', 'httpmethod'),
                path=('path', 'uri', 'url', 'request_uri', 'requesturl', 'request_path', 'target', 'endpoint'),
                status=('status', 'status_code', 'statuscode', 'response_code', 'http_status', 'responsestatus', 'response_status'),
                ms=('duration_ms', 'latency_ms', 'elapsed_ms', 'response_time_ms', 'took_ms', 'request_time'))
LOG_CLF = re.compile(r'"(GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS)\s+(\S+)[^"]*"\s+(\d{3})\b')
LOG_LOOSE = re.compile(r'\b(GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS)\s+((?:https?://[^/\s]+)?/\S*).*?(?<![\d.:/])([1-5]\d{2})(?![\d.])')


def parse_log_line(line, custom=None):
    """접속 로그 한 줄 → (메서드, 경로, 상태 코드, 걸린 시간 ms | None). 알아볼 수 없으면 None.
    JSON 한 줄, nginx·Apache 기본 형식("GET /x HTTP/1.1" 200), 'GET /x … 200' 꼴의 앱 로그를 차례로 시도한다."""
    line = line.strip()
    if not line:
        return None
    if custom is not None:
        m = custom.search(line)
        if not m:
            return None
        g = m.groupdict()
        return (g.get('method') or 'GET').upper(), g['path'], int(g['status']), float(g['ms']) if g.get('ms') else None
    if line[0] == '{':
        try:
            o = json.loads(line)
        except ValueError:
            o = None
        if isinstance(o, dict):
            low = {}
            for k, v in o.items():                      # 한 겹 안쪽(httpRequest, request, response …)까지 본다
                if isinstance(v, dict):
                    for k2, v2 in v.items():
                        low.setdefault(k2.lower(), v2)
            low.update({k.lower(): v for k, v in o.items() if not isinstance(v, dict)})
            pick = lambda names: next((low[n] for n in names if low.get(n) not in (None, '')), None)
            method, path, status, ms = pick(LOG_KEYS['method']), pick(LOG_KEYS['path']), pick(LOG_KEYS['status']), pick(LOG_KEYS['ms'])
            if path is not None and re.fullmatch(r'[1-5]\d{2}', str(status or '')):
                try:
                    ms = float(ms) * (1000 if 'request_time' in low and ms == low.get('request_time') else 1) if ms is not None else None
                except (TypeError, ValueError):
                    ms = None
                return str(method or 'GET').upper(), str(path), int(status), ms
            return None
    m = LOG_CLF.search(line) or LOG_LOOSE.search(line)
    return (m.group(1), m.group(2), int(m.group(3)), None) if m else None


def route_table(records):
    """분석 결과에서 '제공하는 API' 표를 만든다: 메서드, 경로 조각, 그것을 처리하는 유닛·메서드."""
    out = []
    for u in records:
        for m in (u.get('m') or []) if u.get('rt') == 'unit' else []:
            r = m.get('route') or ''
            if ' ' in r:
                method, path = r.split(' ', 1)
                out.append(dict(method=method.upper(), segs=norm_path(path), id=u['id'], m=m['n'], route=r))
    return out


def log_segments(path, strip=()):
    path = re.sub(r'^https?://[^/]+', '', path.split('?')[0].split('#')[0])
    for pre in strip:
        if path == pre or path.startswith(pre.rstrip('/') + '/'):
            path = path[len(pre.rstrip('/')):]
            break
    return [s for s in path.strip('/').split('/') if s]


def match_log(table, method, segs):
    """실제 요청 경로(값이 들어 있다)에 맞는 라우트. 글자가 그대로 맞는 조각이 많은 쪽을 고른다."""
    best, best_score = None, -1
    for r in table:
        if len(r['segs']) != len(segs) or r['method'] not in (method, 'ALL', '*', 'ANY'):
            continue
        score = 0
        for a, b in zip(r['segs'], segs):
            if a.startswith(':'):
                continue
            if a != b:
                score = None
                break
            score += 2
        if score is None:
            continue
        score += 1 if r['method'] == method else 0
        if score > best_score:
            best, best_score = r, score
    return best


def shape_of(method, segs):
    """맞는 라우트가 없는 경로를 모을 때 쓰는 모양. 숫자·긴 식별자 조각은 값이므로 가린다."""
    return method + ' /' + '/'.join(':id' if re.fullmatch(r'\d+|[0-9a-fA-F-]{16,}|[A-Za-z0-9_-]{24,}', s) else s for s in segs[:8])


def runtime_summary(table_size, events, source, window, abnormal):
    """(라우트, 코드, ms) 사건 목록 → atlas-runtime.json 내용. 값(쿼리, 본문, 사용자 정보)은 담지 않고 횟수만 담는다."""
    agg, unmatched, matched = {}, {}, 0
    for r, shape, code, ms in events:
        if r is None:
            unmatched[shape] = unmatched.get(shape, 0) + 1
            continue
        matched += 1
        a = agg.setdefault((r['id'], r['m'], r['route']), dict(codes={}, ms=[]))
        a['codes'][code] = a['codes'].get(code, 0) + 1
        if ms is not None:
            a['ms'].append(ms)
    routes = []
    for (uid, mn, route), a in agg.items():
        rec = dict(id=uid, m=mn, route=route, n=sum(a['codes'].values()), codes={str(c): n for c, n in sorted(a['codes'].items())})
        if a['ms']:
            ms = sorted(a['ms'])
            rec['p95'] = round(ms[min(len(ms) - 1, int(len(ms) * 0.95))], 1)
        routes.append(rec)
    routes.sort(key=lambda r: (-sum(n for c, n in r['codes'].items() if int(c) >= abnormal), -r['n']))
    return dict(generated=datetime.datetime.now().isoformat(timespec='seconds'), source=source, window=window, total=len(events), matched=matched,
                abnormal_from=abnormal, known_routes=table_size, routes=routes,
                unmatched=sorted(unmatched.items(), key=lambda kv: -kv[1])[:15])


def cmd_ingest(cfg, args):
    """접속 로그를 읽어 라우트별 응답 코드 횟수를 atlas-runtime.json 으로 만든다. 화면은 이것을 지도 위에 얹는다."""
    out_dir = os.path.normpath(os.path.join(cfg['_dir'], cfg.get('out', '.')))
    rpath = os.path.join(out_dir, 'atlas-runtime.json')
    if args.clear:
        if os.path.exists(rpath):
            os.remove(rpath)
        print('실측 응답을 지웠습니다.')
        return
    dpath = os.path.join(out_dir, 'atlas-data.json')
    if not os.path.exists(dpath):
        raise SystemExit('아직 분석 결과가 없습니다. 먼저 sync 를 실행하세요.')
    if not args.logs:
        raise SystemExit('로그 파일을 주세요. 표준 입력은 - 로 적습니다 (예: tail -f access.log | atlas.py -w <이름> ingest - --follow).')
    table = route_table(json.load(open(dpath, encoding='utf-8')))
    rt_cfg = cfg.get('runtime', {})
    custom = re.compile(args.regex or rt_cfg['regex']) if (args.regex or rt_cfg.get('regex')) else None
    strip = tuple(args.strip_prefix or rt_cfg.get('strip_prefix', []))
    source = args.label or ', '.join('표준 입력' if p == '-' else os.path.basename(p) for p in args.logs)

    def event(line):
        rec = parse_log_line(line, custom)
        if rec is None:
            return None
        method, path, code, ms = rec
        segs = log_segments(path, strip)
        r = match_log(table, method, segs)
        return (r, None if r else shape_of(method, segs), code, ms)

    def report(doc, skipped):
        bad = sum(n for r in doc['routes'] for c, n in r['codes'].items() if int(c) >= args.abnormal)
        print(f"요청 {doc['total']}건 · 라우트에 맞은 것 {doc['matched']}건 · 비정상({args.abnormal} 이상) {bad}건 · 알아보지 못한 줄 {skipped}")
        for r in [r for r in doc['routes'] if any(int(c) >= args.abnormal for c in r['codes'])][:8]:
            print(f"  비정상 {r['route']:<40} " + ' '.join(f'{c}×{n}' for c, n in r['codes'].items() if int(c) >= args.abnormal) + f"  ({r['id']}.{r['m']})")
        if doc['unmatched'] and doc['matched'] < doc['total']:
            print('  라우트를 찾지 못한 경로(상위): ' + ', '.join(f'{k} ×{n}' for k, n in doc['unmatched'][:5]))
            print('  경로 앞에 게이트웨이 접두어가 붙어 있으면 --strip-prefix /접두어 를 주세요.')

    if not args.follow:
        events, skipped = [], 0
        for p in args.logs:
            fh = sys.stdin if p == '-' else open(p, encoding='utf-8', errors='replace')
            for line in fh:
                e = event(line)
                if e is None:
                    skipped += 1 if line.strip() else 0
                else:
                    events.append(e)
        if args.last:
            events = events[-args.last:]
        doc = runtime_summary(len(table), events, source, f'{len(events)}건' + (' (마지막)' if args.last else ''), args.abnormal)
        write_atomic(rpath, json.dumps(doc, ensure_ascii=False, indent=1))
        report(doc, skipped)
        print(f'{rpath} 에 적었습니다. 열려 있는 화면은 10초 안에 반영합니다.')
        return
    # 따라가기: 새로 들어오는 줄을 읽으며 최근 N초 창을 몇 초마다 다시 적는다
    import collections
    import threading
    queue, done = collections.deque(), threading.Event()

    def reader(p):
        if p == '-':
            for line in sys.stdin:
                queue.append(line)
            done.set()
            return
        with open(p, encoding='utf-8', errors='replace') as fh:
            fh.seek(0, 2)                                  # 파일은 끝에서부터: 지금부터 들어오는 줄만 본다
            while True:
                line = fh.readline()
                if line:
                    queue.append(line)
                else:
                    time.sleep(0.4)

    for p in args.logs:
        threading.Thread(target=reader, args=(p,), daemon=True).start()
    recent, skipped = collections.deque(), 0
    print(f'{source} 를 따라가는 중입니다. 최근 {args.window}초를 {args.every}초마다 적습니다. 끝내려면 Ctrl+C.')
    try:
        while True:
            now = time.time()
            while queue:
                e = event(queue.popleft())
                if e is None:
                    skipped += 1
                else:
                    recent.append((now, e))
            while recent and recent[0][0] < now - args.window:
                recent.popleft()
            doc = runtime_summary(len(table), [e for _, e in recent], source, f'최근 {args.window}초', args.abnormal)
            write_atomic(rpath, json.dumps(doc, ensure_ascii=False, indent=1))
            if done.is_set() and not queue:
                report(doc, skipped)
                return
            time.sleep(args.every)
    except KeyboardInterrupt:
        print('\n끝냈습니다. 마지막으로 적은 내용은 남아 있습니다 (지우려면 ingest --clear).')


def make_server(cfg, port=0):
    """작업 공간의 지도를 보여 줄 로컬 서버. 이 컴퓨터(127.0.0.1)에서만 열리고, 데이터 파일은 작업 공간에서 바로 읽어 sync 결과가 곧장 보인다.

    /api/… 는 화면의 설정 패널이 쓰는 주소다(폴더 추가, 레거시 등록, 부르는 서버 지정, 배치 저장, 다시 분석).
    다른 웹 페이지가 이 주소를 몰래 부르지 못하게, 서버를 띄울 때 만든 토큰을 요청마다 확인하고 Host 가 이 컴퓨터인지도 본다.
    토큰은 /api/state 로만 내주는데, 다른 출처의 페이지는 그 응답을 읽을 수 없다."""
    import http.server
    import secrets
    import threading
    site, live, cfg_path = os.path.join(cfg['_dir'], 'site'), os.path.join(cfg['_dir'], cfg.get('out', '.')), cfg['_path']
    token, lock = secrets.token_urlsafe(18), threading.Lock()
    job = dict(running=False, kind='', log=[], rc=None)

    def run_job(kind, argv):
        with lock:
            if job['running']:
                raise SystemExit('이미 실행 중인 작업이 있습니다.')
            job.update(running=True, kind=kind, log=[], rc=None)

        def work():
            try:
                p = subprocess.Popen([sys.executable, os.path.abspath(__file__), '--config', cfg_path, *argv], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
                for line in p.stdout:
                    job['log'].append(line.rstrip())
                    del job['log'][:-200]
                job['rc'] = p.wait()
            except Exception as e:           # 실행 자체가 안 된 경우도 화면에 보이게 남긴다
                job['log'].append(str(e))
                job['rc'] = 1
            job['running'] = False
        threading.Thread(target=work, daemon=True).start()
        return dict(started=True)

    def state():
        c = load_config(cfg_path)
        mpath = os.path.join(live, 'atlas-manifest.json')
        man = json.load(open(mpath, encoding='utf-8')) if os.path.exists(mpath) else {}
        table = language_table(c.get('parser', 'auto'))
        return dict(token=token, name=c.get('name', ''), home=os.path.expanduser('~'),
                    sources=[dict(name=x['name'], path=x.get('path', ''), git=x.get('git', ''), systems=[y['id'] for y in c['systems'] if y['source'] == x['name']]) for x in c['sources']],
                    systems=[dict(id=y['id'], name=y['name'], sub=y.get('sub', ''), lang=y['lang'], source=y['source'], hosts=y.get('hosts', []), prefix=y.get('prefix', []),
                                  skipped=table.get(y['lang'], '').startswith('문법 없음')) for y in c['systems']],
                    external=c.get('external', []), link=dict(calls=c['link'].get('calls', {}), never=c['link'].get('never', {})),
                    built_at=man.get('built_at', ''), version=man.get('version', ''), summary=man.get('summary', {}), job=dict(job, log=job['log'][-60:]),
                    findings=[dict(kind=k, id=sid, text=t) for k, sid, t in diag_findings(man.get('summary', {}), {y['id']: y['name'] for y in c['systems']})], reads=READS,
                    custom=[dict(id=r.get('id'), role=r.get('role'), **r.get('made', {})) for r in load_custom(c)['rules']])

    def words(v, limit=30):
        """화면에서 온 목록 값: 문자열 목록이나 쉼표로 나눈 문자열을 받아 깨끗한 목록으로."""
        items = v if isinstance(v, list) else re.split(r'[,\n]', str(v or ''))
        return [str(x).strip()[:200] for x in items if str(x).strip()][:limit]

    def api(name, body):
        c = load_config(cfg_path)
        if name == 'job':
            return dict(job, log=job['log'][-60:])
        if name == 'ls':                      # 폴더 고르기: 이름만 내준다. 파일 내용은 읽지 않는다
            path = os.path.abspath(os.path.expanduser(str(body.get('path') or '~')))
            if body.get('child'):
                path = os.path.join(path, os.path.basename(str(body['child'])))
            if body.get('parent'):
                path = os.path.dirname(path)
            if not os.path.isdir(path):
                raise SystemExit(f'폴더가 아닙니다: {path}')
            try:
                names = sorted(os.listdir(path), key=str.lower)
            except OSError as e:
                raise SystemExit(f'이 폴더를 열 수 없습니다: {e.strerror}')
            names = [n for n in names if not n.startswith('.')]
            out = dict(path=path, parent=os.path.dirname(path) if os.path.dirname(path) != path else '', dirs=[n for n in names if os.path.isdir(os.path.join(path, n))][:600])
            have = {os.path.realpath(x['path']): x['name'] for x in c['sources'] if x.get('path')}         # 이미 추가한 폴더는 표시해 두 번 넣지 않게 한다
            out['added'] = [n for n in out['dirs'] if os.path.realpath(os.path.join(path, n)) in have]
            out['here'] = have.get(os.path.realpath(path), '')
            if body.get('files'):
                out['files'] = [n for n in names if os.path.isfile(os.path.join(path, n))][:600]
            return out
        if name == 'add':                     # 폴더를 소스로 추가. dry_run 이면 무엇을 알아보는지만 돌려준다
            target = os.path.abspath(os.path.expanduser(str(body.get('path') or '')))
            if not os.path.isdir(target):
                raise SystemExit(f'폴더가 아닙니다: {target}')
            same = next((x['name'] for x in c['sources'] if x.get('path') and os.path.realpath(x['path']) == os.path.realpath(target)), None)
            if same:
                raise SystemExit(f'이미 추가된 폴더입니다: {same}. 같은 서버는 한 번만 넣어야 지도에 하나로 나옵니다.')
            base = re.sub(r'[^a-z0-9._-]+', '-', str(body.get('name') or os.path.basename(target)).lower()).strip('-') or 'source'
            sname, k = base, 2
            while any(x['name'] == sname for x in c['sources']):
                sname, k = f'{base}-{k}', k + 1
            table = language_table(c.get('parser', 'auto'))
            systems = adopt_systems(c, sname, detect(target, sname))
            shown = [dict(id=y['id'], name=y['name'], sub=y.get('sub', ''), lang=y['lang'], roots=y['roots'][:4],
                          note='어댑터 없음: 분석에서 빠짐' if y['lang'] not in table else '문법 패키지 없음: 설치 전에는 건너뜀' if table[y['lang']].startswith('문법 없음') else '') for y in systems]
            if not body.get('dry_run'):
                if not systems:
                    raise SystemExit('이 폴더에서 알아볼 수 있는 시스템이 없습니다.')
                c['sources'].append(dict(name=sname, path=target))
                c['systems'] += [y for y in systems if y['lang'] in table]
                save_config(c)
            return dict(source=sname, path=target, systems=shown, added=not body.get('dry_run'))
        if name == 'remove':                  # 소스 빼기: 설정에서만 뺀다. 폴더와 그 안의 파일은 건드리지 않는다
            sname = str(body.get('source') or '')
            gone = {y['id'] for y in c['systems'] if y['source'] == sname}
            if not any(x['name'] == sname for x in c['sources']):
                raise SystemExit(f'없는 소스입니다: {sname}')
            c['sources'] = [x for x in c['sources'] if x['name'] != sname]
            c['systems'] = [y for y in c['systems'] if y['source'] != sname]
            for key in ('calls', 'never'):
                c['link'][key] = {k: [t for t in v if t not in gone] for k, v in c['link'].get(key, {}).items() if k not in gone}
            save_config(c)
            return dict(removed=sorted(gone))
        if name == 'system':                  # 시스템 이름·주소·경로 접두어 고치기
            y = next((y for y in c['systems'] if y['id'] == body.get('id')), None)
            if not y:
                raise SystemExit('없는 시스템입니다.')
            if str(body.get('name') or '').strip():
                y['name'] = str(body['name']).strip()[:80]
            for key in ('hosts', 'prefix'):
                if key in body:
                    y[key] = words(body[key])
                    if not y[key]:
                        del y[key]
            save_config(c)
            return dict(ok=True)
        if name == 'external':                # 소스 없는 시스템(레거시) 등록·수정·삭제
            ext = c.setdefault('external', [])
            if body.get('remove'):
                c['external'] = [x for x in ext if x['id'] != body['remove']]
                for key in ('calls', 'never'):
                    c['link'][key] = {k: [t for t in v if t != body['remove']] for k, v in c['link'].get(key, {}).items()}
                save_config(c)
                return dict(ok=True)
            label = str(body.get('name') or '').strip()[:80]
            hosts, paths, clients = words(body.get('hosts')), words(body.get('paths')), str(body.get('clients') or '').strip()[:200]
            if not label:
                raise SystemExit('이름을 적어 주세요.')
            if not (hosts or paths or clients):
                raise SystemExit('주소, 경로 꼴, 클라이언트 이름 가운데 하나는 적어야 어떤 호출이 이 시스템으로 가는지 알 수 있습니다.')
            if clients:
                try:
                    re.compile(clients)
                except re.error as e:
                    raise SystemExit(f'클라이언트 이름 꼴이 정규식으로 읽히지 않습니다: {e}')
            x = next((x for x in ext if x['id'] == body.get('id')), None)
            if not x:
                base = re.sub(r'[^a-z0-9]+', '-', label.lower()).strip('-') or 'legacy'
                xid, k, taken = base, 2, {y['id'] for y in c['systems']} | {x['id'] for x in ext} | {'device', 'cloud'}
                while xid in taken:
                    xid, k = f'{base}-{k}', k + 1
                x = dict(id=xid)
                ext.append(x)
            x.update(name=label, hosts=hosts, paths=paths, clients=clients)
            for key in ('hosts', 'paths', 'clients'):
                if not x[key]:
                    del x[key]
            save_config(c)
            return dict(id=x['id'])
        if name == 'link':                    # 애매한 호출을 어느 서버로 볼지(calls), 잇지 않을 서버(never)
            a, b, mode, ids = str(body.get('from') or ''), str(body.get('to') or ''), body.get('mode'), {y['id'] for y in c['systems']} | {x['id'] for x in c.get('external', [])}
            if a not in ids or (mode != 'clear' and b not in ids):
                raise SystemExit('없는 시스템입니다.')
            for key in ('calls', 'never'):
                cur = [t for t in c['link'].setdefault(key, {}).get(a, []) if t != b and mode != 'clear']
                if key == mode:
                    cur.insert(0, b)
                if cur:
                    c['link'][key][a] = cur
                else:
                    c['link'][key].pop(a, None)
            save_config(c)
            return dict(ok=True)
        if name == 'rule':                    # 읽지 못한 꼴을 규칙으로 만들거나, 만든 규칙을 지운다 (작업 공간의 rules/custom.json)
            pack = load_custom(c)
            if body.get('remove'):
                pack['rules'] = [r for r in pack['rules'] if r.get('id') != body['remove']]
                save_custom(c, pack)
                return dict(ok=True)
            sid, kind, pname, role = str(body.get('sys') or ''), body.get('kind'), str(body.get('name') or '')[:120], body.get('role')
            if sid not in {y['id'] for y in c['systems']} or kind not in ('ann', 'call') or role not in ('provide', 'consume') or not re.match(r'[A-Za-z_][\w.]*$', pname):
                raise SystemExit('만들 수 없는 규칙입니다.')
            rule = custom_rule(sid, kind, pname, int(body.get('argi') or 0), role)
            pack['rules'] = [r for r in pack['rules'] if r.get('made') != rule['made']] + [rule]       # 같은 꼴에는 규칙 하나: 다시 고르면 바꾼다
            save_custom(c, pack)
            return dict(id=rule['id'])
        if name == 'layout':                  # 화면에서 옮긴 시스템 자리: 작업 공간 메모에 적어 같이 쓰는 사람에게도 같은 배치로 보이게 한다
            npath = os.path.join(live, 'atlas-notes.json')
            notes = json.load(open(npath, encoding='utf-8')) if os.path.exists(npath) else {}
            pos = {str(k)[:120]: [float(v[0]), float(v[1])] for k, v in (body.get('positions') or {}).items() if isinstance(v, list) and len(v) == 2}
            if pos:
                notes['positions'] = pos
            else:
                notes.pop('positions', None)
            write_atomic(npath, json.dumps(notes, ensure_ascii=False, indent=1))
            return dict(saved=len(pos))
        if name == 'sync':
            return run_job('sync', ['sync'] + ([] if any(x.get('git') for x in c['sources']) else ['--no-fetch']))
        if name == 'ingest':
            path = os.path.abspath(os.path.expanduser(str(body.get('path') or '')))
            if not os.path.isfile(path):
                raise SystemExit(f'파일이 아닙니다: {path}')
            return run_job('ingest', ['ingest', path])
        raise SystemExit('모르는 요청입니다.')

    class Handler(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *a, **k):
            super().__init__(*a, directory=site, **k)

        def translate_path(self, path):
            name = path.split('?')[0].split('#')[0].lstrip('/')
            if name in ('atlas-data.json', 'atlas-manifest.json', 'atlas-notes.json', 'atlas-runtime.json'):
                return os.path.join(live, name)
            return super().translate_path(path)

        def end_headers(self):
            self.send_header('Cache-Control', 'no-store')
            super().end_headers()

        def log_message(self, *a):
            pass

        def reply(self, code, obj):
            body = json.dumps(obj, ensure_ascii=False).encode()
            self.send_response(code)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def local(self):
            return (self.headers.get('Host') or '').rsplit(':', 1)[0] in ('127.0.0.1', 'localhost')

        def do_GET(self):
            path = self.path.split('?')[0]
            if not path.startswith('/api/'):
                return super().do_GET()
            if not self.local():
                return self.reply(403, dict(error='이 컴퓨터에서 연 화면만 쓸 수 있습니다.'))
            if path == '/api/state':
                return self.reply(200, state())
            return self.reply(404, dict(error='모르는 요청입니다.'))

        def do_POST(self):
            path = self.path.split('?')[0]
            if not path.startswith('/api/') or not self.local() or not secrets.compare_digest(self.headers.get('X-Atlas-Token') or '', token):
                return self.reply(403, dict(error='허용되지 않은 요청입니다.'))
            try:
                n = int(self.headers.get('Content-Length') or 0)
                body = json.loads(self.rfile.read(n) or b'{}') if 0 < n <= 2_000_000 else {}
                self.reply(200, api(path[5:], body if isinstance(body, dict) else {}))
            except SystemExit as e:
                self.reply(400, dict(error=str(e)))
            except Exception as e:
                self.reply(400, dict(error=f'{type(e).__name__}: {e}'))

    httpd = http.server.ThreadingHTTPServer(('127.0.0.1', port), Handler)
    return httpd, f'http://127.0.0.1:{httpd.server_address[1]}/'


def cmd_open(cfg, args):
    if not os.path.exists(os.path.join(cfg['_dir'], cfg.get('out', '.'), 'atlas-data.json')):
        if cfg['systems']:
            raise SystemExit('아직 분석 결과가 없습니다. 먼저 sync 를 실행하세요.')
        args.no_fetch, args.pull, args.force = True, False, False       # 빈 작업 공간: 빈 지도를 만들어 열고, 폴더 추가는 화면에서 한다
        cmd_sync(cfg, args)
    args.viewer = args.out = None
    cmd_site(cfg, args)
    httpd, url = make_server(cfg, args.port)
    print(f'{url} 에서 보여 주는 중입니다(이 컴퓨터에서만 열립니다). 끝내려면 Ctrl+C.')
    if not args.no_browser:
        import webbrowser
        webbrowser.open(url)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print('\n끝냈습니다.')
    finally:
        httpd.server_close()


def main():
    use_project_venv()
    ap = argparse.ArgumentParser(description='코드 아틀라스')
    ap.add_argument('--config', help='설정 파일 경로 (기본: 현재 폴더의 atlas.config.json)')
    ap.add_argument('-w', '--workspace', metavar='이름', help='작업 공간 이름. 이 저장소의 workspaces/<이름>/atlas.config.json 을 쓴다')
    sub = ap.add_subparsers(dest='cmd', required=True)
    p = sub.add_parser('init'); p.add_argument('name', nargs='?')
    p = sub.add_parser('add'); p.add_argument('target'); p.add_argument('--name'); p.add_argument('--branch'); p.add_argument('--dry-run', action='store_true')
    p = sub.add_parser('sync'); p.add_argument('--no-fetch', action='store_true'); p.add_argument('--pull', action='store_true'); p.add_argument('--force', action='store_true')
    sub.add_parser('status')
    sub.add_parser('diagnose')
    p = sub.add_parser('site'); p.add_argument('viewer', nargs='?'); p.add_argument('out', nargs='?')
    p = sub.add_parser('open'); p.add_argument('--port', type=int, default=0); p.add_argument('--no-browser', action='store_true')
    p = sub.add_parser('ingest'); p.add_argument('logs', nargs='*'); p.add_argument('--follow', action='store_true'); p.add_argument('--window', type=int, default=300)
    p.add_argument('--every', type=float, default=3); p.add_argument('--last', type=int); p.add_argument('--abnormal', type=int, default=400); p.add_argument('--regex')
    p.add_argument('--strip-prefix', action='append'); p.add_argument('--label'); p.add_argument('--clear', action='store_true')
    args = ap.parse_args()
    if args.workspace and not args.config:
        args.config = os.path.join(os.path.dirname(HERE), 'workspaces', args.workspace, 'atlas.config.json')
    args.config = args.config or 'atlas.config.json'
    if args.cmd == 'init':
        args.name = args.name or args.workspace
        if not args.name:
            raise SystemExit('이름을 주세요: -w <이름> init 또는 init <이름>')
        os.makedirs(os.path.dirname(os.path.abspath(args.config)), exist_ok=True)
        if os.path.exists(args.config):
            raise SystemExit(f'{args.config} 가 이미 있습니다.')
        json.dump(dict(name=args.name, out='.', store='atlas-store', api_hosts=[], sources=[], systems=[], link={}), open(args.config, 'w', encoding='utf-8'), ensure_ascii=False, indent=2)
        print(f'{args.config} 를 만들었습니다. add 로 소스를 추가하세요.')
        return
    if not os.path.exists(args.config):
        raise SystemExit(f'{args.config} 가 없습니다. 먼저 init 을 실행하세요 (예: python3 atlas/atlas.py -w <이름> init).')
    cfg = load_config(args.config)
    {'add': cmd_add, 'sync': cmd_sync, 'status': cmd_status, 'diagnose': cmd_diagnose, 'site': cmd_site, 'open': cmd_open, 'ingest': cmd_ingest}[args.cmd](cfg, args)


if __name__ == '__main__':
    main()
