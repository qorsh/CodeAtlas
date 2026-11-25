"""분석 엔진 · 규칙 팩 실행. 프레임워크 지식은 rules/*.json 에 데이터로 있고, 여기는 그것을 사실(facts)에 적용해 접점을 낼 뿐이다.

규칙 종류
  annotation       멤버에 붙은 어노테이션 → API 제공/호출 (Spring, NestJS, FastAPI, Retrofit, JAX-RS …)
  call             문자열을 인자로 받는 호출 → API 제공/호출, 메시지 발행/구독 (Express, axios, fetch, requests, Kafka …)
  code / text      정규식. code 는 주석·문자열을 지운 코드에, text 는 원문에 적용 → DB 접근 등
  unit-annotation  유닛에 붙은 어노테이션 → 테이블 제공 (JPA @Entity/@Table …)
  unit-text        유닛 원문 정규식 → 테이블 제공 (SQLAlchemy __tablename__ …)
  service          외부·기기 서비스 사용 흔적
  heuristic        이름으로 부르는 내장 휴리스틱 (프레임워크 없이 경로 문자열을 직접 쓰는 클라이언트용)
모든 접점에는 만든 규칙 이름이 남는다.
"""
http_cands = []    # 호출하는 API 후보: [unit, member|None, method, path, rule, 코드 위치]
db_access = []     # DB 접근: [unit, member|None, 'prisma'|'sql'|'name', 테이블 이름, 'r'|'w', rule]
table_ports = []   # 테이블 제공: [unit, 테이블 이름, rule]
other_ports = []   # 그 밖의 접점(메시지 토픽 등): [kind, 'provide'|'consume', key, unit, member|None, rule]
svc_hits = {}      # 서비스 id -> {unit: 횟수}
rule_hits = Counter()
_hosts = '|'.join(re.escape(h) for h in OPTS.get('api_hosts', []))


def _sub(tpl, m):
    return re.sub(r'\$(\d)', lambda g: m.group(int(g.group(1))) or '', tpl)


def _op(spec, m, before=''):
    if isinstance(spec, str):
        return spec
    word = m.group(spec['group'])
    if 'write_prefix' in spec:
        return 'w' if word.startswith(tuple(spec['write_prefix'])) else 'r'
    if 'read' in spec:
        return 'r' if word in spec['read'] and not (spec.get('unless_before') and spec['unless_before'] in before) else 'w'
    return 'r'


def _ann_values(an, spec):
    """어노테이션에서 경로 값들을 꺼낸다. spec: 'pos0' 또는 이름 인자 목록."""
    if spec in (None, 'pos0'):
        vals = an['pos'][:] or [v for k in ('value', 'path') for v in an['kw'].get(k, [])]
        return vals
    return [v for k in spec for v in an['kw'].get(k, [])] or an['pos'][:]


def _join(prefix, path):
    return '/' + '/'.join(x for x in (prefix.strip('/'), path.strip('/')) if x)


def rule_annotation(R, rid, i, u, fi, items):
    names, rx = R.get('names', {}), re.compile(R['match']) if R.get('match') else None
    prefix, P = '', R.get('prefix')
    if P:
        hit = [an for an in u.get('anns', []) if an['name'].split('.')[-1] in P['names']]
        if not hit and P.get('required'):
            return
        if hit:
            prefix = (_ann_values(hit[0], P.get('path')) or [''])[0]
            if R.get('unit_kind'):
                u['kind'] = R['unit_kind']
    if R.get('prefix_call'):       # router = APIRouter(prefix="/payments") 처럼 호출 인자로 주어지는 접두 경로
        C = R['prefix_call']
        for sc in u.get('scalls', []):
            if sc['chain'] and sc['kw'] == C['kw'] and re.search(C['match'], sc['chain']):
                prefix = sc['lit']
    for mi, m in enumerate(u['members']):
        for an in m.get('anns', []):
            short, method = an['name'].split('.')[-1], None
            if short in names:
                method = names[short]
            elif rx:
                mm = rx.search(an['name'])
                method = _sub(R.get('method', '$1'), mm).upper() if mm else None
            if not method:
                continue
            methods = [method]
            if method == '*':      # 메서드가 인자로 주어지는 꼴: methods=["POST"], method = RequestMethod.POST
                named = [v.upper() for k in ('methods', 'method') for v in an['kw'].get(k, [])]
                mm = re.findall(r'\b(GET|POST|PUT|PATCH|DELETE)\b', an['raw'])
                methods = named or mm or ['ALL']
            paths = _ann_values(an, R.get('path'))
            if R.get('path_from'):
                paths = [v for o in m['anns'] if o['name'].split('.')[-1] in R['path_from'] for v in _ann_values(o, None)]
            if R.get('require_path') and not paths:
                continue
            for method in methods:
                for path in paths or ['']:
                    rule_hits[rid] += 1
                    if R.get('kind', 'http') != 'http':
                        other_ports.append([R['kind'], R['role'], path, i, mi, rid])
                        continue
                    full = _join(prefix, path)
                    if R['role'] == 'provide':
                        routes.append(dict(method=method, path=full, unit=i, member=mi, rule=rid))
                        m.setdefault('route', f'{method} {full}')
                    else:
                        items.append((an['a'], 'http', [i, mi, method, full, rid]))
                    if R.get('unit_kind'):
                        u['kind'] = R['unit_kind']


