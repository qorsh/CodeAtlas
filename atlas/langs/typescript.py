"""언어 어댑터 · TypeScript / JavaScript. class 와 최상위 함수(파일 유닛), import 로 이름을 푼다."""
# ───────────────────────────── TypeScript / JavaScript ─────────────────────────────
TS_KEYWORDS = {'if', 'for', 'while', 'switch', 'catch', 'function', 'return', 'else', 'do', 'try', 'new', 'await', 'throw', 'typeof', 'super', 'with'}
DECOR = re.compile(r'^\s*@[\w.]+\s*(\((?:[^()]|\((?:[^()]|\([^()]*\))*\))*\))?')
TS_METHOD = re.compile(r'^\s*(?:(?:public|private|protected|static|async|readonly|override|abstract|get|set)\s+)*\*?\s*([A-Za-z_$][\w$]*)\s*(?:<[^>]*>)?\s*\(')
TS_ARROW_PROP = re.compile(r'^\s*(?:(?:public|private|protected|static|readonly|override)\s+)*([A-Za-z_$][\w$]*)\s*(?::[^=]+)?=\s*(?:async\s*)?(?:\([^()]*(?:\([^()]*\)[^()]*)*\)|[A-Za-z_$][\w$]*)\s*(?::\s*[^=>{]+)?=>\s*$', re.S)
TS_FUNC = re.compile(r'\bfunction\s*\*?\s*([A-Za-z_$][\w$]*)\s*(?:<[^>]*>)?\s*\(')
TS_ARROW = re.compile(r'\b(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*(?::[^=]+)?=\s*(?:async\s*)?(?:\([^()]*(?:\([^()]*\)[^()]*)*\)|[A-Za-z_$][\w$]*)\s*(?::\s*[^=>{]+)?=>\s*$', re.S)


def strip_decorators(h):
    while True:
        m = DECOR.match(h)
        if not m:
            return h
        h = h[m.end():]


def classify_ts(header, parent_kind):
    m = None
    for m in re.finditer(r'\bclass\s+([A-Za-z_$][\w$]*)', header):
        pass
    if m and '(' not in header[m.end():].split('{')[0].replace('implements', '').replace('extends', '')[:0]:
        return 'type', m.group(1), 'class', m.start()
    h = strip_decorators(header)
    off = len(header) - len(h)
    if parent_kind == 'type':
        mm = TS_METHOD.match(h)
        if mm and mm.group(1) not in TS_KEYWORDS:
            p = h.find('(', mm.end() - 1)
            q = close_paren(h, p)
            tail = h[q + 1:]
            if re.fullmatch(r'\s*(?::\s*[^=]+)?\s*', tail, re.S):
                return 'func', mm.group(1), 'method', off + mm.start(1)
        mm = TS_ARROW_PROP.match(h)
        if mm:
            return 'func', mm.group(1), 'method', off + mm.start(1)
        return 'other', None, None, None
    mm = None
    for mm in TS_FUNC.finditer(header):
        pass
    if mm:
        q = close_paren(header, mm.end() - 1)
        if re.fullmatch(r'\s*(?::\s*[^=]+)?\s*', header[q + 1:], re.S):
            return 'func', mm.group(1), 'function', mm.start()
    mm = TS_ARROW.search(header)
    if mm:
        return 'func', mm.group(1), 'function', mm.start()
    mm = re.search(r'\b(interface|enum)\s+([A-Za-z_$][\w$]*)[^=]*$', header)
    if mm:
        return 'typedecl', mm.group(2), mm.group(1), mm.start()
    mm = re.search(r'\btype\s+([A-Za-z_$][\w$]*)\s*(?:<[^>]*>)?\s*=[^;]*$', header)
    if mm:
        return 'typedecl', mm.group(1), 'type', mm.start()
    mm = re.search(r'\b(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*(?::[^=]+)?=\s*(?:\w+\s*\(\s*)?$', header)
    if mm:
        return 'const', mm.group(1), 'const', mm.start()
    return 'other', None, None, None


def ts_kind(name, rel, has_routes=False):
    if rel.endswith('.tsx') or 'page' in os.path.basename(rel) and '/app/' in '/' + rel:
        return 'view'
    if re.search(r'(Dto|Error|Failure|Exception|Limited|Busy)$', name):
        return 'model'
    for pat, kind in ((r'Controller$', 'controller'), (r'(Guard|Middleware|Filter)$', 'guard'), (r'Module$', 'module'),
                      (r'(Store|Cache)$', 'store'), (r'Service$', 'service')):
        if re.search(pat, name):
            return kind
    low = (name + ' ' + os.path.basename(rel)).lower() if not re.match(r'[A-Z]', name) else name.lower()
    if 'controller' in low or '/route.ts' in rel:
        return 'controller'
    if 'guard' in low or 'middleware' in low or 'filter' in low:
        return 'guard'
    if '.module' in low or name.endswith('Module'):
        return 'module'
    if 'dto' in low or '.types' in low or 'types' in low.split('.')[-2:-1]:
        return 'model'
    if re.search(r'(store|cache|prisma|schema|db)', low):
        return 'store'
    if 'service' in low:
        return 'service'
    if re.search(r'(config|env|tokens)', low):
        return 'config'
    if '.pages' in low or '.renderer' in low:
        return 'view'
    return 'logic'


