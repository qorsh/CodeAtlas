"""언어 어댑터 · tree-sitter 앞단. 정식 파서로 유닛·멤버의 범위를 낸다.

내장 어댑터(java.py, python.py …)는 주석·문자열을 지운 뒤 괄호·들여쓰기 구조로 근사 분석한다. 이 파일은 같은 약속
(load_file → add_unit → 멤버 범위)을 구문 트리로 채우는 또 하나의 앞단이고, 뒤 단계(이름 풀기, 사실, 규칙, 연결)는 그대로다.

  · 언어 하나에 필요한 것은 SITTER_SPECS 의 명세 한 덩어리다: 어떤 노드가 타입·함수·주석·문자열인지. 트리를 훑는 코드는 공용이다.
  · 문법은 PyPI 의 언어별 패키지(tree-sitter-java 등)에서 온다. 실행 중에 내려받는 것은 없다.
    패키지가 없으면 이 앞단은 조용히 빠지고 내장 어댑터가 쓰인다. 내장 어댑터가 없는 언어(Go …)는 문법 패키지가 있어야 분석된다.
  · 설정의 "parser": "builtin" 이면 항상 내장 어댑터를 쓰고, "tree-sitter" 면 문법이 없을 때 멈춘다. 기본은 "auto"(있으면 쓴다).

이 파일은 이름순으로 java.py · python.py 뒤에 읽히므로, 같은 언어 이름으로 다시 등록해 그 어댑터를 대신한다.
"""
try:
    import tree_sitter as _SITTER
except Exception:                      # 설치돼 있지 않으면 내장 어댑터만 쓴다
    _SITTER = None

SITTER_LAST = {}                       # strip 이 방금 파싱한 결과. 같은 파일의 parse 가 이어 쓴다(파일당 파싱 한 번)
SITTER_QUERIES = {}                    # (언어, 노드 종류들) → 질의
SITTER_USED = {}                       # 이 앞단으로 읽는 언어 (결과에 남긴다)
SITTER_MISSING = {}                    # 문법 패키지가 없어 분석할 수 없는 언어 → 설치할 패키지 이름


def sitter_conv(src, data):
    """바이트 위치 → 문자 위치. tree-sitter 는 UTF-8 바이트로, 엔진은 문자로 센다."""
    if len(data) == len(src):
        return lambda b: b
    table, pos = [0] * (len(data) + 1), 0
    for ci, ch in enumerate(src):
        o = ord(ch)
        n = 1 if o < 0x80 or 0xD800 <= o <= 0xDFFF else 2 if o < 0x800 else 3 if o < 0x10000 else 4
        for k in range(n):
            table[pos + k] = ci
        pos += n
    table[pos] = len(src)
    return table.__getitem__


def sitter_nodes(lang_obj, root, kinds):
    """root 아래에서 종류가 kinds 인 노드를 문서 순서로 돌려준다. 질의로 고르고(C 쪽에서 걸러 빠르다), 안 되면 직접 훑는다."""
    key = (id(lang_obj), kinds)
    if key not in SITTER_QUERIES:
        valid = []
        for k in sorted(kinds):
            try:
                _SITTER.Query(lang_obj, f'({k}) @n')
                valid.append(k)
            except Exception:           # 이 문법 판에는 없는 노드 이름
                pass
        try:
            SITTER_QUERIES[key] = _SITTER.Query(lang_obj, '[' + ' '.join(f'({k})' for k in valid) + '] @n') if valid else None
        except Exception:
            SITTER_QUERIES[key] = False
    q = SITTER_QUERIES[key]
    if q is None:
        return []
    out = None
    if q:
        try:
            out = [n for v in _SITTER.QueryCursor(q).captures(root).values() for n in v]
        except Exception:
            out = None
    if out is None:
        out, stack = [], [root]
        while stack:
            n = stack.pop()
            if n.type in kinds:
                out.append(n)
            stack.extend(n.children)
    out.sort(key=lambda n: (n.start_byte, -n.end_byte))
    return out


