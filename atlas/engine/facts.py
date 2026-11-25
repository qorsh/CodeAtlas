"""분석 엔진 · 공통 사실. 어느 언어든 같은 모양으로 뽑는다.
  · anns    선언(유닛·멤버)에 붙은 어노테이션/데코레이터: 이름, 위치 인자 문자열, 이름 인자 문자열, 인자 원문
  · scalls  문자열 리터럴이 놓인 자리: 어느 호출 사슬의 몇 번째 인자인지, 이름 인자면 그 이름
규칙 팩(rules/*.json)은 언어 문법을 모르고 이 사실만 보고 접점을 만든다."""

def template_to_path(raw, lang):
    """문자열 리터럴 본문을 경로 패턴으로. `${x}` / `\\(x)` / `$x` 는 세그먼트면 :p, 접미사면 버린다."""
    out, i, n = '', 0, len(raw)
    while i < n:
        if raw.startswith('${', i):
            j = skip_brace_expr(raw, i + 2, 'ts')
            out += ':p' if out.endswith('/') else ''
            i = j
            continue
        if raw.startswith('\\(', i):
            depth, j = 1, i + 2
            while j < n and depth:
                depth += raw[j] == '('
                depth -= raw[j] == ')'
                j += 1
            out += ':p' if out.endswith('/') else ''
            i = j
            continue
        if raw[i] == '$' and lang == 'kotlin':
            m = re.match(r'\$[A-Za-z_]\w*', raw[i:])
            if m:
                out += ':p' if out.endswith('/') else ''
                i += m.end()
                continue
        out += raw[i]
        i += 1
    return out


def member_at(u, pos):
    for mi, m in enumerate(u['members']):
        if m['a'] <= pos <= m['b']:
            return mi
    return None


def in_comment(fi, pos):
    cs = fi['comments']
    k = bisect_right([c[0] for c in cs], pos) - 1
    return k >= 0 and cs[k][0] <= pos < cs[k][1]

ANN_AT = re.compile(r'@([A-Za-z_][\w.]*)')
_KW = re.compile(r'([A-Za-z_]\w*)\s*(=(?!=)|:(?!:))')
_WORD = re.compile(r'[\w.$]+')
_CHAIN = re.compile(r'((?:[A-Za-z_$][\w$]*\s*(?:\(\s*\))?\s*[?!]{0,2}\.\s*)*[A-Za-z_$][\w$]*|\.\s*[A-Za-z_$][\w$]*)\s*$')
_TRIPLE = ('"' * 3, "'" * 3)
_FORMATTER = re.compile(r'(?:^|\.)(Sprintf|sprintf|format|formatted)$')     # 문자열을 조립해 곧바로 인자로 넘기는 호출
_PRINTF = re.compile(r'%[-+ #0-9.]*[a-zA-Z]')


def lit_text(fi, q, end):
    raw = fi['src'][q:end]
    for t in _TRIPLE:
        if raw.startswith(t) and raw.endswith(t) and len(raw) >= 6:
            return raw[3:-3]
    return raw[1:-1] if len(raw) >= 2 else ''


def _kw_at(code, a, q):
    """인자 구간 a..q 를 훑어, q 위치의 값이 어떤 이름 인자(name = / name:)에 속하는지 돌려준다."""
    depth, cur, i = 0, None, a
    while i < q:
        c = code[i]
        if c in '([{':
            depth += 1
        elif c in ')]}':
            depth -= 1
        elif c == ',' and depth == 0:
            cur = None
        elif depth == 0 and (c.isalpha() or c == '_'):
            m = _KW.match(code, i)
            if m:
                cur = m.group(1)
                i = m.end()
                continue
            i = _WORD.match(code, i).end()
            continue
        i += 1
    return cur


def _ann(fi, name, a, args):
    pos, kw, raw = [], {}, ''
    if args:
        lo, hi = args
        raw = fi['code'][lo:hi]
        k = max(bisect_right(fi['_sstarts'], lo) - 1, 0)
        while k < len(fi['strings']) and fi['strings'][k][0] < hi:
            q, end = fi['strings'][k]
            k += 1
            if q < lo:
                continue
            name_kw = _kw_at(fi['code'], lo, q)
            (kw.setdefault(name_kw, []) if name_kw else pos).append(lit_text(fi, q, end))
    return dict(name=name, a=a, pos=pos, kw=kw, raw=raw)


def anns_before(fi, pos):
    """pos 바로 위(앞)에 연달아 붙은 @이름(인자) 들을 거꾸로 훑어 모은다."""
    code, out, i = fi['code'], [], pos
    while True:
        j = i - 1
        while j >= 0 and code[j] in ' \t\r\n':
            j -= 1
        if j < 0:
            break
        args = None
        if code[j] == ')':
            depth, k = 0, j
            while k >= 0:
                if code[k] in ')]}':
                    depth += 1
                elif code[k] in '([{':
                    depth -= 1
                    if depth == 0:
                        break
                k -= 1
            if k < 0:
                break
            args = (k + 1, j)
            j = k - 1
            while j >= 0 and code[j] in ' \t':
                j -= 1
        k = j
        while k >= 0 and (code[k].isalnum() or code[k] in '_.$'):
            k -= 1
        name = code[k + 1:j + 1]
        if not name or k < 0 or code[k] != '@':
            break
        out.append(_ann(fi, name, k, args))
        i = k
    out.reverse()
    return out


