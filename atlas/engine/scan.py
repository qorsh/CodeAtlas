"""분석 엔진 · 파싱 실행과 이름 풀기. 어댑터가 만든 유닛·멤버 사이의 참조와 메서드 호출을 언어와 무관한 규칙으로 잇는다."""
# ───────────────────────────── 모듈 이름 ─────────────────────────────

def module_of(sys_id, rel):
    """파일이 든 폴더 경로에 시스템 설정의 module 규칙(정규식 치환)을 차례로 적용해 모듈 이름을 만든다."""
    d = os.path.dirname(rel)
    cfg = SYS[sys_id]
    rules = cfg.get('module')
    if rules is None:
        for root in sorted(cfg['roots'], key=len, reverse=True):
            if d == root or d.startswith(root.rstrip('/') + '/'):
                d = d[len(root):].strip('/')
                break
        return d.replace('/', ' / ') or cfg.get('module_default', '(root)')
    for pat, repl in rules:
        d = re.sub(pat, repl, d)
    return d or cfg.get('module_default', '(root)')


# ───────────────────────────── 1) 파싱 ─────────────────────────────
for s in SYSTEMS:
    if s['lang'] not in LANGUAGES:
        raise SystemExit(f"언어 어댑터가 없습니다: {s['lang']} (시스템 {s['id']}). langs/ 에 어댑터를 추가하세요.")
    L = LANGUAGES[s['lang']]
    for root in s['roots']:
        for rel in walk(root, s.get('exts') or L['exts']):
            L['parse'](s['id'], rel, module_of(s['id'], rel))

for i, u in enumerate(units):
    by_name[NS[u['sys']]][u['name']].append(i)
    if u['file']:
        ln = file_info[u['file']]['lines']
        u['loc'] = sum(ln.line(b) - ln.line(a) + 1 for a, b in u['ranges'])

# 최상위 함수 이름 → 유닛 (Kotlin·Python 처럼 다른 파일에서 함수 이름만으로 부르는 언어)
kt_funcs = defaultdict(set)
for i, u in enumerate(units):
    if u['file'] and LANGUAGES[file_info[u['file']]['lang']].get('toplevel_calls'):
        for m in u['members']:
            if m.get('top'):
                kt_funcs[m['n']].add(i)

# ───────────────────────────── 2) 참조 · 호출 ─────────────────────────────
IDENT = re.compile(r'[A-Za-z_$][\w$]*')
CALL = re.compile(r'(?:([A-Za-z_$][\w$]*)\s*[?!]?\.\s*)?([A-Za-z_$][\w$]*)\s*(?:<[^<>()]{0,60}>)?\(')
STOP = set('''map filter forEach reduce contains append insert remove first last count sorted sort join split replace trim push pop get set has add delete
keys values entries find findMany findUnique findFirst create update upsert then catch send json status log error warn encode decode data init description hash
isEmpty min max toString parse stringify test match slice substring includes startsWith endsWith some every flatMap compactMap removeAll write read close open
start stop cancel resume perform fetch run launch collect emit copy equals let also apply with use require check toList toSet joinToString async await sleep
store body sink receive assign print debug info fatalError precondition guard string int double date url bool array dict object value key index item text title
onAppear onChange task sheet padding frame font foregroundColor background overlay clipShape accessibilityLabel remember mutableStateOf LaunchedEffect Text Column Row Box
Button Image Spacer Modifier next call bind listen register resolve reject of from all race any assign defineProperty toISOString getTime now floor ceil round abs
isNotEmpty isBlank isNotBlank orEmpty firstOrNull lastOrNull filterNot mapNotNull associate groupBy sortedBy sortedByDescending take drop plus minus toInt toLong
trimmingCharacters components lowercased uppercased hasPrefix hasSuffix addingPercentEncoding appendingPathComponent setValue removeValue object bool integer
'''.split())


def norm(s):
    return re.sub(r'[^a-z0-9]', '', s.lower())


def unit_text(u, which='code'):
    fi = file_info[u['file']]
    return '\n'.join(fi[which][a:b + 1] for a, b in u['ranges'])