def sitter_strip_fn(spec, lang_obj, parser):
    comment_kinds, string_kinds = frozenset(spec['comments']), frozenset(spec['strings'])
    kinds = comment_kinds | string_kinds | frozenset(spec.get('chars', ()))

    def strip(src):
        """주석·문자열을 같은 길이의 공백으로 바꾼다. 구간은 구문 트리에서 얻으므로 따옴표 규칙을 언어마다 흉내 낼 필요가 없다."""
        data = src.encode('utf-8', 'replace')
        tree = parser.parse(data)
        cv = sitter_conv(src, data)
        out, comments, strings, last = list(src), [], [], -1
        for node in sitter_nodes(lang_obj, tree.root_node, kinds):
            a, b = cv(node.start_byte), cv(node.end_byte)
            if a < last:                 # 문자열 보간 안의 문자열처럼 이미 지운 구간 안
                continue
            if node.type in comment_kinds:
                comments.append((a, b))
            elif node.type in string_kinds:
                while a < b and src[a] not in '"\'`':      # r"…", f"…" 같은 접두 글자는 코드로 남긴다
                    a += 1
                strings.append((a, b))
            for k in range(a, b):
                if out[k] != '\n':
                    out[k] = ' '
            last = b
        SITTER_LAST['v'] = (src, tree, cv)
        return ''.join(out), comments, strings
    return strip


