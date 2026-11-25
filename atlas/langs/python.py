"""언어 어댑터 · Python. 들여쓰기로 범위를 잡는다. class 는 유닛, 메서드는 멤버, 최상위 함수는 파일(모듈) 유닛의 멤버가 된다.

중괄호 언어와 문법이 전혀 달라도, 내놓는 것은 같은 모양(유닛·멤버의 범위)이라 뒤 단계는 손대지 않는다.
"""
PY_DECL = re.compile(r'[ \t]*(?:(async[ \t]+)?def[ \t]+([A-Za-z_]\w*)[ \t]*\(|class[ \t]+([A-Za-z_]\w*))')


def strip_python(src):
    """# 주석과 따옴표 문자열(세 겹 포함)을 같은 길이의 공백으로 바꾼다."""
    n, out, comments, strings, i = len(src), list(src), [], [], 0

    def blank(a, b):
        for k in range(a, min(b, n)):
            if out[k] != '\n':
                out[k] = ' '

    while i < n:
        c = src[i]
        if c == '#':
            j = src.find('\n', i)
            j = n if j < 0 else j
            comments.append((i, j))
            blank(i, j)
            i = j
        elif c in '"\'':
            q3 = src[i:i + 3]
            if q3 in ('"""', "'''"):
                j = src.find(q3, i + 3)
                j = n if j < 0 else j + 3
            else:
                j = i + 1
                while j < n and src[j] != c and src[j] != '\n':
                    j += 2 if src[j] == '\\' else 1
                j = min(j + 1, n)
            strings.append((i, j))
            blank(i, j)
            i = j
        else:
            i += 1
    return ''.join(out), comments, strings


def python_kind(name):
    for pat, kind in ((r'(Router|Controller|View|ViewSet|Resource|Handler)s?$', 'controller'), (r'(Repository|Repo|Dao|Store)$', 'store'),
                      (r'(Client|Gateway)$', 'client'), (r'Service$', 'service'), (r'(Config|Settings)$', 'config'),
                      (r'(Model|Schema|Dto|Request|Response|Event|Error|Exception)$', 'model')):
        if re.search(pat, name):
            return kind
    return 'logic'


def parse_python(sys_id, rel, module):
    fi = load_file(rel, 'python')
    src, code, lines = fi['src'], fi['code'], fi['lines']
    starts = lines.starts
    # 논리 줄(괄호가 닫힌 뒤 시작하는 줄)마다 들여쓰기와 선언 여부를 본다
    rows, depth = [], 0
    for ln, a in enumerate(starts):
        b = starts[ln + 1] if ln + 1 < len(starts) else len(code)
        text = code[a:b]
        if depth == 0 and text.strip():
            indent = len(text) - len(text.lstrip(' \t'))
            rows.append((a, indent, PY_DECL.match(text)))
        depth = max(0, depth + sum(text.count(c) for c in '([{') - sum(text.count(c) for c in ')]}'))
    decls = []
    for k, (a, indent, m) in enumerate(rows):
        if not m:
            continue
        end = len(code) - 1
        for a2, indent2, _ in rows[k + 1:]:
            if indent2 <= indent:
                end = a2 - 1
                break
        while end > a and src[end] in ' \t\r\n':
            end -= 1
        decls.append(dict(a=a, indent=indent, end=end, kind='class' if m.group(3) else 'func', name=m.group(3) or m.group(2)))

    def docstring(d):
        """선언 바로 아래 첫 문자열을 설명으로 쓴다."""
        colon = code.find(':', code.find(')', d['a']) if d['kind'] == 'func' else d['a'])
        for q, e in fi['strings']:
            if q > colon >= 0:
                return re.sub(r'\s+', ' ', src[q:e].strip('"\' \n')).strip()[:300] if not code[colon + 1:q].strip() else ''
        return ''

    def sig(d):
        e = code.find(':\n', d['a'])
        return squeeze(src[d['a']:e if 0 <= e <= d['end'] else min(d['end'], d['a'] + 200)])

    stem = os.path.splitext(os.path.basename(rel))[0]
    stem = os.path.basename(os.path.dirname(rel)) or stem if stem == '__init__' else stem
    class_units, covered = [], []
    for d in decls:
        if d['kind'] == 'class' and d['indent'] == 0:
            ui = add_unit(sys_id, d['name'], python_kind(d['name']), rel, [(d['a'], d['end'])], module, decl='class')
            units[ui]['doc'] = docstring(d) or doc_before(src, fi['comments'], d['a'], lines)
            m = re.match(r'[ \t]*class[ \t]+\w+[ \t]*\(([^)]*)\)', code[d['a']:d['end'] + 1])
            if m:
                units[ui]['conforms'] = [x.strip().split('.')[-1] for x in m.group(1).split(',') if x.strip() and '=' not in x][:5]
            class_units.append((d, ui))
            covered.append((d['a'], d['end']))
            families[(NS[sys_id], d['name'])].append(ui)
    file_unit = None
    for d in decls:
        if d['kind'] != 'func':
            continue
        outer = [p for p in decls if p is not d and p['a'] < d['a'] <= p['end'] and p['indent'] < d['indent']]
        parent = max(outer, key=lambda p: p['a']) if outer else None
        mem = dict(n=d['name'], s=sig(d), a=d['a'], b=d['end'], k='func', d=docstring(d))
        if parent is None:
            if file_unit is None:
                file_unit = add_unit(sys_id, stem, python_kind(stem.title().replace('_', '')), rel, [], module, decl='file')
            mem['top'] = True
            units[file_unit]['members'].append(mem)
        elif parent['kind'] == 'class' and parent['indent'] == 0:
            owner = next(ui for p, ui in class_units if p is parent)
            units[owner]['members'].append(mem)
    if file_unit is None and not class_units and code.strip():
        file_unit = add_unit(sys_id, stem, 'config' if stem in ('settings', 'config') else 'logic', rel, [], module, decl='file')
    if file_unit is not None:
        # 모듈 유닛의 범위는 클래스 밖 전체: 모듈 수준 문장(router = APIRouter(...))도 여기에 속한다
        cur, rng = 0, []
        for a, b in sorted(covered):
            if a > cur:
                rng.append((cur, a - 1))
            cur = max(cur, b + 1)
        if cur < len(code):
            rng.append((cur, len(code) - 1))
        units[file_unit]['ranges'] = rng or [(0, max(0, len(code) - 1))]
        q = fi['strings'][0] if fi['strings'] and not code[:fi['strings'][0][0]].strip() else None
        units[file_unit]['doc'] = re.sub(r'\s+', ' ', src[q[0]:q[1]].strip('"\' \n')).strip()[:300] if q else ''


register_language('python', exts=['.py'], parse=parse_python, strip=strip_python, toplevel_calls=True)
