"""언어 어댑터 · Dart (Flutter). class/mixin/enum/extension 을 유닛으로, 메서드·게터·화살표 함수·본문 없는 선언을 멤버로 만든다.
최상위 함수는 파일 유닛에 담는다. 문법까지만 알고, dio·http·Retrofit 같은 통신 방식은 rules/*.json 이 맡는다.
"""
DT_TYPE = re.compile(r'(?<![\w.$])(class|mixin|enum|extension)\s+(?!on\b|type\b)([A-Za-z_$]\w*)')
DT_FUNC = re.compile(r'(?<![\w.$])([A-Za-z_$]\w*(?:\.[A-Za-z_$]\w*)?)\s*(?:<[^<>()]*>)?\s*\(')
DT_GET = re.compile(r'(?<![\w.$])get\s+([A-Za-z_$]\w*)\s*(?:async\*?|sync\*)?\s*$')
DT_NOT_FUNC = {'if', 'for', 'while', 'switch', 'catch', 'try', 'do', 'else', 'return', 'new', 'throw', 'super', 'this', 'assert', 'await', 'yield', 'on', 'in', 'is', 'as',
               'Function', 'when', 'case', 'default', 'with', 'required', 'const', 'final', 'var', 'late'}
DT_ANN = JV_ANN if 'JV_ANN' in globals() else re.compile(r'@[\w.]+\s*(\((?:[^()]|\((?:[^()]|\([^()]*\))*\))*\))?')


def dart_skip_string(s, i):
    """i 에서 시작하는 문자열 리터럴이 끝난 다음 위치. '…' · "…" · 세 겹 따옴표 · r'…' 를 알고, ${ … } 안의 문자열도 따라간다."""
    n, raw = len(s), False
    if s[i] in 'rR':
        raw, i = True, i + 1
    q = s[i]
    if s.startswith(q * 3, i):
        j = s.find(q * 3, i + 3)
        return n if j < 0 else j + 3
    j = i + 1
    while j < n:
        c = s[j]
        if c == '\\' and not raw:
            j += 2
            continue
        if c == '$' and not raw and s[j + 1:j + 2] == '{':
            depth, j = 1, j + 2
            while j < n and depth:
                if s[j] in '\'"':
                    j = dart_skip_string(s, j)
                    continue
                depth += s[j] == '{'
                depth -= s[j] == '}'
                j += 1
            continue
        if c == q:
            return j + 1
        if c == '\n':
            return j
        j += 1
    return n


def dart_strip(src):
    """주석·문자열을 같은 길이의 공백으로 바꾼 텍스트, 주석 구간, 문자열 구간. Dart 는 /* */ 가 겹칠 수 있다."""
    n, out, comments, strings = len(src), list(src), [], []

    def blank(a, b):
        for k in range(a, min(b, n)):
            if out[k] != '\n':
                out[k] = ' '

    i = 0
    while i < n:
        c, two = src[i], src[i:i + 2]
        if two == '//':
            j = src.find('\n', i)
            j = n if j < 0 else j
            comments.append((i, j))
            blank(i, j)
            i = j
        elif two == '/*':
            depth, j = 1, i + 2
            while j < n and depth:
                if src.startswith('/*', j):
                    depth, j = depth + 1, j + 2
                elif src.startswith('*/', j):
                    depth, j = depth - 1, j + 2
                else:
                    j += 1
            comments.append((i, j))
            blank(i, j)
            i = j
        elif c in '\'"' or (c in 'rR' and src[i + 1:i + 2] in ('\'', '"') and not (i and (src[i - 1].isalnum() or src[i - 1] in '_$'))):
            q = i + (c in 'rR')              # 문자열 구간은 따옴표부터: 뒤 단계가 따옴표를 떼고 본문을 읽는다
            j = dart_skip_string(src, i)
            strings.append((q, j))
            blank(i, j)
            i = j
        else:
            i += 1
    return ''.join(out), comments, strings


def classify_dart(header, parent_kind):
    """블록 머리(여는 중괄호 앞)가 타입 선언인지, 함수·메서드인지 가린다."""
    h = DT_ANN.sub(lambda m: ' ' * len(m.group(0)), header)       # 어노테이션 인자 속 괄호·등호에 속지 않게 가린다
    t = None
    for t in DT_TYPE.finditer(h):
        pass
    if t and '=' not in h[t.end():] and '(' not in h[t.end():]:
        return 'type', t.group(2), t.group(1), t.start()
    if parent_kind in ('type', None):
        g = DT_GET.search(h)
        if g and '=' not in h[:g.start()]:
            return 'func', g.group(1), 'getter', g.start()
        m = DT_FUNC.search(h)
        if m and m.group(1).split('.')[0] not in DT_NOT_FUNC and '=' not in h[:m.start()]:
            q = close_paren(h, m.end() - 1)
            if re.fullmatch(r'\s*(?::[^{]*)?\s*(?:async\*?|sync\*)?\s*', h[q + 1:], re.S):      # 생성자의 초기화 목록( : super(…) )과 async 표시까지 허용
                return 'func', m.group(1), 'method', m.start()
    return 'other', None, None, None


