#!/usr/bin/env python3
"""파서 비교. 같은 소스를 내장 어댑터와 tree-sitter 앞단으로 각각 분석해 유닛·멤버가 어디서 달라지는지 보여 준다.

  .venv/bin/python tests/parser_parity.py <언어> <소스 루트> [하위 폴더 ...]
  예) .venv/bin/python tests/parser_parity.py java fixtures/shop/order-service

tree-sitter 와 해당 문법 패키지가 깔린 파이썬으로 실행해야 한다. 차이가 없으면 0, 있으면 1 로 끝난다.
차이가 곧 오류는 아니다(정식 파서가 더 정확한 경우가 많다). 새 명세를 쓰거나 어댑터를 고칠 때 무엇이 달라지는지 보는 도구다.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'atlas'))
import atlas as A  # noqa: E402


def analyze(lang, root, roots, parser):
    systems = [dict(id='x', name='x', lang=lang, roots=roots, source='s')]
    opts = dict(ns='x', api_hosts=[], rules=A.load_rules(), strings_xml=[], skip_dirs=[], parser=parser)
    return A.run_group(os.path.abspath(root), systems, opts)


def table(res):
    units = {}
    for u in res['units']:
        units[(u['file'], u['name'])] = u
    return units


def main():
    if len(sys.argv) < 3:
        print(__doc__)
        return 2
    lang, root, roots = sys.argv[1], sys.argv[2], sys.argv[3:] or ['.']
    old, new = analyze(lang, root, roots, 'builtin'), analyze(lang, root, roots, 'tree-sitter')
    if set(new['stats']['parsers'].values()) != {'tree-sitter'}:
        print('tree-sitter 앞단이 쓰이지 않았습니다:', new['stats']['parsers'])
        return 2
    a, b = table(old), table(new)
    diffs = 0
    show = lambda *x: print('  ', *x)
    for k in sorted(set(a) - set(b)):
        diffs += 1
        show('내장에만 있는 유닛', k)
    for k in sorted(set(b) - set(a)):
        diffs += 1
        show('tree-sitter 에만 있는 유닛', k, b[k].get('decl'))
    n_members = 0
    for k in sorted(set(a) & set(b)):
        ua, ub = a[k], b[k]
        for f in ('kind', 'decl', 'line', 'loc', 'doc', 'conf', 'nested'):
            if ua.get(f) != ub.get(f):
                diffs += 1
                show(f'{k[0]}:{k[1]} · {f}: 내장 {str(ua.get(f))[:70]!r} / tree-sitter {str(ub.get(f))[:70]!r}')
        ma, mb = {(m['n'], m['l']): m for m in ua.get('m', [])}, {(m['n'], m['l']): m for m in ub.get('m', [])}
        n_members += len(mb)
        for mk in sorted(set(ma) - set(mb)):
            diffs += 1
            show(f'{k[0]}:{k[1]} · 내장에만 있는 멤버 {mk}')
        for mk in sorted(set(mb) - set(ma)):
            diffs += 1
            show(f'{k[0]}:{k[1]} · tree-sitter 에만 있는 멤버 {mk}')
        for mk in sorted(set(ma) & set(mb)):
            for f in ('s', 'e', 'd', 'route'):
                if ma[mk].get(f) != mb[mk].get(f):
                    diffs += 1
                    show(f'{k[0]}:{k[1]}.{mk[0]} · {f}: 내장 {str(ma[mk].get(f))[:60]!r} / tree-sitter {str(mb[mk].get(f))[:60]!r}')
    ea = sorted((old['units'][e['s']]['name'], old['units'][e['t']]['name'], e['k']) for e in old['edges'])
    eb = sorted((new['units'][e['s']]['name'], new['units'][e['t']]['name'], e['k']) for e in new['edges'])
    for kind, title in (('routes', 'API 제공'), ('http', 'API 호출'), ('db', 'DB 접근'), ('tables', '테이블'), ('other', '토픽')):
        if len(old['ports'][kind]) != len(new['ports'][kind]):
            diffs += 1
            show(f'접점 {title}: 내장 {len(old["ports"][kind])} / tree-sitter {len(new["ports"][kind])}')
    if ea != eb:
        diffs += 1
        show(f'유닛 간 참조: 내장 {len(ea)} / tree-sitter {len(eb)} (한쪽에만 {len(set(ea) ^ set(eb))})')
    print(f'{lang} · 파일 {new["stats"]["files"]} · 유닛 {len(b)} · 멤버 {n_members} · 차이 {diffs}건')
    return 1 if diffs else 0


if __name__ == '__main__':
    sys.exit(main())
