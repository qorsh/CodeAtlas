"""분석 엔진 · 글자 단위 도구: 주석·문자열 제거, 중괄호 구조, 줄 번호, 문서 주석."""
# ───────────────────────────── 공통 유틸 ─────────────────────────────

def walk(root, exts):
    p = os.path.join(ROOT, root)
    if os.path.isfile(p):
        return [root]
    out = []
    for d, dirs, files in os.walk(p):
        dirs[:] = sorted(x for x in dirs if x not in SKIP_DIRS)
        for f in sorted(files):
            if os.path.splitext(f)[1] in exts and not f.endswith('.d.ts'):
                out.append(os.path.relpath(os.path.join(d, f), ROOT))
    return out


def read(rel):
    with open(os.path.join(ROOT, rel), encoding='utf-8', errors='replace') as fh:
        return fh.read()


def skip_swift_string(s, i):
    n = len(s)
    if s.startswith('"""', i):
        j = s.find('"""', i + 3)
        return n if j < 0 else j + 3
    j = i + 1
    while j < n:
        c = s[j]
        if c == '\\':
            if j + 1 < n and s[j + 1] == '(':
                depth, j = 1, j + 2
                while j < n and depth > 0:
                    if s[j] == '(':
                        depth += 1
                    elif s[j] == ')':
                        depth -= 1
                    elif s[j] == '"':
                        j = skip_swift_string(s, j)
                        continue
                    elif s[j] == '\n':
                        break
                    j += 1
                continue
            j += 2
            continue
        if c == '"':
            return j + 1
        if c == '\n':
            return j
        j += 1
    return n


def skip_brace_expr(s, j, lang):
    """`${` 다음 위치에서 시작해 짝이 맞는 `}` 다음 위치를 돌려준다."""
    n, depth = len(s), 1
    while j < n and depth > 0:
        c = s[j]
        if c == '{':
            depth += 1
        elif c == '}':
            depth -= 1
        elif c in '"\'`':
            if lang == 'kotlin' and c != '"':
                j += 1
                continue
            j = skip_kotlin_string(s, j) if lang == 'kotlin' else skip_ts_string(s, j)
            continue
        j += 1
    return j


def skip_kotlin_string(s, i):
    n = len(s)
    if s.startswith('"""', i):
        j = s.find('"""', i + 3)
        if j < 0:
            return n
        j += 3
        while j < n and s[j] == '"':
            j += 1
        return j
    j = i + 1
    while j < n:
        c = s[j]
        if c == '\\':
            j += 2
            continue
        if c == '$' and j + 1 < n and s[j + 1] == '{':
            j = skip_brace_expr(s, j + 2, 'kotlin')
            continue
        if c == '"':
            return j + 1
        if c == '\n':
            return j
        j += 1
    return n


def skip_c_string(s, i):
    """Java·C# 등 C 계열: 큰따옴표 문자열(역슬래시 이스케이프)과 세 겹 따옴표 텍스트 블록."""
    n = len(s)
    if s.startswith('"""', i):
        j = s.find('"""', i + 3)
        return n if j < 0 else j + 3
    j = i + 1
    while j < n:
        c = s[j]
        if c == '\\':
            j += 2
            continue
        if c == '"':
            return j + 1
        if c == '\n':
            return j
        j += 1
    return n


def skip_ts_string(s, i):
    n, q = len(s), s[i]
    j = i + 1
    while j < n:
        c = s[j]
        if c == '\\':
            j += 2
            continue
        if q == '`' and c == '$' and j + 1 < n and s[j + 1] == '{':
            j = skip_brace_expr(s, j + 2, 'ts')
            continue
        if c == q:
            return j + 1
        if c == '\n' and q != '`':
            return j
        j += 1
    return n


REGEX_PREV = set('(,=:[!&|?{};+-*%<>~^')


def strip_code(src, lang):
    """주석·문자열을 같은 길이의 공백으로 바꾼 텍스트, 주석 구간, 문자열 구간을 돌려준다."""
    n = len(src)
    out = list(src)
    comments, strings = [], []

    def blank(a, b):
        for k in range(a, min(b, n)):
            if out[k] != '\n':
                out[k] = ' '

    i = 0
    while i < n:
        c = src[i]
        two = src[i:i + 2]
        if two == '//':
            j = src.find('\n', i)
            j = n if j < 0 else j
            comments.append((i, j))
            blank(i, j)
            i = j
            continue
        if two == '/*':
            depth, j = 1, i + 2
            while j < n and depth > 0:
                if src.startswith('/*', j) and lang in ('swift', 'kotlin'):
                    depth += 1
                    j += 2
                elif src.startswith('*/', j):
                    depth -= 1
                    j += 2
                else:
                    j += 1
            comments.append((i, j))
            blank(i, j)
            i = j
            continue
        if c == '"' or (c in "'`" and lang in ('ts', 'js')):
            j = skip_swift_string(src, i) if lang == 'swift' else skip_kotlin_string(src, i) if lang == 'kotlin' else skip_c_string(src, i) if lang == 'c' else skip_ts_string(src, i)
            strings.append((i, j))
            blank(i, j)
            i = j
            continue
        if c == "'" and lang in ('kotlin', 'c'):
            if src[i + 1:i + 2] == '\\':
                j = src.find("'", i + 3)
                j = i + 1 if j < 0 or j - i > 9 else j + 1
            elif src[i + 2:i + 3] == "'":
                j = i + 3
            else:
                j = i + 1
            if j > i + 1:
                blank(i, j)
            i = j
            continue
        if c == '/' and lang in ('ts', 'js'):
            k = i - 1
            while k >= 0 and src[k] in ' \t':
                k -= 1
            prev = src[k] if k >= 0 else '\n'
            word = re.search(r'(return|typeof|case|in|of|void)$', src[max(0, k - 6):k + 1])
            if prev in REGEX_PREV or prev == '\n' or word:
                j, in_class, ok = i + 1, False, False
                while j < n and src[j] != '\n':
                    if src[j] == '\\':
                        j += 2
                        continue
                    if src[j] == '[':
                        in_class = True
                    elif src[j] == ']':
                        in_class = False
                    elif src[j] == '/' and not in_class:
                        ok = True
                        break
                    j += 1
                if ok and j > i + 1:
                    j += 1
                    while j < n and src[j].isalpha():
                        j += 1
                    strings.append((i, j))
                    blank(i, j)
                    i = j
                    continue
        i += 1
    return ''.join(out), comments, strings