def dart_kind(name, decl, header):
    if re.search(r'\bextends\s+(Stateless|Stateful|Hook|Consumer|Inherited)\w*Widget\b|\bextends\s+(\w*State|ConsumerState)\s*<', header) or re.search(r'(Page|Screen|View|Widget|Dialog|Sheet|Tile|Card|Tab|Bar|Button|Form|Layout)$', name):
        return 'view'
    for pat, kind in ((r'(Bloc|Cubit|Controller|ViewModel|Notifier|Provider|Store|Reducer)$', 'reducer'), (r'(Repository|Dao|Storage|Database|Preferences|Cache|Box)$', 'store'),
                      (r'(Api|API|Client|DataSource|Remote|Gateway|Http)\w*$', 'client'), (r'Service(Impl)?$', 'service'), (r'(Config|Constants?|Env|Routes?|Theme|Colors|Strings)$', 'config'),
                      (r'(Model|Dto|DTO|Entity|State|Event|Request|Response|Exception|Error|Failure|Args|Params)$', 'model'), (r'(Util|Utils|Helper|Extension|Extensions|Mixin)$', 'util')):
        if re.search(pat, name):
            return kind
    return 'model' if decl == 'enum' else 'util' if decl in ('extension', 'mixin') else 'logic'


def parse_dart(sys_id, rel, module):
    fi = load_file(rel, 'dart')
    src, code, blocks, lines = fi['src'], fi['code'], fi['blocks'], fi['lines']
    base_file = os.path.splitext(os.path.basename(rel))[0]
    for b in blocks:
        pk = blocks[b['parent']]['kind'] if b['parent'] >= 0 else None
        b['kind'], b['name'], b['sub'], b['decl'] = classify_dart(code[b['hstart']:b['open']], pk) if not b['paren'] else ('other', None, None, None)
    block_unit = {}

    def start_of(a, stop):
        while a < stop and code[a] in ' \t\r\n':
            a += 1
        return a

    for bi, b in enumerate(blocks):
        if b['kind'] != 'type' or b['parent'] >= 0:
            continue
        a = start_of(b['hstart'], b['open'])
        header = code[a:b['open']]
        ui = add_unit(sys_id, b['name'], dart_kind(b['name'], b['sub'], header), rel, [(a, b['close'])], module, decl=b['sub'])
        units[ui]['doc'] = doc_before(src, fi['comments'], a, lines)
        m = re.search(r'\b(?:extends|with|implements|on)\s+(.+)$', header, re.S)
        if m:
            units[ui]['conforms'] = [re.sub(r'<.*', '', x).strip() for x in re.split(r',|\bextends\b|\bwith\b|\bimplements\b|\bon\b', m.group(1)) if x.strip()][:5]
        block_unit[bi] = ui
        families[(NS[sys_id], b['name'])].append(ui)

    file_unit = [None]

    def top_owner():
        if file_unit[0] is None:
            file_unit[0] = next((ui for ui in block_unit.values() if units[ui]['name'].lower() == base_file.replace('_', '').lower()), None)
            if file_unit[0] is None:
                file_unit[0] = add_unit(sys_id, base_file, 'logic', rel, [], module, decl='file')
                families[(NS[sys_id], base_file)].append(file_unit[0])
        return file_unit[0]

    def add_member(owner, name, a, sig_end, end, top=False):
        if top:
            units[owner]['ranges'].append((a, end))
        units[owner]['members'].append(dict(n=name, s=squeeze(DT_ANN.sub('', src[a:sig_end])), a=a, b=end, k='func', top=top, d=doc_before(src, fi['comments'], a, lines)))

    # 본문이 중괄호인 함수: 타입 바로 아래의 메서드와 최상위 함수만 (함수 안의 함수·클로저는 담지 않는다)
    for bi, b in enumerate(blocks):
        if b['kind'] == 'type' and b['parent'] >= 0 and b['parent'] in block_unit and b['name'] not in units[block_unit[b['parent']]]['nested']:
            units[block_unit[b['parent']]]['nested'].append(b['name'])
        if b['kind'] != 'func':
            continue
        a = start_of(b['hstart'], b['open'])
        if b['parent'] < 0:
            add_member(top_owner(), b['name'], a, b['open'], b['close'], top=True)
        elif b['parent'] in block_unit:
            add_member(block_unit[b['parent']], b['name'], a, b['open'], b['close'])

    # 중괄호 없는 선언: 화살표 함수( => … ; )와 본문 없는 메서드(추상 메서드, Retrofit 방식 인터페이스). 타입 본문 바로 아래와 파일 맨 위에서 ; 로 끝나는 문장을 본다
    def flat(lo, hi, owner_of):
        i, seg, depth = lo, lo, 0
        while i < hi:
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
                arrow = re.search(r'=>', DT_ANN.sub(lambda m: ' ' * len(m.group(0)), stmt))
                head = stmt[:arrow.start()] if arrow else stmt
                bare = head
                while True:                    # 괄호 안(이름 붙은 인자의 { … } 포함)은 가리고 본다: 바깥에 중괄호가 있으면 선언이 아니라 블록이 섞인 문장이다
                    cut = re.sub(r'\([^()]*\)', lambda m: ' ' * len(m.group(0)), bare)
                    if cut == bare:
                        break
                    bare = cut
                if '{' not in bare and not re.match(r'\s*(import|export|part|library|typedef)\b', stmt):
                    kind, name, _, _ = classify_dart(head, 'type')
                    if kind == 'func' and (arrow or ')' in head):
                        owner, top = owner_of()
                        add_member(owner, name, a, seg + arrow.start() if arrow else i, i, top=top)
                seg = i + 1
            i += 1

    for bi, ui in block_unit.items():
        flat(blocks[bi]['open'] + 1, blocks[bi]['close'], lambda ui=ui: (ui, False))
    flat(0, len(code), lambda: (top_owner(), True))
    if file_unit[0] is not None and units[file_unit[0]]['decl'] == 'file':
        units[file_unit[0]]['doc'] = doc_before(src, fi['comments'], min((a for a, _ in units[file_unit[0]]['ranges']), default=0), lines)


register_language('dart', exts=['.dart'], parse=parse_dart, strip=dart_strip, toplevel_calls=True)