member_names = [defaultdict(list) for _ in units]
for i, u in enumerate(units):
    for mi, m in enumerate(u['members']):
        member_names[i][m['n'].split('.')[-1]].append(mi)

ts_import_cache = {}
for i, u in enumerate(units):
    if is_schema(u):
        continue
    fi = file_info[u['file']]
    ns = NS[u['sys']]
    lang = fi['lang']
    if LANGUAGES[lang].get('imports'):
        if u['file'] not in ts_import_cache:
            imp = LANGUAGES[lang]['imports'](u['file'], SYS[u['sys']].get('alias_root'))
            for other in fi['units']:
                imp.setdefault(units[other]['name'], other)
            ts_import_cache[u['file']] = imp
        scope = ts_import_cache[u['file']]
    else:
        scope = None

    def lookup(tok):
        if scope is not None:
            t = scope.get(tok)
            return [t] if t is not None else []
        return by_name[ns].get(tok, [])

    def refs_in(text):
        c = Counter()
        for tok, n in Counter(IDENT.findall(text)).items():
            for t in lookup(tok):
                if t != i:
                    c[t] += n
            if LANGUAGES[lang].get('toplevel_calls') and tok in kt_funcs and len(kt_funcs[tok]) == 1 and len(tok) > 3:
                t = next(iter(kt_funcs[tok]))
                if t != i and re.search(r'\b' + re.escape(tok) + r'\s*\(', text):
                    c[t] += 1
        return c

    text = unit_text(u)
    u['refs'].update(refs_in(text))
    if u.get('base'):
        for t in by_name[ns].get(u['base'], []):
            u['ext_of'] = t
    for m in u['members']:
        mtext = fi['code'][m['a']:m['b'] + 1]
        m['r'] = sorted(refs_in(mtext))
        m['_text'] = mtext


def family_of(t):
    u = units[t]
    base = u.get('base') or u['name']
    return families.get((NS.get(u['sys'], ''), base), [t]) if LANGUAGES[file_info[u['file']]['lang']].get('extensions') else [t]


for i, u in enumerate(units):
    if is_schema(u):
        continue
    lang = file_info[u['file']]['lang']
    unit_refs = set(t for t in u['refs'] if isinstance(t, int))
    fam = [t for t in family_of(i)]
    for mi, m in enumerate(u['members']):
        calls = []
        seen = set()
        for cm in CALL.finditer(m.pop('_text')):
            recv, callee = cm.group(1), cm.group(2)
            if callee in TS_KEYWORDS or callee in ('guard', 'when', 'catch', 'func', 'fun'):
                continue
            target = None
            if recv is None or recv in ('self', 'this', 'Self'):
                for t in [i] + [x for x in fam if x != i]:
                    for tm in member_names[t].get(callee, []):
                        if not (t == i and tm == mi):
                            target = (t, tm)
                            break
                    if target:
                        break
                if not target and LANGUAGES[lang].get('toplevel_calls') and callee in kt_funcs and len(kt_funcs[callee]) == 1:
                    t = next(iter(kt_funcs[callee]))
                    if t != i and member_names[t].get(callee):
                        target = (t, member_names[t][callee][0])
            if not target and recv and recv not in ('self', 'this', 'Self'):
                cands = set(m['r']) | unit_refs
                expanded = set()
                for t in cands:
                    expanded.update(family_of(t))
                expanded.discard(i)
                named = [t for t in expanded if units[t]['name'].split('+')[0] == recv or (units[t].get('base') == recv)]
                pool = named if named else expanded
                having = [(t, member_names[t][callee][0]) for t in pool if callee in member_names[t]]
                if having:
                    if named:
                        target = having[0]
                    else:
                        r = norm(recv)
                        aff = [h for h in having if len(r) > 3 and (r in norm(units[h[0]]['name']) or norm(units[h[0]]['name'].split('+')[0]) in r)]
                        if aff:
                            target = aff[0]
                        elif len(having) == 1 and callee not in STOP and len(callee) > 3:
                            target = having[0]
            if target and target not in seen:
                seen.add(target)
                calls.append(list(target))
        m['c'] = calls
        for t, _ in calls:
            if t != i:
                u['refs'][t] += 1
