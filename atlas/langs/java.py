"""언어 어댑터 · Java. class/interface/enum/record 를 유닛으로, 메서드(본문 없는 인터페이스 메서드 포함)를 멤버로 만든다.

어댑터가 하는 일은 문법까지다. @GetMapping 같은 프레임워크 의미는 모르고, 그것은 rules/*.json 이 맡는다.
"""
JV_TYPE = re.compile(r'(?<![\w.])(class|interface|enum|record)\s+([A-Za-z_]\w*)')
JV_METHOD = re.compile(r'(?<![\w.])([A-Za-z_]\w*)\s*\(')
JV_NOT_METHOD = {'if', 'for', 'while', 'switch', 'catch', 'synchronized', 'try', 'do', 'else', 'return', 'new', 'throw', 'super', 'this', 'assert'}
JV_ANN = re.compile(r'@[\w.]+\s*(\((?:[^()]|\((?:[^()]|\([^()]*\))*\))*\))?')


def classify_java(header, parent_kind):
    h = JV_ANN.sub(lambda m: ' ' * len(m.group(0)), header)       # 어노테이션 인자 속 괄호·등호에 속지 않게 가린다
    t = None
    for t in JV_TYPE.finditer(h):
        pass
    if t and '=' not in h[t.end():]:
        return 'type', t.group(2), t.group(1), t.start()
    if parent_kind == 'type':
        m = JV_METHOD.search(h)
        if m and m.group(1) not in JV_NOT_METHOD and '=' not in h[:m.start()]:
            q = close_paren(h, m.end() - 1)
            if re.fullmatch(r'\s*(throws\s+[\w.,\s<>]+)?\s*', h[q + 1:]):
                return 'func', m.group(1), 'method', m.start()
    return 'other', None, None, None


def java_kind(name, decl, header):
    for pat, kind in ((r'(Controller|Resource|Endpoint|Handler)$', 'controller'), (r'(Filter|Interceptor|Guard)$', 'guard'),
                      (r'(Repository|Dao|DAO|Mapper|Store)$', 'store'), (r'(Client|Gateway|Api|API)$', 'client'), (r'Service(Impl)?$', 'service'),
                      (r'(Config|Configuration|Properties)$', 'config'), (r'(Dto|DTO|Request|Response|Entity|Event|Exception|Error)$', 'model')):
        if re.search(pat, name):
            return kind
    return 'model' if decl in ('enum', 'record') else 'logic'


def parse_java(sys_id, rel, module):
    fi = load_file(rel, 'java')
    src, code, blocks, lines = fi['src'], fi['code'], fi['blocks'], fi['lines']
    for b in blocks:
        pk = blocks[b['parent']]['kind'] if b['parent'] >= 0 else None
        b['kind'], b['name'], b['sub'], b['decl'] = classify_java(code[b['hstart']:b['open']], pk) if not b['paren'] else ('other', None, None, None)
    block_unit = {}

    def start_of(b):
        a = b['hstart']
        while a < b['open'] and code[a] in ' \t\r\n':
            a += 1
        return a

    for bi, b in enumerate(blocks):
        if b['kind'] != 'type' or b['parent'] >= 0:
            continue
        a = start_of(b)
        header = code[a:b['open']]
        ui = add_unit(sys_id, b['name'], java_kind(b['name'], b['sub'], header), rel, [(a, b['close'])], module, decl=b['sub'])
        units[ui]['doc'] = doc_before(src, fi['comments'], a, lines)
        m = re.search(r'\b(?:extends|implements)\s+(.+)$', header, re.S)
        if m:
            units[ui]['conforms'] = [re.sub(r'<.*', '', x).strip() for x in re.split(r',|\bimplements\b|\bextends\b', m.group(1)) if x.strip()][:5]
        block_unit[bi] = ui
        families[(NS[sys_id], b['name'])].append(ui)

    def owner_of(bi):
        """bi 블록을 감싼 타입 사슬을 따라 올라가 (최상위 타입 유닛, 중첩 타입 이름 접두어)를 돌려준다."""
        chain, k = [], blocks[bi]['parent']
        while k >= 0 and blocks[k]['kind'] == 'type':
            chain.append(k)
            k = blocks[k]['parent']
        if k >= 0 or not chain or chain[-1] not in block_unit:
            return None, ''
        return block_unit[chain[-1]], '.'.join(blocks[c]['name'] for c in reversed(chain[:-1]))

    for bi, b in enumerate(blocks):
        if b['kind'] == 'type' and b['parent'] >= 0:
            owner, _ = owner_of(bi)
            if owner is not None and b['name'] not in units[owner]['nested']:
                units[owner]['nested'].append(b['name'])
        if b['kind'] != 'func':
            continue
        owner, prefix = owner_of(bi)
        if owner is None:
            continue
        a = start_of(b)
        units[owner]['members'].append(dict(n=(prefix + '.' if prefix else '') + b['name'], s=squeeze(JV_ANN.sub('', src[a:b['open']])), a=a, b=b['close'], k='func',
                                            d=doc_before(src, fi['comments'], a, lines)))
    # 본문 없는 메서드(인터페이스·추상 메서드): 타입 본문 바로 아래에서 ; 로 끝나는 선언
    for bi, ui in block_unit.items():
        b = blocks[bi]
        i, seg, depth = b['open'] + 1, b['open'] + 1, 0
        while i < b['close']:
            c = code[i]
            if c in '({[':
                depth += 1
            elif c in ')}]':
                depth -= 1
                if c == '}' and depth == 0:
                    seg = i + 1
            elif c == ';' and depth == 0:
                stmt = code[seg:i]
                a = seg + (len(stmt) - len(stmt.lstrip()))
                kind, name, _, _ = classify_java(stmt, 'type')
                if kind == 'func' and '{' not in stmt:
                    units[ui]['members'].append(dict(n=name, s=squeeze(JV_ANN.sub('', src[a:i])), a=a, b=i, k='func', d=doc_before(src, fi['comments'], a, lines)))
                seg = i + 1
            i += 1


register_language('java', exts=['.java'], parse=parse_java, syntax='c')