def rule_call(R, rid, i, u, fi, items):
    rx = re.compile(R['match'])
    for sc in u.get('scalls', []):
        if not sc['chain'] or (sc['argi'] or 0) != R.get('arg', 0) or (sc['kw'] and not R.get('allow_kw')):
            continue
        mm = rx.search(sc['chain'])
        if not mm or (R.get('lit') and not re.search(R['lit'], sc['lit'])):
            continue
        key = template_to_path(sc['lit'], 'js')
        if R.get('kind', 'http') != 'http':
            other_ports.append([R['kind'], R['role'], key, i, sc['mi'], rid])
            rule_hits[rid] += 1
            continue
        method = R['method_map'].get(mm.group(R.get('method_group', 1))) if R.get('method_map') else _sub(R['method'], mm).upper() if R.get('method') else None
        if not method and R.get('method_near'):
            near = re.search(R['method_near'], fi['src'][sc['end']:sc['end'] + 220].split(');')[0])
            method = near.group(1).upper() if near else None
        method = method or R.get('default_method', 'GET')
        if R['role'] == 'provide':
            routes.append(dict(method=method, path=_join('', key), unit=i, member=sc['mi'], rule=rid))
            if sc['mi'] is not None:
                u['members'][sc['mi']].setdefault('route', f"{method} {_join('', key)}")
            if R.get('unit_kind'):
                u['kind'] = R['unit_kind']
        else:
            items.append((sc['q'], 'http', [i, sc['mi'], method, key, rid]))
        rule_hits[rid] += 1


def rule_regex(R, rid, i, u, fi, items):
    rx, which = re.compile(R['regex']), 'code' if R['type'] == 'code' else 'src'
    for a, b in u['ranges']:
        for m in rx.finditer(fi[which][a:b + 1]):
            pos = a + m.start()
            mi = member_at(u, pos)
            key = _sub(R['key'], m)
            if R.get('kind', 'db') == 'db':
                items.append((pos, 'db', [i, mi, R.get('style', 'name'), key, _op(R.get('op', 'r'), m, fi['src'][max(a, pos - 8):pos]), rid]))
            else:
                other_ports.append([R['kind'], R['role'], key, i, mi, rid])
            rule_hits[rid] += 1


def rule_unit(R, rid, i, u, fi, items):
    if R['type'] == 'unit-annotation':
        hit = [an for an in u.get('anns', []) if an['name'].split('.')[-1] in R['names']]
        if not hit:
            return
        named = [v for an in u['anns'] if an['name'].split('.')[-1] in R.get('name_from', []) for v in an['kw'].get('name', []) + an['pos']]
        table_ports.append([i, named[0] if named else u['name'], rid])
    else:
        m = re.search(R['regex'], unit_text(u, 'src'))
        if not m:
            return
        table_ports.append([i, m.group(1), rid])
    if R.get('unit_kind'):
        u['kind'] = R['unit_kind']
    rule_hits[rid] += 1


def rule_service(R, rid, i, u, fi, items):
    lang = fi['lang']
    key = 'ts' if lang == 'js' else lang
    n = 0
    if key in R.get('code', {}):
        n += len(re.findall(R['code'][key], unit_text(u)))
    if R.get('text') and (not R.get('text_langs') or key in R['text_langs'] or lang in R['text_langs']):
        n += len(re.findall(R['text'], unit_text(u, 'src')))
    if n:
        svc_hits.setdefault(R['id'], {})[i] = n
        rule_hits[rid] += 1


# ── 내장 휴리스틱: 프레임워크 없이 경로 문자열을 직접 쓰는 클라이언트 ──
_SWIFT_PATH = re.compile(r'(?:\b(get|post|request|put|patch|delete)\(\s*|(appendingPathComponent)\(\s*)"([a-z][A-Za-z0-9\-/:._]*)"')
_KOTLIN_URL = re.compile(r'(?:\b(fetch|get|post|request)\(\s*"([a-z][A-Za-z0-9\-/]*)"|"(?:' + (r'https://(?:' + _hosts + r')|' if _hosts else '')
                         + r'\$\{?[A-Za-z_.]*BASE_URL\}?)(/[A-Za-z0-9\-/_:.{}$]*))')


