"""분석 엔진 · 부가 정보: 주석 속 타 플랫폼 이름, 관련 테스트, 문자열 단서(화면 문구·오류 코드)."""
# 3-4) 주석에 `이름` 꼴로 적힌 식별자. 다른 플랫폼의 대응 구현을 가리키는지는 연결 단계에서 가린다.
mirror_toks = {}
for i, u in enumerate(units):
    if not u['file'] or is_schema(u):
        continue
    fi = file_info[u['file']]
    text = u['doc'] + ' ' + ' '.join(fi['src'][a:b] for a, b in fi['comments'] if any(ra <= a <= rb for ra, rb in u['ranges']))
    toks = sorted(set(re.findall(r'`([A-Za-z_][\w.\-+]*)', text)))
    if toks:
        mirror_toks[i] = toks

# ───────────────────────────── 4) 테스트 연결 ─────────────────────────────
for sys_id, root, ext in TEST_ROOTS:
    for rel in walk(root, [ext]):
        text = read(rel)
        toks = set(IDENT.findall(text))
        base = os.path.basename(rel)
        for i, u in enumerate(units):
            if not u['file'] or is_schema(u):
                continue
            hit = u['name'].split('+')[0] in toks if ext != '.js' else (
                u['name'] in toks or re.search(r'/' + re.escape(re.sub(r'\.ts$', '', os.path.basename(u['file']))) + r"['\"]", text) is not None)
            if hit and len(u['name']) > 4:
                u['tests'].append(base)

# ───────────────────────────── 5) 문자열 단서 ─────────────────────────────
# 화면 문구(한글), 오류 코드, 설정 키처럼 문의 내용과 바로 맞닿는 문자열만 골라 담는다. 코드 본문은 담지 않는다.
HANGUL = re.compile(r'[가-힣]')
IDENT_LIT = re.compile(r'^[A-Za-z][A-Za-z0-9]*(?:[_.\-:/][A-Za-z0-9]+)+$')
CONST_LIT = re.compile(r'^[A-Z][A-Z0-9_]{5,}$')
NOISE_LIT = re.compile(r'^(application|text|image|multipart|audio|video)/|^(Content|Accept|Cache|User|X-Forwarded|If)-|^(utf-8|UTF-8|en_US_POSIX|no-store|no-cache|no-referrer|keep-alive)$|yyyy|^[a-z]+\.[a-z]{2,4}$')
SECRETISH = re.compile(r'[A-Za-z0-9+/=_\-]{28,}|@[\w-]+\.|://[^/]*:[^/]*@')


def clean_literal(raw):
    t = raw
    for q in ('"""', '"', "'", '`'):
        if t.startswith(q) and t.endswith(q) and len(t) >= 2 * len(q):
            t = t[len(q):-len(q)]
            break
    t = re.sub(r'\\\((?:[^()]|\([^()]*\))*\)|\$\{[^}]*\}|\$[A-Za-z_]\w*|%(?:\d+\$)?[@dsf]', '…', t)
    t = re.sub(r'\\[nt"]', ' ', t)
    t = re.sub(r'\s+', ' ', t).strip()
    if not t or SECRETISH.search(t) or NOISE_LIT.match(t):
        return None
    if HANGUL.search(t):
        return t[:70] if 2 <= len(t) else None
    if len(t) <= 44 and (IDENT_LIT.match(t) or CONST_LIT.match(t)):
        return t
    return None


android_strings = {}
for rel_xml in OPTS.get('strings_xml', []):
    px = os.path.join(ROOT, rel_xml)
    if os.path.exists(px):
        for m in re.finditer(r'<string name="(\w+)"[^>]*>(.*?)</string>', open(px, encoding='utf-8').read(), re.S):
            android_strings.setdefault(m.group(1), re.sub(r'\s+', ' ', re.sub(r'\\[n\'"]|%(?:\d+\$)?[sd]', ' ', m.group(2))).strip()[:70])

n_lit = 0
for i, u in enumerate(units):
    if not u['file'] or is_schema(u):
        continue
    fi = file_info[u['file']]
    src = fi['src']
    starts = [a for a, _ in fi['strings']]
    seen, unit_lits = set(), []
    budget = 60
    for ra, rb in u['ranges']:
        k = bisect_right(starts, ra) - 1
        k = max(k, 0)
        while k < len(fi['strings']) and fi['strings'][k][0] <= rb:
            a, b = fi['strings'][k]
            k += 1
            if a < ra:
                continue
            t = clean_literal(src[a:b])
            if not t or t in seen or budget <= 0:
                continue
            seen.add(t)
            budget -= 1
            mi = member_at(u, a)
            if mi is not None:
                lst = u['members'][mi].setdefault('x', [])
                if len(lst) < 8:
                    lst.append(t)
            elif len(unit_lits) < 10:
                unit_lits.append(t)
            n_lit += 1
        if android_strings:
            for m in re.finditer(r'R\.string\.(\w+)', fi['code'][ra:rb + 1]):
                t = android_strings.get(m.group(1))
                if not t or t in seen or budget <= 0:
                    continue
                seen.add(t)
                budget -= 1
                mi = member_at(u, ra + m.start())
                if mi is not None:
                    lst = u['members'][mi].setdefault('x', [])
                    if len(lst) < 8:
                        lst.append(t)
                elif len(unit_lits) < 10:
                    unit_lits.append(t)
                n_lit += 1
    if unit_lits:
        u['x'] = unit_lits

# ───────────────────────────── 6) 코드에 적힌 서버 주소 ─────────────────────────────
# "https://api.example.com" 처럼 문자열에 적힌 주소에서 호스트만 모은다. 경로만 쓰는 호출이 어느 서버로 가는지 가릴 때 단서가 된다.
URL_HOST = re.compile(r'https?://([A-Za-z0-9](?:[A-Za-z0-9.\-]*[A-Za-z0-9])?(?::\d{2,5})?)(?![A-Za-z0-9.\-]*[{$%])')
host_mentions = []     # [unit, host]
for i, u in enumerate(units):
    if not u['file'] or is_schema(u):
        continue
    fi = file_info[u['file']]
    seen_hosts = set()
    for a, b in u['ranges']:
        for m in URL_HOST.finditer(fi['src'], a, b + 1):
            h = m.group(1).lower()
            if h not in seen_hosts and not in_comment(fi, m.start()):
                seen_hosts.add(h)
                host_mentions.append([i, h])