def sitter_parse_fn(lang, spec, lang_obj):
    types, funcs = spec['types'], frozenset(spec['funcs'])
    kinds = frozenset(types) | funcs
    walls = kinds | frozenset(spec.get('walls', ()))     # 이 노드 안쪽의 선언은 바깥 타입의 것이 아니다 (익명 클래스, 람다 …)
    body_field = spec.get('body_field', 'body')

    def parse(sys_id, rel, module):
        fi = load_file(rel, lang)
        src, code, lines = fi['src'], fi['code'], fi['lines']
        last = SITTER_LAST.pop('v')
        assert last[0] is src
        tree, cv = last[1], last[2]
        text = lambda node: src[cv(node.start_byte):cv(node.end_byte)]

        def name_of(node):
            n = node.child_by_field_name('name')
            return text(n) if n is not None else None

        def span(node):
            a, b = cv(node.start_byte), cv(node.end_byte) - 1
            if spec.get('line_start'):               # 들여쓰기 언어: 선언이 있는 줄의 처음부터 (데코레이터는 그 윗줄에서 찾는다)
                ls = lines.start_of(a)
                if not src[ls:a].strip():
                    a = ls
            while b > a and src[b] in ' \t\r\n':
                b -= 1
            return a, b

        def header_end(node, a, b):
            """선언 머리(이름·인자·상속)가 끝나는 곳. 본문이 없으면 선언 끝."""
            body = node.child_by_field_name(body_field)
            end = cv(body.start_byte) if body is not None else b + 1
            return a + len(code[a:end].rstrip())      # 머리 뒤에 붙은 주석·공백은 뺀다

        def signature(a, end):
            s = src[a:end]
            if spec.get('sig_clean'):
                s = spec['sig_clean'](s)
            return squeeze(s.rstrip().rstrip(';:{').rstrip())

        def docstring(node):
            """본문 첫 문장이 문자열이면 그것이 설명이다 (Python)."""
            if not spec.get('docstring'):
                return ''
            body = node.child_by_field_name(body_field) if node.type != tree.root_node.type else node
            for c in (body.named_children if body is not None else []):
                if c.type in spec['comments']:
                    continue
                if c.type == 'expression_statement' and c.named_child_count == 1 and c.named_children[0].type in spec['strings']:
                    return re.sub(r'\s+', ' ', text(c.named_children[0]).lstrip('rRbBuUfF').strip('"\' \n')).strip()[:300]
                break
            return ''

        def decl_ancestors(node):
            out, p = [], node.parent
            while p is not None:
                if p.type in walls:
                    out.append(p)
                p = p.parent
            return out                                # 가까운 것부터

        decls = sitter_nodes(lang_obj, tree.root_node, kinds)
        top, local, covered = {}, {}, []             # 노드 id → 유닛, 이름 → 유닛, 유닛이 차지한 구간
        # 1) 타입 선언 → 유닛. 타입 안의 타입은 바깥 유닛의 '안에 든 타입'으로만 남긴다.
        for node in decls:
            if node.type not in types:
                continue
            name, anc = name_of(node), decl_ancestors(node)
            if not name:
                continue
            if anc:
                if all(x.type in types for x in anc) and anc[-1].id in top and name not in units[top[anc[-1].id]]['nested']:
                    units[top[anc[-1].id]]['nested'].append(name)
                continue
            label = types[node.type]
            decl = label(node) if callable(label) else label
            a, b = span(spec['unit_span'](node) if spec.get('unit_span') else node)
            ui = add_unit(sys_id, name, spec['kind'](name, decl), rel, [(a, b)], module, decl=decl)
            units[ui]['doc'] = docstring(node) or doc_before(src, fi['comments'], a, lines)
            if spec.get('supers'):
                units[ui]['conforms'] = spec['supers'](code[a:header_end(node, a, b)], node, text)[:5]
            top[node.id], local[name] = ui, ui
            covered.append((a, b))
            families[(NS[sys_id], name)].append(ui)
        # 2) 함수 선언 → 멤버. 타입 안이면 그 유닛의, 타입 밖이면 파일 유닛의 멤버. 함수 안의 함수·지역 타입의 메서드는 버린다.
        stem = os.path.splitext(os.path.basename(rel))[0]
        if spec.get('file_stem'):
            stem = spec['file_stem'](rel, stem)
        file_members, ext_units = [], {}
        for node in decls:
            if node.type not in funcs:
                continue
            name, anc = name_of(node), decl_ancestors(node)
            if not name:
                continue
            a, b = span(node)
            mem = dict(n=name, s=signature(a, header_end(node, a, b)), a=a, b=b, k='func',
                       d=docstring(node) or doc_before(src, fi['comments'], a, lines))
            recv = spec['receiver'](node, text) if spec.get('receiver') else None
            if recv:                                  # 타입 밖에 선언하는 메서드 (Go 의 func (s *Server) Reserve)
                owner = local.get(recv)
                if owner is None:                     # 타입이 다른 파일에 있다: 그 타입의 확장 유닛으로 묶는다
                    owner = ext_units.get(recv)
                    if owner is None:
                        owner = ext_units[recv] = add_unit(sys_id, f'{recv}+{stem}', spec['kind'](recv, 'extension'), rel, [], module, decl='extension', base=recv)
                        families[(NS[sys_id], recv)].append(owner)
                units[owner]['ranges'].append((a, b))
                units[owner]['members'].append(mem)
                covered.append((a, b))
            elif not anc:
                if spec.get('file_unit'):
                    mem['top'] = True
                    file_members.append(mem)
            elif all(x.type in types for x in anc) and anc[-1].id in top:
                prefix = '.'.join(name_of(x) or '' for x in reversed(anc[:-1]))
                mem['n'] = (prefix + '.' if prefix else '') + name
                units[top[anc[-1].id]]['members'].append(mem)
        # 3) 파일 유닛: 타입 밖 함수와 최상위 문장(router = APIRouter(...) 같은 것)이 여기에 속한다
        if spec.get('file_unit') and (file_members or (not top and not ext_units and code.strip())):
            fu = add_unit(sys_id, stem, spec['file_kind'](stem, bool(file_members)), rel, [], module, decl='file')
            units[fu]['members'] = file_members
            cur, rng = 0, []
            for a, b in sorted(covered):
                if a > cur:
                    rng.append((cur, a - 1))
                cur = max(cur, b + 1)
            if cur < len(code):
                rng.append((cur, len(code) - 1))
            units[fu]['ranges'] = rng or [(0, max(0, len(code) - 1))]
            units[fu]['doc'] = docstring(tree.root_node)
    return parse


