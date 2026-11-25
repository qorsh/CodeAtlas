#!/usr/bin/env python3
"""반출 검사. 저장소에 올라갈 파일에 분석 대상 코드의 내용이 섞였는지 본다. 커밋·푸시 전에 돌린다.

  python3 tests/leak_check.py            # workspaces/*/ 의 분석 결과를 기준으로, git 이 추적하거나 추적하게 될 파일을 검사
  python3 tests/leak_check.py <atlas-data.json 또는 작업 공간 폴더> ...

기준이 되는 것: 작업 공간 설정의 제품 이름·소스 이름·API 호스트, 분석 결과의 유닛·모듈·파일 이름과 API 경로(이상 '확실'),
메서드 이름과 코드 속 문자열(이상 '참고'). 한 낱말짜리 흔한 이름(Request, index 등)은 우연히 겹치므로 보지 않는다.
'확실'이 하나라도 걸리면 1 로 끝난다. '참고'는 사람이 읽고 판단한다.
"""
import collections
import glob
import json
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
COMMON_FILES = {'index', 'main', 'route', 'page', 'layout', 'schema', 'app', 'settings', 'config', 'utils', 'types', 'models', 'routes', 'service', 'api', 'db', 'consumer'}


def compound(name):
    """우연히 겹치기 어려운 이름인가: 낱말이 둘 이상 붙었거나(camelCase, 구분자), 한글 등 영문이 아닌 글자가 들어 있다."""
    if len(name) < 8:
        return False
    return bool(re.search(r'[a-z0-9][A-Z]|[A-Z]{2,}[a-z]|[_\-/. ]|[^\x00-\x7f]', name))


def collect(workspace):
    strong, weak = {}, {}
    data = workspace if workspace.endswith('.json') else os.path.join(workspace, 'atlas-data.json')
    cfg_path = os.path.join(os.path.dirname(data), 'atlas.config.json')
    if os.path.exists(cfg_path):
        cfg = json.load(open(cfg_path, encoding='utf-8'))
        srcs = cfg.get('sources', [])
        if srcs and all(s.get('path') and os.path.abspath(os.path.expanduser(s['path'])).startswith(ROOT + os.sep) for s in srcs):
            return None, None                              # 이 저장소 안의 시험용 소스(fixtures)를 분석한 작업 공간은 기준이 아니다
        for v in [cfg.get('name')] + [s.get('name') for s in cfg.get('sources', [])]:
            if v and len(v) >= 4:
                strong[v] = '제품·소스 이름'
        for h in cfg.get('api_hosts', []):
            strong[h] = 'API 호스트'
            strong['.'.join(h.split('.')[-2:])] = 'API 호스트'
    if not os.path.exists(data):
        return strong, weak
    for r in json.load(open(data, encoding='utf-8')):
        if r.get('rt') == 'unit' and r.get('file'):
            stem = os.path.splitext(os.path.basename(r['file']))[0]
            for v, kind in ((r['name'], '유닛'), (r.get('mod'), '모듈'), (os.path.basename(r['file']), '파일')):
                if v and compound(v) and not (kind == '파일' and stem.lower() in COMMON_FILES):
                    strong.setdefault(v, kind)
            for v in r.get('nested') or []:
                if compound(v):
                    strong.setdefault(v, '중첩 타입')
            for m in r.get('m', []):
                if compound(m['n']) and len(m['n']) >= 12:
                    weak.setdefault(m['n'], '메서드')
                if m.get('route'):
                    strong.setdefault(str(m['route']).split(' ')[-1], 'API 경로')
                for v in m.get('lit') or []:
                    if len(v) >= 12:
                        weak.setdefault(v, '문자열')
            for v in r.get('lit') or []:
                if len(v) >= 12:
                    weak.setdefault(v, '문자열')
    for k in list(weak):
        if k in strong:
            del weak[k]
    for k in [k for k, kind in strong.items() if kind == 'API 경로' and k.count('/') < 2]:
        del strong[k]                                     # /health 같은 한 토막 경로는 어디에나 있다
    return strong, weak


def tracked_files():
    out = subprocess.run(['git', '-C', ROOT, 'ls-files', '--cached', '--others', '--exclude-standard'], capture_output=True, text=True).stdout
    return [f for f in out.splitlines() if f and os.path.isfile(os.path.join(ROOT, f))]


def main():
    spaces = sys.argv[1:] or sorted(glob.glob(os.path.join(ROOT, 'workspaces', '*')))
    strong, weak = {}, {}
    used = []
    for w in spaces:
        s, k = collect(w)
        if s is None:
            continue
        used.append(os.path.basename(w.rstrip('/')))
        strong.update(s)
        weak.update(k)
    if not strong and not weak:
        print('기준으로 삼을 작업 공간이 없습니다 (workspaces/*/atlas-data.json).')
        return 0
    files, texts = tracked_files(), {}
    for f in files:
        try:
            texts[f] = open(os.path.join(ROOT, f), encoding='utf-8').read()
        except (UnicodeDecodeError, OSError):
            texts[f] = None                                # 이미지 같은 바이너리는 내용을 볼 수 없으니 이름만 알린다
    binaries = [f for f, t in texts.items() if t is None]
    hits = {True: collections.defaultdict(list), False: collections.defaultdict(list)}
    for is_strong, names in ((True, strong), (False, weak)):
        for n, kind in names.items():
            pat = re.compile(r'(?<![A-Za-z0-9_])' + re.escape(n) + r'(?![A-Za-z0-9_])')
            for f, t in texts.items():
                if t and pat.search(t):
                    hits[is_strong][f].append(f'{n} [{kind}]')
    print(f'기준이 된 작업 공간 {len(used)}곳 · 이름 {len(strong)}개(확실) + {len(weak)}개(참고) · 검사한 파일 {len(files)}개')
    for is_strong, title in ((True, '확실: 분석 대상의 이름이 들어 있다'), (False, '참고: 겹치는 메서드 이름·문자열 (우연일 수 있다)')):
        for f in sorted(hits[is_strong]):
            print(f'  {title}\n    {f}: ' + ', '.join(hits[is_strong][f][:12]) + (f' 외 {len(hits[is_strong][f]) - 12}' if len(hits[is_strong][f]) > 12 else ''))
    if binaries:
        print('  내용을 볼 수 없는 파일 (화면 캡처라면 분석 대상이 찍혔는지 직접 확인): ' + ', '.join(binaries))
    if hits[True]:
        print('실패: 위 파일에서 분석 대상의 내용을 빼거나 작업 공간(workspaces/)으로 옮기세요.')
        return 1
    print('통과: 분석 대상의 이름이 들어 있는 파일이 없습니다.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