def heur_path_arg(R, rid, i, u, fi, items):
    """get("orders/recent") · appendingPathComponent("x") 처럼 경로 조각을 인자로 넘기는 호출."""
    src = fi['src']
    for a, b in u['ranges']:
        seg = src[a:b + 1]
        for m in _SWIFT_PATH.finditer(seg):
            pos = a + m.start()
            if in_comment(fi, pos):
                continue
            after = seg[m.end():m.end() + 420]
            stmt, fn, method = after.split('\n')[0], m.group(1), None
            mm = re.search(r'method:\s*"(\w+)"', stmt) or (re.search(r'httpMethod\s*=\s*"(\w+)"', after) if m.group(2) else None)
            if mm:
                method = mm.group(1).upper()
            elif fn in ('post', 'put', 'patch', 'delete'):
                method = fn.upper()
            elif fn == 'request' and 'body:' in stmt:
                method = 'POST'
            elif fn in ('get', 'request'):
                method = 'GET'
            items.append((pos, 'http', [i, member_at(u, pos), method, m.group(3), rid]))


def heur_base_url(R, rid, i, u, fi, items):
    """"$BASE_URL/path" · "https://알려진-호스트/path" 처럼 기준 주소에 경로를 붙인 문자열."""
    src = fi['src']
    for a, b in u['ranges']:
        seg = src[a:b + 1]
        for m in _KOTLIN_URL.finditer(seg):
            pos = a + m.start()
            if in_comment(fi, pos):
                continue
            path = m.group(2) or template_to_path(m.group(3), 'kotlin')
            mm = re.search(r'\.(post|delete|put|patch)\(', seg[m.end():m.end() + 260])
            items.append((pos, 'http', [i, member_at(u, pos), mm.group(1).upper() if mm else 'GET', path, rid]))


def heur_slash_literal(R, rid, i, u, fi, items):
    """'/api/orders' 처럼 슬래시로 시작하는 문자열 전부를 후보로 본다. 실제 라우트와 맞는 것만 연결 단계에서 남는다."""
    src = fi['src']
    for a, b in u['ranges']:
        for q, end in fi['strings']:
            if not (a <= q <= b) or src[q] not in '\'"`':
                continue
            raw = src[q + 1:end - 1]
            if not re.match(r'/[a-z]', raw) or '.html' in raw or ' ' in raw.split('${')[0]:
                continue
            path = template_to_path(raw, 'js')
            near = src[end:end + 220]
            if path.endswith('/') and near.lstrip().startswith('+'):
                path += ':p'
            mm = re.search(r'method:\s*[\'"](\w+)[\'"]', near.split(');')[0])
            items.append((q, 'http', [i, member_at(u, q), mm.group(1).upper() if mm else 'GET', path, rid]))


HEURISTICS = {'path-arg': heur_path_arg, 'base-url': heur_base_url, 'slash-literal': heur_slash_literal}
RULE_FN = {'annotation': rule_annotation, 'call': rule_call, 'code': rule_regex, 'text': rule_regex,
           'unit-annotation': rule_unit, 'unit-text': rule_unit, 'service': rule_service}

PACKS = {p['id']: p for p in OPTS.get('rules', [])}
for i, u in enumerate(units):
    if not u['file'] or is_schema(u):
        continue
    fi = file_info[u['file']]
    lang = fi['lang']
    extra, off = SYS[u['sys']].get('rules') or [], SYS[u['sys']].get('rules_off') or []
    items = []
    for pid, pack in PACKS.items():
        if lang not in pack.get('langs', [lang]):
            continue
        if not ((pack.get('auto', True) and pid not in off) or pid in extra):
            continue
        for k, R in enumerate(pack['rules']):
            rid = f"{pid}#{R.get('id', k)}"
            if R['type'] == 'heuristic':
                HEURISTICS[R['name']](R, rid, i, u, fi, items)
            else:
                RULE_FN[R['type']](R, rid, i, u, fi, items)
    # 한 유닛 안에서는 코드에 적힌 순서대로 내보낸다
    for pos, kind, rec in sorted(items, key=lambda t: (t[0], t[2][4].startswith('client-') if t[1] == 'http' else False)):
        if kind == 'http':
            http_cands.append(rec + [pos])
            rule_hits[rec[4]] += 1
        else:
            db_access.append(rec)