def sitter_kind(name, decl):
    """이름 끝말로 유닛 종류를 어림한다. 규칙 팩이 더 잘 알면(unit_kind) 나중에 덮어쓴다."""
    for pat, kind in ((r'(Controller|Handler|Handlers|Router|Routes|Resource|Endpoint|Server|Api|API)$', 'controller'), (r'(Middleware|Filter|Interceptor|Guard)$', 'guard'),
                      (r'(Repository|Repo|Dao|DAO|Store|Storage|Cache)$', 'store'), (r'(Client|Gateway)$', 'client'), (r'Service$', 'service'),
                      (r'(Config|Configuration|Settings|Options)$', 'config'), (r'(Dto|DTO|Request|Response|Model|Entity|Event|Error|Exception|Params|Result)$', 'model')):
        if re.search(pat, name):
            return kind
    return 'ext' if decl == 'extension' else 'model' if decl in ('enum', 'record', 'type') else 'logic'


def sitter_camel(stem):
    return ''.join(p[:1].upper() + p[1:] for p in re.split(r'[_\-.]+', stem) if p)


# ───────────────────────────── 언어 명세 ─────────────────────────────
# grammar   문법 패키지의 모듈 이름 (pip install tree-sitter-<언어>)
# types     타입 선언 노드 → 선언 종류(문자열 또는 노드를 받아 정하는 함수)
# funcs     함수·메서드 선언 노드
# comments / strings / chars   지울 노드. chars 는 지우기만 하고 문자열 목록에는 넣지 않는다
# kind      (이름, 선언 종류) → 유닛 종류
# 선택: walls(안쪽 선언을 지역 선언으로 만드는 노드), supers((머리 코드, 노드, 원문 함수) → 상속 이름들), sig_clean(시그니처 손질), line_start, docstring, file_unit, file_kind, file_stem,
#       unit_span(유닛 범위로 쓸 노드), receiver(타입 밖 메서드의 소속 타입), body_field, toplevel_calls, extensions
SITTER_SPECS = {}

if 'java' in LANGUAGES:               # 종류 판별과 상속 읽기는 내장 Java 어댑터의 것을 그대로 쓴다
    def sitter_java_supers(header, node, text):
        m = re.search(r'\b(?:extends|implements)\s+(.+)$', JV_ANN.sub(lambda g: ' ' * len(g.group(0)), header), re.S)
        return [re.sub(r'<.*', '', x, flags=re.S).strip() for x in re.split(r',|\bimplements\b|\bextends\b|\bpermits\b', m.group(1)) if x.strip()] if m else []

    SITTER_SPECS['java'] = dict(
        grammar='tree_sitter_java', exts=['.java'],
        types={'class_declaration': 'class', 'interface_declaration': 'interface', 'enum_declaration': 'enum', 'record_declaration': 'record',
               'annotation_type_declaration': 'interface'},
        funcs=['method_declaration', 'constructor_declaration', 'annotation_type_element_declaration'],
        comments=['line_comment', 'block_comment'], strings=['string_literal'], chars=['character_literal'],
        walls=['object_creation_expression', 'lambda_expression', 'enum_constant'],
        kind=lambda name, decl: java_kind(name, decl, ''), supers=sitter_java_supers, sig_clean=lambda s: JV_ANN.sub('', s))