def parse_blocks(code):
    blocks, stack, boundary, paren = [], [], [0], [0]
    for i, ch in enumerate(code):
        if ch in '([':
            paren[-1] += 1
        elif ch in ')]':
            paren[-1] = max(0, paren[-1] - 1)
        elif ch == '{':
            blocks.append(dict(open=i, close=None, parent=stack[-1] if stack else -1, hstart=boundary[-1],
                               paren=paren[-1] > 0, kind='other', name=None, decl=None))
            stack.append(len(blocks) - 1)
            boundary.append(i + 1)
            paren.append(0)
        elif ch == '}':
            if stack:
                bi = stack.pop()
                blocks[bi]['close'] = i
                boundary.pop()
                paren.pop()
                if paren[-1] == 0:
                    boundary[-1] = i + 1
        elif ch == ';' and paren[-1] == 0:
            boundary[-1] = i + 1
    for b in blocks:
        if b['close'] is None:
            b['close'] = len(code) - 1
    return blocks


class Lines:
    def __init__(self, src):
        self.starts = [0] + [m.end() for m in re.finditer('\n', src)]

    def line(self, pos):
        return bisect_right(self.starts, pos)

    def start_of(self, pos):
        return self.starts[bisect_right(self.starts, pos) - 1]


def close_paren(code, i):
    """code[i] == '(' 일 때 짝이 맞는 ')' 위치."""
    depth, n = 0, len(code)
    while i < n:
        if code[i] in '([':
            depth += 1
        elif code[i] in ')]':
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return n - 1


def statement_end(code, i):
    """i 부터 괄호/중괄호가 닫히고 줄이 끝나는 지점(다음 줄이 이어지는 줄이 아닐 때)."""
    n, depth = len(code), 0
    while i < n:
        c = code[i]
        if c in '([{':
            depth += 1
        elif c in ')]}':
            if depth == 0:
                return i
            depth -= 1
        elif c == '\n' and depth == 0:
            m = re.match(r'\s*(\.|\?\.|\?:|&&|\|\||\+|-|\*|/|=|:|,|\)|else\b|where\b|->)', code[i + 1:i + 60])
            prev = code[:i].rstrip()[-1:] if code[:i].rstrip() else ''
            if not m and prev not in '=,+-*/&|(:.':
                return i
        i += 1
    return n


_HEADER_RE = None


def _header_re():
    """파일 머리말 주석의 첫머리: 소스 파일 이름(등록된 언어의 확장자), 작성자, 저작권 표시."""
    global _HEADER_RE
    if _HEADER_RE is None:
        exts = {e.lstrip('.') for L in LANGUAGES.values() for e in L.get('exts', [])} | {'h', 'm', 'c', 'cc', 'cpp', 'cs', 'go', 'rb', 'php', 'rs'}
        _HEADER_RE = re.compile(r'^([\w+\-]+\.(' + '|'.join(sorted(map(re.escape, exts), key=len, reverse=True)) + r')\b|Created by|Copyright\b|SPDX-License)')
    return _HEADER_RE


def doc_before(src, comments, pos, lines):
    """선언 바로 위에 붙은 문서 주석을 한 덩어리로 돌려준다."""
    cend = [c[1] for c in comments]
    k = bisect_right(cend, pos) - 1
    parts = []
    cur = pos
    while k >= 0:
        a, b = comments[k]
        gap = src[b:cur]
        if gap.strip() and not re.fullmatch(r'(\s*@\w[^\n]*)*\s*', gap):
            break
        if gap.count('\n') > 1 + gap.count('@'):
            break
        text = src[a:b]
        if text.startswith('//') and not text.startswith('///') and lines.line(a) == lines.line(a + 1) and re.match(r'//\s*(MARK|swiftlint|TODO|FIXME|eslint)', text):
            break
        parts.append(text)
        cur = a
        k -= 1
    if not parts:
        return ''
    text = '\n'.join(reversed(parts))
    text = re.sub(r'^\s*(///?|/\*\*?|\*/|\*)\s?', '', text, flags=re.M)
    text = re.sub(r'\*/\s*$', '', text)
    text = re.sub(r'\s+', ' ', text).strip()
    if _header_re().match(text) or 'Created by' in text[:120]:      # 파일 머리말(파일 이름·작성자·저작권)은 설명이 아니다
        return ''
    return text[:420]


def squeeze(s, limit=150):
    s = re.sub(r'\s+', ' ', s).strip()
    return s if len(s) <= limit else s[:limit - 1] + '…'