def parse_ts(sys_id, rel, module, lang='ts'):
    fi = load_file(rel, lang)
    src, code, blocks, lines = fi['src'], fi['code'], fi['blocks'], fi['lines']
    stem = re.sub(r'\.(ts|tsx|js)$', '', os.path.basename(rel))
    if stem in ('page', 'route', 'layout', 'index', 'schema'):
        stem = os.path.basename(os.path.dirname(rel)) + '/' + stem
    for i, b in enumerate(blocks):
        pk = blocks[b['parent']]['kind'] if b['parent'] >= 0 else None
        b['kind'], b['name'], b['sub'], b['decl'] = classify_ts(code[b['hstart']:b['open']], pk) if not b['paren'] else ('other', None, None, None)
    classes = [(i, b) for i, b in enumerate(blocks) if b['kind'] == 'type' and b['parent'] < 0]
    block_unit = {}
    covered = []
    exports = {}
    for bi, b in classes:
        hs = b['hstart']
        while hs < b['open'] and code[hs] in ' \t\n':
            hs += 1
        ui = add_unit(sys_id, b['name'], ts_kind(b['name'], rel), rel, [(hs, b['close'])], module, decl='class')
        u = units[ui]
        u['doc'] = doc_before(src, fi['comments'], hs, lines)
        head = src[hs:b['open']]
        u['attrs'] = re.findall(r'@(\w+)', code[hs:b['open']])
        m = re.search(r'\b(?:extends|implements)\s+([^{]+)$', code[hs:b['open']])
        if m:
            u['conforms'] = [x.strip() for x in re.split(r',|\bimplements\b|\bextends\b', m.group(1)) if x.strip()][:4]
        block_unit[bi] = ui
        covered.append((hs, b['close']))
        exports[b['name']] = ui
        prev_end = b['open'] + 1
        for mi, mb in enumerate(blocks):
            if mb['parent'] != bi:
                continue
            if mb['kind'] != 'func':
                if not mb['paren']:
                    prev_end = mb['close'] + 1
                continue
            a = mb['hstart']
            while a < mb['open'] and code[a] in ' \t\n':
                a += 1
            pos = mb['hstart'] + mb['decl']
            mem = dict(n=mb['name'], s=squeeze(src[lines.start_of(pos):mb['open']]), a=a, b=mb['close'], k='func',
                       d=doc_before(src, fi['comments'], a, lines))
            u['members'].append(mem)
            prev_end = mb['close'] + 1
    # 클래스 밖: 함수 / 상수 / 타입 → 파일 유닛
    top_funcs = [b for b in blocks if b['kind'] == 'func' and b['parent'] < 0 or (b['kind'] == 'func' and lang == 'js' and not func_ancestor(blocks, blocks.index(b)) and type_parent(blocks, blocks.index(b)) is None)]
    top_decl = [b for b in blocks if b['kind'] in ('typedecl', 'const') and b['parent'] < 0]
    bodiless = [m.group(1) for m in re.finditer(r'^export\s+(?:const|let|type|function|enum|interface)\s+([A-Za-z_$][\w$]*)', code, re.M)]
    need_file_unit = top_funcs or (not classes and (top_decl or bodiless or lang == 'js' or rel.endswith('.tsx')))
    if not classes and not need_file_unit and code.strip():
        need_file_unit = True
    file_unit = None
    func_loc = sum(lines.line(b['close']) - lines.line(b['hstart'] + b['decl']) + 1 for b in top_funcs)
    main = max(classes, key=lambda p: p[1]['close'] - p[1]['open'])[0] if classes else None
    main_loc = lines.line(blocks[main]['close']) - lines.line(blocks[main]['open']) + 1 if classes else 0
    imports_end = max([m.end() for m in IMPORT_RE.finditer(src)] + [m.end() for m in re.finditer(r'^import\s+[\'"][^\'"]+[\'"];?', src, re.M)] + [0])
    if need_file_unit:
        if classes and (not top_funcs or main_loc >= func_loc):
            file_unit = block_unit[main]
            for b in top_funcs:
                units[file_unit]['ranges'].append((lines.start_of(b['hstart'] + b['decl']), b['close']))
        else:
            nm = stem + ('.js' if lang == 'js' else '')
            file_unit = add_unit(sys_id, nm, 'page' if lang == 'js' else ts_kind(nm, rel), rel, [], module, decl='file')
            u = units[file_unit]
            # 클래스 밖 영역 전체를 범위로 잡는다 (import 문은 뺀다)
            cur, rng = imports_end, []
            for a, b_ in sorted(covered):
                if a > cur:
                    rng.append((cur, a))
                cur = max(cur, b_ + 1)
            if cur < len(code):
                rng.append((cur, len(code)))
            u['ranges'] = rng
            first = re.search(r'^(?!import\b)(export|const|function|async|type|interface|enum|let|var|class|@)', code, re.M)
            u['doc'] = doc_before(src, fi['comments'], first.start() if first else 0, lines)
        for b in top_funcs:
            pos = b['hstart'] + b['decl']
            a = lines.start_of(pos)
            units[file_unit]['members'].append(dict(n=b['name'], s=squeeze(src[a:b['open']]), a=a, b=b['close'], k='func',
                                                    d=doc_before(src, fi['comments'], a, lines)))
            exports[b['name']] = file_unit
        for b in top_decl:
            exports[b['name']] = file_unit
            if b['name'] not in units[file_unit]['exports']:
                units[file_unit]['exports'].append(b['name'])
        for nm in bodiless:
            exports.setdefault(nm, file_unit)
            if nm not in units[file_unit]['exports'] and exports[nm] == file_unit and nm not in [m['n'] for m in units[file_unit]['members']]:
                units[file_unit]['exports'].append(nm)
    elif classes:
        main = block_unit[classes[0][0]]
        for b in top_decl:
            exports[b['name']] = main
            units[main]['exports'].append(b['name'])
        for nm in bodiless:
            exports.setdefault(nm, main)
    fi['exports'] = exports
    fi['default'] = file_unit if file_unit is not None else (block_unit[classes[0][0]] if classes else None)
    # Next.js route handler
    if rel.endswith('/route.ts') and file_unit is not None:
        seg = os.path.dirname(rel).split('/app/', 1)[-1]
        for mi, mem in enumerate(units[file_unit]['members']):
            if mem['n'] in ('GET', 'POST', 'PUT', 'PATCH', 'DELETE'):
                mem['route'] = f"{mem['n']} /{seg}"
                routes.append(dict(method=mem['n'], path='/' + seg, unit=file_unit, member=mi, rule='nextjs-route-file'))