if 'python' in LANGUAGES:
    def sitter_python_supers(header, node, text):
        out, args = [], node.child_by_field_name('superclasses')
        for c in (args.named_children if args is not None else []):
            if c.type == 'call':                      # class Pair(namedtuple('Pair', 'a b')) → namedtuple
                c = c.child_by_field_name('function')
            if c is not None and c.type in ('identifier', 'attribute', 'subscript'):
                out.append(re.sub(r'\[.*', '', text(c), flags=re.S).strip().split('.')[-1])
        return out

    SITTER_SPECS['python'] = dict(
        grammar='tree_sitter_python', exts=['.py'],
        types={'class_definition': 'class'}, funcs=['function_definition'], comments=['comment'], strings=['string'],
        kind=lambda name, decl: python_kind(name), supers=sitter_python_supers, line_start=True, docstring=True, toplevel_calls=True,
        file_unit=True, file_stem=lambda rel, stem: os.path.basename(os.path.dirname(rel)) or stem if stem == '__init__' else stem,
        file_kind=lambda stem, has_funcs: python_kind(stem.title().replace('_', '')) if has_funcs else 'config' if stem in ('settings', 'config') else 'logic')


def sitter_go_receiver(node, text):
    """func (s *Server) Reserve(...) 의 Server."""
    r = node.child_by_field_name('receiver')
    if r is None:
        return None
    m = re.search(r'([A-Za-z_]\w*)\s*(?:\[[^\]]*\])?\s*\)\s*$', text(r))
    return m.group(1) if m else None


SITTER_SPECS['go'] = dict(
    grammar='tree_sitter_go', exts=['.go'], skip=lambda rel: rel.endswith('_test.go'),
    types={'type_spec': lambda n: {'struct_type': 'struct', 'interface_type': 'interface'}.get(getattr(n.child_by_field_name('type'), 'type', ''), 'type'),
           'type_alias': 'type'},
    funcs=['function_declaration', 'method_declaration', 'method_elem', 'method_spec'],
    comments=['comment'], strings=['interpreted_string_literal', 'raw_string_literal'], chars=['rune_literal'],
    kind=sitter_kind, body_field='body', receiver=sitter_go_receiver, toplevel_calls=True, extensions=True,
    unit_span=lambda n: n.parent if n.parent is not None and n.parent.type == 'type_declaration' and sum(1 for c in n.parent.named_children if c.type in ('type_spec', 'type_alias')) == 1 else n,
    file_unit=True, file_kind=lambda stem, has_funcs: sitter_kind(sitter_camel(stem), 'file'))


def sitter_register(lang, spec):
    builtin, mode = LANGUAGES.get(lang), OPTS.get('parser', 'auto')
    if mode == 'builtin' and builtin:
        return
    lang_obj, why = None, 'tree-sitter 가 설치돼 있지 않습니다'
    if _SITTER is not None:
        try:
            mod = __import__(spec['grammar'])
            lang_obj = _SITTER.Language(mod.language())
        except Exception as e:
            why = f"문법 패키지를 읽지 못했습니다 ({type(e).__name__}: {e})"
    pkg = spec['grammar'].replace('_', '-')
    if lang_obj is None:
        if builtin and mode != 'tree-sitter':
            return                                    # 내장 어댑터가 맡는다
        def missing(sys_id, rel, module, _lang=lang, _pkg=pkg, _why=why):
            raise SystemExit(f'{_lang} 분석에는 tree-sitter 문법이 필요합니다 (시스템 {sys_id}): {_why}. 설치: pip install tree-sitter {_pkg}')
        register_language(lang, exts=spec['exts'], parse=missing)
        SITTER_MISSING[lang] = pkg
        return
    parser = _SITTER.Parser(lang_obj)
    inner = sitter_parse_fn(lang, spec, lang_obj)
    skip = spec.get('skip')
    register_language(lang, exts=spec['exts'], strip=sitter_strip_fn(spec, lang_obj, parser),
                      parse=(lambda sys_id, rel, module: None if skip(rel) else inner(sys_id, rel, module)) if skip else inner,
                      toplevel_calls=spec.get('toplevel_calls', False), extensions=spec.get('extensions', False))
    SITTER_USED[lang] = 'tree-sitter'


for _lang, _spec in SITTER_SPECS.items():
    sitter_register(_lang, _spec)
