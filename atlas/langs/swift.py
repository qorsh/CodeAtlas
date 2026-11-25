"""언어 어댑터 · Swift. 타입(class/struct/enum/protocol/actor)과 extension, 그 안의 func·init·계산 프로퍼티를 유닛·멤버로 만든다."""
# ───────────────────────────── Swift ─────────────────────────────
SW_TYPE = re.compile(r'\b(class|struct|enum|protocol|actor|extension)\s+([A-Za-z_][\w.]*)')
SW_FUNC = re.compile(r'\bfunc\s+([A-Za-z_]\w*|`[^`]+`|[^\s(<]+)')
SW_INIT = re.compile(r'(?<![\w.])init[?!]?\s*(?:<[^>]*>)?\s*\(')
SW_VAR = re.compile(r'^\s*(?:@\w+(?:\([^)]*\))?\s+)*(?:(?:public|private|fileprivate|internal|static|override|final|nonisolated|lazy|mutating|private\(set\)|class)\s+)*var\s+([A-Za-z_]\w*)\s*:\s*([^={]+)$')
STMT_BREAK = re.compile(r'\n\s*(let|var|val|fun|func|if|guard|return|switch|for|while|case|class|struct|object|private|public|internal|override|static|@\w+|when|try|do)\b')


def classify_swift(header):
    cands = []
    for m in SW_TYPE.finditer(header):
        if m.group(2) not in ('func', 'var', 'let'):
            cands.append((m.start(), 'type', m.group(2), m.group(1), m.end()))
    for m in SW_FUNC.finditer(header):
        cands.append((m.start(), 'func', m.group(1), 'func', m.end()))
    for m in SW_INIT.finditer(header):
        cands.append((m.start(), 'func', 'init', 'init', m.end() - 1))
    last_nl = header.rstrip().rfind('\n')
    last = header[last_nl + 1:]
    mv = SW_VAR.match(last)
    if mv:
        cands.append((last_nl + 1 + mv.start(1), 'func', mv.group(1), 'var:' + mv.group(2).strip(), len(header)))
    if not cands:
        return 'other', None, None, None
    pos, kind, name, sub, end = max(cands)
    tail = header[end:]
    if kind == 'func' and not sub.startswith('var'):
        p = tail.find('(') if sub == 'func' else 0
        if p >= 0:
            q = close_paren(tail, p) if tail[p:p + 1] == '(' else p
            if STMT_BREAK.search(tail[q:]):
                return 'other', None, None, None
    if kind == 'type' and STMT_BREAK.search(tail):
        return 'other', None, None, None
    return kind, name, sub, pos


def swift_kind(u, header, body_code, rel):
    name, conf = u['name'], u['conforms']
    attrs = ' '.join(u['attrs'])
    if 'Reducer' in attrs or 'Reducer' in conf:
        return 'reducer'
    if any(c in conf for c in ('View', 'UIViewRepresentable', 'UIViewControllerRepresentable', 'ViewModifier', 'App', 'Scene', 'Widget', 'WidgetBundle', 'ButtonStyle', 'LabelStyle', 'Shape', 'NSViewRepresentable')) or re.search(r'(View|ViewController|Sheet|Screen)$', name) or 'UIViewController' in conf or 'UIView' in conf:
        return 'view'
    if u['decl'] == 'protocol':
        return 'protocol'
    if u['decl'] in ('enum', 'struct') and not u['members'] and u['ranges'] and u['ranges'][0][1] - u['ranges'][0][0] < 420:
        return 'model'
    if re.search(r'(Client|API\w*)$', name):
        return 'client'
    if re.search(r'(Store|Repository|Storage|Persistence|Library|Inbox|Mapper|Mapping|Factory)$', name) or '/Persistence/' in rel:
        return 'store'
    if re.search(r'(Service|Driver|Manager|Publisher|Policy|Resolver|Converter|Detector|Validator|Transfer|Provider|Bootstrap|Console|Availability|Coordinator|Reader|Classification|Import)$', name):
        return 'logic'
    if u['decl'] == 'extension':
        return 'ext'
    if '/Models/' in rel or u['decl'] == 'enum' or any(c in conf for c in ('Codable', 'Decodable', 'Encodable', 'Identifiable', 'Equatable', 'Hashable', 'Error')):
        return 'model'
    if '/Utilities/' in rel or '/Design/' in rel:
        return 'util'
    return 'logic'