def resolve_import(rel, spec, alias_root=None):
    if spec.startswith('.'):
        base = os.path.normpath(os.path.join(os.path.dirname(rel), spec))
    elif spec.startswith('@/') and alias_root:
        base = os.path.join(alias_root, spec[2:])
    else:
        return None
    for cand in (base + '.ts', base + '.tsx', base + '/index.ts', base + '/index.tsx', base):
        if cand in file_info:
            return cand
    return None


IMPORT_RE = re.compile(r'import\s+(?:type\s+)?(?:([A-Za-z_$][\w$]*)\s*,?\s*)?(?:\{([^}]*)\}|\*\s+as\s+([A-Za-z_$][\w$]*))?\s*from\s*[\'"]([^\'"]+)[\'"]', re.S)


def ts_imports(rel, alias_root=None):
    """파일의 import 를 풀어 {로컬 이름: 유닛 idx} 를 돌려준다."""
    fi = file_info[rel]
    names = {}
    for m in IMPORT_RE.finditer(fi['src']):
        target = resolve_import(rel, m.group(4), alias_root)
        if not target:
            continue
        tf = file_info[target]
        if m.group(1) and tf.get('default') is not None:
            names[m.group(1)] = tf['default']
        if m.group(3) and tf.get('default') is not None:
            names[m.group(3)] = tf['default']
        for part in (m.group(2) or '').split(','):
            part = part.strip()
            if not part:
                continue
            part = re.sub(r'^type\s+', '', part)
            orig, _, alias = part.partition(' as ')
            orig, alias = orig.strip(), (alias.strip() or orig.strip())
            ui = tf['exports'].get(orig, tf.get('default'))
            if ui is not None:
                names[alias] = ui
    return names




register_language('ts', exts=['.ts', '.tsx'], imports=ts_imports,
                  parse=lambda sid, rel, mod: parse_ts(sid, rel, mod, 'js' if rel.endswith(('.js', '.jsx', '.mjs')) else 'ts'))
register_language('js', exts=['.js', '.jsx', '.mjs'], imports=ts_imports, syntax='js',
                  parse=lambda sid, rel, mod: parse_ts(sid, rel, mod, 'js'))