def anns_from(fi, pos, limit):
    """pos 에서 시작해 선언 앞머리에 나란히 적힌 @이름(인자) 들을 모은다."""
    code, out, i = fi['code'], [], pos
    while i < limit:
        while i < limit and code[i] in ' \t\r\n':
            i += 1
        m = ANN_AT.match(code, i)
        if not m:
            break
        j, args, k = m.end(), None, m.end()
        while k < limit and code[k] in ' \t':
            k += 1
        if k < limit and code[k] == '(':
            q = close_paren(code, k)
            args = (k + 1, q)
            j = q + 1
        out.append(_ann(fi, m.group(1), m.start(), args))
        i = j
    return out


def printf_to_path(raw):
    """조립 호출의 서식 문자열을 경로 패턴으로. %s·%d 는 세그먼트면 :p, 그 밖(맨 앞의 기준 주소 등)이면 버린다."""
    out, i = '', 0
    for m in _PRINTF.finditer(raw):
        out += raw[i:m.start()]
        out += ':p' if out.endswith('/') else ''
        i = m.end()
    return out + raw[i:]


def call_ctx(fi, lo, q):
    """문자열 리터럴(q) 바로 앞 코드를 보고 (호출 사슬, 인자 순번, 이름 인자, 감싼 조립 호출)을 돌려준다. 호출 인자가 아니면 사슬은 None.
    client.Get(fmt.Sprintf("%s/items/%d", base, id)) 처럼 조립 호출의 첫 인자면 바깥 호출(client.Get)을 사슬로 본다."""
    code, j, kw = fi['code'], q - 1, None
    while j >= lo and code[j] in ' \t\r\n':
        j -= 1
    if j > lo and code[j] in '=:' and code[j - 1] not in '=!<>:':
        k = j - 1
        while k >= lo and code[k] in ' \t':
            k -= 1
        e = k
        while k >= lo and (code[k].isalnum() or code[k] == '_'):
            k -= 1
        kw = code[k + 1:e + 1] or None
        j = k
    found = None                     # (사슬, 인자 순번, 감싼 조립 호출)
    for _ in range(2):
        depth, argi = 0, 0
        while j >= lo:
            c = code[j]
            if c in ')]}':
                depth += 1
            elif c in '([{':
                if depth == 0:
                    if c != '[':
                        break
                    argi = 0            # 목록 리터럴은 한 겹 벗기고 바깥 호출을 찾는다
                else:
                    depth -= 1
            elif c == ',' and depth == 0:
                argi += 1
            elif c == ';' and depth == 0:
                j = lo - 1
                break
            j -= 1
        if j < lo or code[j] != '(':
            break
        base = max(lo, j - 160)
        m = _CHAIN.search(code[base:j])
        if not m:
            break
        chain = re.sub(r'[\s?!]|\(\s*\)', '', m.group(1))
        if found is None and argi == 0 and _FORMATTER.search(chain):
            found = (chain, argi, None)                 # 바깥 호출을 못 찾으면 이 조립 호출 자체가 답이다
            j = base + m.start(1) - 1
            continue
        found = (chain, argi, found[0] if found else None)
        break
    if found is None:
        return None, None, kw, None
    return found[0], found[1], kw, found[2]


n_ann = n_scall = 0
for fi in file_info.values():
    if 'strings' in fi:
        fi['_sstarts'] = [a for a, _ in fi['strings']]
for i, u in enumerate(units):
    if not u['file'] or is_schema(u) or not u['ranges']:
        continue
    fi = file_info[u['file']]
    start = u['ranges'][0][0]
    u['anns'] = anns_before(fi, start) + anns_from(fi, start, min(start + 800, len(fi['code'])))
    n_ann += len(u['anns'])
    for m in u['members']:
        m['anns'] = anns_before(fi, m['a']) + anns_from(fi, m['a'], m['b'])
        n_ann += len(m['anns'])
    sc = []
    for ra, rb in u['ranges']:
        k = max(bisect_right(fi['_sstarts'], ra) - 1, 0)
        while k < len(fi['strings']) and fi['strings'][k][0] <= rb:
            q, end = fi['strings'][k]
            k += 1
            if q < ra or end - q > 400 or fi['src'][q] not in '\'"`':
                continue
            mi = member_at(u, q)
            lo = max(u['members'][mi]['a'] if mi is not None else ra, q - 600)
            chain, argi, kw, wrap = call_ctx(fi, lo, q)
            sc.append(dict(q=q, end=end, lit=lit_text(fi, q, end), chain=chain, argi=argi, kw=kw, mi=mi, wrap=wrap))
    u['scalls'] = sc
    n_scall += len(sc)