def parse_swift(sys_id, rel, module):
    fi = load_file(rel, 'swift')
    src, code, blocks, lines = fi['src'], fi['code'], fi['blocks'], fi['lines']
    base_file = os.path.splitext(os.path.basename(rel))[0]
    for b in blocks:
        b['kind'], b['name'], b['sub'], b['decl'] = classify_swift(code[b['hstart']:b['open']])
    # 프로토콜 안쪽 블록은 멤버로 보지 않는다
    top_types = [(i, b) for i, b in enumerate(blocks) if b['parent'] < 0 and b['kind'] == 'type']
    local = {}   # name -> unit idx (이 파일에서 정의된 타입)
    ext_units = {}
    block_unit = {}
    for bi, b in top_types:
        if b['sub'] != 'extension':
            hs = b['hstart'] + b['decl']
            header = code[hs:b['open']]
            conf = []
            m = re.search(r':\s*(.+)$', header.split('where')[0], re.S)
            if m:
                conf = [re.sub(r'<.*', '', x).strip() for x in m.group(1).split(',') if x.strip()]
            pre = src[lines.start_of(max(b['hstart'], 0)):hs]
            attrs = re.findall(r'@(\w+)', code[b['hstart']:hs])
            ui = add_unit(sys_id, b['name'], 'tbd', rel, [(hs, b['close'])], module, conforms=conf, attrs=attrs, decl=b['sub'])
            units[ui]['doc'] = doc_before(src, fi['comments'], lines.start_of(hs) if not attrs else b['hstart'] + len(code[b['hstart']:hs]) - len(code[b['hstart']:hs].lstrip()), lines)
            local[b['name']] = ui
            block_unit[bi] = ui
            families[('apple', b['name'])].append(ui)
    for bi, b in top_types:
        if b['sub'] == 'extension':
            hs = b['hstart'] + b['decl']
            base = b['name']
            header = code[hs:b['open']]
            conf = []
            m = re.search(r':\s*(.+)$', header.split('where')[0], re.S)
            if m:
                conf = [re.sub(r'<.*', '', x).strip() for x in m.group(1).split(',') if x.strip()]
            if base in local:
                ui = local[base]
                units[ui]['ranges'].append((hs, b['close']))
                units[ui]['conforms'] += [c for c in conf if c not in units[ui]['conforms']]
            else:
                if base not in ext_units:
                    if base_file.startswith(base + '+'):
                        nm = base_file
                    elif '+' in base_file and not ext_units:
                        nm = base_file if base_file.split('+')[0] == base else f'{base}+{base_file.split("+")[-1]}'
                    else:
                        nm = f'{base}+{base_file}'
                    ui = add_unit(sys_id, nm, 'tbd', rel, [(hs, b['close'])], module, conforms=conf, attrs=[], decl='extension', base=base.split('.')[0])
                    units[ui]['doc'] = doc_before(src, fi['comments'], lines.start_of(hs), lines)
                    ext_units[base] = ui
                    families[('apple', base.split('.')[0])].append(ui)
                else:
                    ui = ext_units[base]
                    units[ui]['ranges'].append((hs, b['close']))
                    units[ui]['conforms'] += [c for c in conf if c not in units[ui]['conforms']]
            block_unit[bi] = ui
    # 최상위 함수 → 파일 유닛
    file_unit = None
    for bi, b in enumerate(blocks):
        if b['kind'] != 'func':
            continue
        tp = type_parent(blocks, bi)
        if tp is None:
            continue
        hs = b['hstart'] + b['decl']
        if tp == -1:
            if b['sub'].startswith('var'):
                continue
            if file_unit is None:
                file_unit = local.get(base_file)
                if file_unit is None:
                    file_unit = add_unit(sys_id, base_file, 'util', rel, [], module, decl='file')
            owner, prefix = file_unit, ''
            units[owner]['ranges'].append((lines.start_of(hs), b['close']))
        else:
            # 가장 바깥 타입 블록을 찾는다
            chain, k = [], tp
            while k >= 0 and blocks[k]['kind'] == 'type':
                chain.append(k)
                k = blocks[k]['parent']
            if k >= 0 or chain[-1] not in block_unit:
                continue
            if blocks[chain[-1]]['sub'] == 'protocol':
                continue
            owner = block_unit[chain[-1]]
            prefix = '.'.join(blocks[c]['name'] for c in reversed(chain[:-1]))
        sub = b['sub']
        nlines = lines.line(b['close']) - lines.line(hs) + 1
        if sub.startswith('var'):
            if b['name'] != 'body' and 'some ' not in sub and nlines < 8:
                continue
        name = (prefix + '.' if prefix else '') + b['name']
        sig = squeeze(src[lines.start_of(hs):b['open']])
        units[owner]['members'].append(dict(n=name, s=sig, a=lines.start_of(hs), b=b['close'], k='prop' if sub.startswith('var') else 'func',
                                            d=doc_before(src, fi['comments'], lines.start_of(hs), lines)))
    # 프로토콜 요구사항 / 중첩 타입 이름
    for bi, b in top_types:
        ui = block_unit[bi]
        body = code[b['open']:b['close']]
        if b['sub'] == 'protocol':
            for m in re.finditer(r'^\s*(?:static\s+|mutating\s+)*func\s+(\w+)[^\n]*', body, re.M):
                a = b['open'] + m.start()
                units[ui]['members'].append(dict(n=m.group(1), s=squeeze(src[a:b['open'] + m.end()]), a=a, b=b['open'] + m.end(), k='req', d=''))
        for bj, nb in enumerate(blocks):
            if nb['kind'] == 'type' and nb['parent'] == bi and nb['name'] not in units[ui]['nested']:
                units[ui]['nested'].append(nb['name'])
    for ui in fi['units']:
        u = units[ui]
        u['kind'] = swift_kind(u, '', '', '/' + rel) if u['kind'] == 'tbd' else u['kind']



register_language('swift', exts=['.swift'], parse=parse_swift, extensions=True)
