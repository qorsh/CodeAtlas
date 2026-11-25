"""분석 엔진 · 응답 코드 지점. 코드의 어느 메서드가 어떤 HTTP 상태 코드를 내려주는지 모은다.

  1) 규칙 팩의 status 규칙이 찾은 자리 (http.Error(w, …, http.StatusConflict), HTTPException(status_code=404), res.status(500) …)
  2) 예외를 던지는 자리: throw new X(…) / raise X(…) 의 X 가 상태 코드를 가진 예외일 때
       · 예외 클래스에 코드가 붙어 있다 (@ResponseStatus, 상속한 프레임워크 예외의 이름)
       · 예외 처리기가 코드를 정한다 (@ExceptionHandler(X.class) + @ResponseStatus)
       · 어느 것도 없으면 클래스 이름으로 어림한다 (…NotFound… → 404). 이때는 '추정' 표시를 남긴다
결과는 멤버의 st = [[코드, 줄, 문구, 추정 여부]] 로 나간다. 문구는 그 자리에 함께 적힌 문자열 하나(화면 문구·오류 코드로 볼 만한 것만)다.
"""
def statement_span(code, pos, end):
    """상태 코드가 적힌 자리(pos~end)를 품은 문장의 범위. 메시지 문자열이 코드 앞에 오는 꼴(http.Error(w, "…", code))도 잡으려고 문장 전체를 본다."""
    start = code.rfind('\n', 0, pos) + 1
    for _ in range(4):                 # 인자를 여러 줄에 나눠 쓴 호출: 윗줄이 ( 나 , 로 끝나면 같은 문장이다
        prev_end = start - 1
        if prev_end <= 0:
            break
        prev_start = code.rfind('\n', 0, prev_end) + 1
        if not code[prev_start:prev_end].rstrip().endswith(('(', ',', '+', '->', '=>')) and not code[start:pos].lstrip().startswith(('.', ')', '?')):
            break
        start = prev_start
    cut = max(code.rfind(c, start, pos) for c in ';{}')
    if cut >= start:
        start = cut + 1
    depth, stop = code.count('(', start, end) - code.count(')', start, end), end
    while stop < len(code) and stop < end + 400:
        c = code[stop]
        if c == '(':
            depth += 1
        elif c == ')':
            depth -= 1
        elif c == ';' or (c == '\n' and depth <= 0):
            break
        stop += 1
    return start, stop


def status_message(fi, pos, end):
    """그 자리와 같은 문장에 적힌 첫 문자열. 문의에 그대로 나올 만한 문구일 때만 담는다."""
    start, stop = statement_span(fi['code'], pos, end)
    k = bisect_right(fi['_sstarts'], start - 1)
    while k < len(fi['strings']) and fi['strings'][k][0] <= stop:
        q, e = fi['strings'][k]
        k += 1
        if e - q <= 200:
            t = clean_literal(lit_text(fi, q, e))
            if t:
                return t
    return ''


# 1) 예외 클래스가 가진 코드: 규칙이 붙인 것(@ResponseStatus) → 상속한 이름 → 예외 처리기
unit_status = {}                       # 유닛 번호 → (코드, 추정 여부)
for i, u in enumerate(units):
    if u.get('status'):
        unit_status[i] = (u['status'], 0)
        continue
    for c in u.get('conforms') or []:
        code = status_of(re.sub(r'<.*', '', c)) if re.search(r'(Exception|Error)$', c) else None
        if code:
            unit_status[i] = (code, 0)
            break
for i, u in enumerate(units):          # @ExceptionHandler(OrderNotFoundException.class) + @ResponseStatus(HttpStatus.NOT_FOUND)
    for mi, m in enumerate(u['members']):
        handled = [x for an in m.get('anns', []) if an['name'].split('.')[-1] in ('ExceptionHandler', 'exception_handler', 'errorhandler', 'Catch')
                   for x in re.findall(r'([A-Z]\w+)(?:\.class|::class)?', an['raw'])]
        if not handled:
            continue
        codes = [c for c, _, _, _ in m.get('_st', [])]
        if not codes:
            continue
        for name in handled:
            for t in by_name[NS[u['sys']]].get(name, []):
                unit_status.setdefault(t, (codes[0], 0))

# 2) 예외를 만드는 자리. throw·raise 뒤에 오면 이름이 무엇이든 보고, 그냥 new X(…) 는 X 가 예외로 보일 때만 본다
#    (orElseThrow(() -> new OrderNotFoundException("…")) 처럼 던지는 말 없이 넘기는 꼴이 흔하다)
THROW = re.compile(r'\b(throw\s+new|throw|raise|new)\s+((?:[A-Za-z_]\w*\.)*[A-Z]\w*)\b(?!\s*\.)')
for i, u in enumerate(units):
    if not u['file'] or is_schema(u) or not u['members']:
        continue
    fi = file_info[u['file']]
    for a, b in u['ranges']:
        for m in THROW.finditer(fi['code'][a:b + 1]):
            pos, name = a + m.start(), m.group(2).split('.')[-1]
            mi = member_at(u, pos)
            if mi is None:
                continue
            known = next((unit_status[t] for t in by_name[NS[u['sys']]].get(name, []) if t in unit_status), None)
            looks_like = bool(re.search(r'(Exception|Error)$', name))
            if m.group(1) == 'new' and not known and not looks_like:
                continue
            found = known
            if found is None and looks_like and status_of(name):
                found = (status_of(name), 0)
            if found is None and (looks_like or m.group(1) != 'new') and status_guess(name):
                found = (status_guess(name), 1)
            if found:
                u['members'][mi].setdefault('_st', []).append((found[0], pos, a + m.end(), found[1]))

# 3) 정리: 같은 줄의 같은 코드는 하나로, 문구를 붙인다
n_status = 0
for i, u in enumerate(units):
    if not u['file'] or is_schema(u):
        continue
    fi = file_info[u['file']]
    for m in u['members']:
        raw = m.pop('_st', None)
        if not raw:
            continue
        seen, out = set(), []
        for code, pos, end, guess in sorted(raw, key=lambda x: (x[1], x[3])):
            line = fi['lines'].line(pos)
            if (code, line) in seen:
                continue
            seen.add((code, line))
            out.append([code, line, status_message(fi, pos, end), guess])
        m['st'] = out[:12]
        n_status += len(m['st'])
for i, c in unit_status.items():
    units[i]['status'] = c[0]
