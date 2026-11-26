"""분석 엔진 · 규칙 팩 실행. 프레임워크 지식은 rules/*.json 에 데이터로 있고, 여기는 그것을 사실(facts)에 적용해 접점을 낼 뿐이다.

규칙 종류
  annotation       멤버에 붙은 어노테이션 → API 제공/호출 (Spring, NestJS, FastAPI, Retrofit, JAX-RS …)
  call             문자열을 인자로 받는 호출 → API 제공/호출, 메시지 발행/구독 (Express, axios, fetch, requests, Kafka …)
                   lit_method: "POST /x" 처럼 문자열에 메서드가 함께 적힌 꼴 · handler: 마지막 인자로 넘긴 처리 메서드에 라우트를 붙인다
  code / text      정규식. code 는 주석·문자열을 지운 코드에, text 는 원문에 적용 → DB 접근 등
  unit-annotation  유닛에 붙은 어노테이션 → 테이블 제공 (JPA @Entity/@Table …)
  unit-text        유닛 원문 정규식 → 테이블 제공 (SQLAlchemy __tablename__ …)
  service          외부·기기 서비스 사용 흔적
  status           응답 상태 코드를 내려주는 자리 (http.Error, HTTPException, res.status …). 정규식의 첫 묶음이 코드 숫자나 이름이다.
                   map: 묶음 값 → 코드 표 · unit: 타입 선언에 붙은 것이면 그 타입(예외 클래스)의 코드로 삼는다
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


# ── 응답 상태 코드: 이름 ↔ 숫자 ──
STATUS_NAMES = {
    'OK': 200, 'CREATED': 201, 'ACCEPTED': 202, 'NOCONTENT': 204, 'MOVEDPERMANENTLY': 301, 'FOUND': 302, 'SEEOTHER': 303, 'NOTMODIFIED': 304,
    'TEMPORARYREDIRECT': 307, 'PERMANENTREDIRECT': 308, 'BADREQUEST': 400, 'UNAUTHORIZED': 401, 'PAYMENTREQUIRED': 402, 'FORBIDDEN': 403,
    'NOTFOUND': 404, 'METHODNOTALLOWED': 405, 'NOTACCEPTABLE': 406, 'REQUESTTIMEOUT': 408, 'CONFLICT': 409, 'GONE': 410, 'PRECONDITIONFAILED': 412,
    'PAYLOADTOOLARGE': 413, 'REQUESTENTITYTOOLARGE': 413, 'UNSUPPORTEDMEDIATYPE': 415, 'IMATEAPOT': 418, 'UNPROCESSABLEENTITY': 422,
    'UNPROCESSABLECONTENT': 422, 'LOCKED': 423, 'TOOMANYREQUESTS': 429, 'INTERNALSERVERERROR': 500, 'INTERNALSERVER': 500, 'NOTIMPLEMENTED': 501,
    'BADGATEWAY': 502, 'SERVICEUNAVAILABLE': 503, 'GATEWAYTIMEOUT': 504, 'HTTPVERSIONNOTSUPPORTED': 505,
}
# 이름으로 어림할 때 쓰는 낱말. 위에서부터 먼저 맞는 것을 쓴다.
STATUS_GUESS = [(r'NotFound|NoSuch|Missing', 404), (r'Unauthori[sz]ed|Unauthenticated|InvalidToken|TokenExpired|SessionExpired', 401),
                (r'Forbidden|AccessDenied|PermissionDenied|NotAllowed', 403), (r'Conflict|Duplicate|AlreadyExists?|AlreadyUsed', 409),
                (r'TooManyRequests|RateLimit|Throttl', 429), (r'Unprocessable', 422), (r'BadRequest|Invalid|Validation|Illegal(Argument|State)?|Malformed', 400),
                (r'Timeout|TimedOut', 504), (r'Unavailable|Overloaded', 503)]


def status_of(name):
    """HttpStatus.NOT_FOUND · StatusNotFound · NotFoundException · HTTP_404_NOT_FOUND · 404 → 404. 모르면 None."""
    name = (name or '').split('.')[-1]
    m = re.fullmatch(r'(?:HTTP_|SC_|Status)?(\d{3})(?:_\w+)?', name)
    if m:
        return int(m.group(1)) if 100 <= int(m.group(1)) <= 599 else None
    key = re.sub(r'^(HTTP_|SC_|Status|Http)', '', name)
    key = re.sub(r'(Exception|Error)$', '', key)
    return STATUS_NAMES.get(re.sub(r'[^A-Za-z]', '', key).upper())


def status_guess(name):
    for pat, code in STATUS_GUESS:
        if re.search(pat, name):
            return code
    return None


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
    prefix, P, hit = '', R.get('prefix'), []
    if P:
        hit = [an for an in u.get('anns', []) if an['name'].split('.')[-1] in P['names']]
        if not hit and P.get('required'):
            return
        if hit:
            prefix = (_ann_values(hit[0], P.get('path')) or [''])[0]
            hit[0]['_used'] = 1
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
            if not method or (R.get('unread_only') and an.get('_used')):       # unread_only: 다른 규칙이 이미 읽은 자리는 건드리지 않는다(화면에서 만든 규칙)
                continue
            methods = [method]
            if method == '*':      # 메서드가 인자로 주어지는 꼴: methods=["POST"], method = RequestMethod.POST
                named = [v.upper() for k in ('methods', 'method') for v in an['kw'].get(k, [])]
                mm = re.findall(r'\b(GET|POST|PUT|PATCH|DELETE)\b', an['raw'])
                methods = named or mm or ['ALL']
            paths = _ann_values(an, R.get('path'))
            if R.get('path_from'):
                paths = [v for o in m['anns'] if o['name'].split('.')[-1] in R['path_from'] for v in _ann_values(o, None)]
                for o in m['anns']:
                    if o['name'].split('.')[-1] in R['path_from']:
                        o['_used'] = 1
            if R.get('require_path') and not paths:
                continue
            if R.get('require') == 'path-or-prefix' and not paths and not hit:       # 경로가 메서드에도 클래스에도 없으면 이 규칙이 읽을 것이 아니다 (같은 이름의 어노테이션을 쓰는 다른 라이브러리)
                continue
            an['_used'] = 1
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


_HANDLER = re.compile(r'^((?:[A-Za-z_$][\w$]*\s*\.\s*)*)([A-Za-z_$][\w$]*)$')


def handler_ref(i, u, fi, sc):
    """라우트를 등록하는 곳과 처리하는 곳이 다를 때(mux.HandleFunc("/x", server.Reserve), router.get('/x', auth, ctrl.list)):
    호출의 마지막 인자가 이름이면 그 이름의 메서드를 가진 유닛을 찾아 (유닛, 멤버)를 돌려준다. 못 찾거나 여럿이면 None."""
    code, j, depth, last = fi['code'], sc['end'], 0, sc['end']
    while j < len(code) and j < sc['end'] + 400:
        c = code[j]
        if c in '([{':
            depth += 1
        elif c in ')]}':
            if depth == 0:
                break
            depth -= 1
        elif c == ',' and depth == 0:
            last = j + 1
        j += 1
    m = _HANDLER.match(code[last:j].strip()) if j < len(code) and code[j] == ')' and last > sc['end'] else None
    if not m:
        return None
    recv, name = re.sub(r'[\s.]+$', '', m.group(1)).split('.')[-1].strip(), m.group(2)
    if not recv or recv in ('this', 'self'):
        if name in member_names[i]:
            return i, member_names[i][name][0]
    pool = set()
    for t in u['refs']:
        if isinstance(t, int):
            pool.update(family_of(t))
    if not recv:
        pool.update(kt_funcs.get(name, ()))
    having = sorted((t, member_names[t][name][0]) for t in pool if t != i and name in member_names[t])
    if len(having) > 1 and recv:
        r = norm(recv)
        having = [h for h in having if r and (r in norm(units[h[0]]['name']) or norm(units[h[0]]['name'].split('+')[0]) in r)] or having
    return having[0] if len(having) == 1 else None


# ── 기준 주소 상수: BASE_URL = "https://api.example.com" 처럼 이름에 주소를 담아 둔 것 ──
_URL_CONST = re.compile(r'(?<![\w.])([A-Za-z_]\w*)\s*(?::\s*[\w.<>?\[\] ]+?)?\s*[:=]\s*(?:URL\(\s*string:\s*)?[frb]?["\'`](https?://[A-Za-z0-9.\-]+(?::\d{2,5})?)(/[^"\'`\s{}$]*)?["\'`]')
URL_CONSTS = {}        # 이름공간 → {이름: {주소}}
_const_files = set()
for _u in units:
    if _u['file'] and _u['file'] not in _const_files:
        _const_files.add(_u['file'])
        for _m in _URL_CONST.finditer(file_info[_u['file']]['src']):
            URL_CONSTS.setdefault(NS[_u['sys']], {}).setdefault(_m.group(1), set()).add((_m.group(2) + (_m.group(3) or '')).rstrip('/'))
_LEAD = re.compile(r'^(?:\$\{([^{}]*)\}|\{([^{}]*)\}|\$([A-Za-z_]\w*)|\\\(([^()]*(?:\([^()]*\))?[^()]*)\))(?=/)')       # 맨 앞의 ${…} · {…} · $이름 · \\(…). 안에는 이름뿐 아니라 base() 같은 식도 온다


def const_url(name, ns):
    urls = URL_CONSTS.get(ns, {}).get(name.split('.')[-1], ())
    return next(iter(urls)) if len(urls) == 1 else ''


def lead_base(lit, ns):
    """문자열 맨 앞이 기준 주소 변수(`${API}/x` · `{ERP_BASE}/x` · `$BASE_URL/x`)이면, 그 이름의 주소 상수가 하나일 때 주소로 바꾸고 아니면 떼어 낸다.
    `${base()}/x` 처럼 환경에 따라 주소를 고르는 식이 와도 떼어 내고 경로를 살린다(개발·운영 주소가 따로 있는 코드에서 흔한 꼴).
    주소가 붙으면 어느 서버로 가는 호출인지 연결 단계에서 가릴 수 있다."""
    m = _LEAD.match(lit)
    if not m:
        return lit
    name = re.match(r'\s*((?:[A-Za-z_]\w*\.)*[A-Za-z_]\w*)\s*$', next((g for g in m.groups() if g), ''))      # 이름 하나일 때만 상수로 본다. base() 같은 식이면 떼어 내기만 한다
    return (const_url(name.group(1), ns) if name else '') + lit[m.end():]


def rule_call(R, rid, i, u, fi, items):
    rx = re.compile(R['match'])
    http = R.get('kind', 'http') == 'http'
    for sc in u.get('scalls', []):
        if not sc['chain'] or (sc['argi'] or 0) != R.get('arg', 0) or (sc['kw'] and not R.get('allow_kw')):
            continue
        mm = rx.search(sc['chain'])
        lit = lead_base(sc['lit'], NS[u['sys']]) if http and mm else sc['lit']
        if not mm or (R.get('lit') and not re.search(R['lit'], lit)) or (R.get('unread_only') and sc.get('_used')):
            continue
        if sc.get('wrap') and not R.get('allow_wrap', True):
            continue
        lit_method = None
        if R.get('lit_method'):          # "POST /orders" 처럼 문자열 안에 메서드가 함께 적힌 꼴
            lm = re.match(R['lit_method'], lit)
            if lm:
                lit_method, lit = lm.group(1).upper(), lm.group(2)
        key = template_to_path(printf_to_path(lit) if sc.get('wrap') else lit, 'js')
        if R.get('kind', 'http') != 'http':
            other_ports.append([R['kind'], R['role'], key, i, sc['mi'], rid])
            rule_hits[rid] += 1
            sc['_used'] = 1
            continue
        method = R['method_map'].get(mm.group(R.get('method_group', 1))) if R.get('method_map') else _sub(R['method'], mm).upper() if R.get('method') else None
        if not method and R.get('method_near'):
            near = re.search(R['method_near'], fi['src'][sc['end']:sc['end'] + 220].split(');')[0])
            method = near.group(1).upper() if near else None
        method = lit_method or method or R.get('default_method', 'GET')
        sc['_used'] = 1
        if R['role'] == 'provide':
            tu, tm = (handler_ref(i, u, fi, sc) or (i, sc['mi'])) if R.get('handler') else (i, sc['mi'])
            routes.append(dict(method=method, path=_join('', key), unit=tu, member=tm, rule=rid))
            if tm is not None:
                units[tu]['members'][tm].setdefault('route', f"{method} {_join('', key)}")
            if R.get('unit_kind'):
                units[tu]['kind'] = R['unit_kind']
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


def rule_status(R, rid, i, u, fi, items):
    """상태 코드가 적힌 자리를 멤버에 모아 둔다. 문구 붙이기와 예외 클래스 풀기는 engine/status.py 가 한다."""
    rx, which = re.compile(R['regex']), 'src' if R.get('on') == 'src' else 'code'
    for a, b in u['ranges']:
        for m in rx.finditer(fi[which][a:b + 1]):
            raw = next((g for g in m.groups() if g), '')
            code = R['map'].get(raw) if R.get('map') else status_of(raw)
            if not code:
                continue
            pos = a + m.start()
            mi = member_at(u, pos)
            if R.get('unit'):
                if mi is None:
                    u['status'] = code
                continue
            if mi is not None:
                u['members'][mi].setdefault('_st', []).append((code, pos, a + m.end(), 0))
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
_KOTLIN_URL = re.compile(r'(?:\b(?P<fn>fetch|get|post|request)\(\s*"(?P<arg>[a-z][A-Za-z0-9\-/]*)"|"(?:' + (r'(?P<host>https?://(?:' + _hosts + r'))|' if _hosts else '')
                         + r'\$\{?[A-Za-z_.]*BASE_URL\}?)(?P<path>/[A-Za-z0-9\-/_:.{}$]*))')


_CLIENTISH = re.compile(r'(?i)(api|client|http|rest|remote|network|service|server|request|retrofit|session|gateway)')
_MIME = re.compile(r'^(application|text|image|audio|video|multipart|font|message|model)/')
_REL_PATH = re.compile(r'^[A-Za-z][\w\-]*(?:/[\w\-.{}$()\\:]+)+/?$', re.ASCII)  # api/orders/{id} · am/menu/list.do 처럼 슬래시 없이 시작하는 경로 (영문만)
_URL_PATH = re.compile(r'^https?://[A-Za-z0-9.\-]+(?::\d{2,5})?/[^\s]+$')


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
            if m.group('arg') and '/' not in m.group('arg'):
                # get("name") 처럼 조각 하나짜리 인자: 맵·JSON 에서 값을 꺼내는 호출이 대부분이다. 받는 쪽 이름이 통신 객체로 보일 때만 호출로 친다
                recv = re.search(r'([A-Za-z_]\w*)\s*(?:\?|!!)?\.\s*$', seg[max(0, m.start() - 60):m.start()])
                if recv and not _CLIENTISH.search(recv.group(1)):
                    continue
            path = m.group('arg') or template_to_path(m.group('path'), 'kotlin')
            if m.groupdict().get('host'):          # 주소가 적혀 있으면 함께 넘긴다. 어느 서버로 가는 호출인지 연결 단계에서 가린다
                path = m.group('host') + path
            elif m.group('path'):
                lead = re.match(r'"\$\{?([A-Za-z_.]*BASE_URL)', m.group(0))
                path = (const_url(lead.group(1), NS[u['sys']]) if lead else '') + path
            mm = re.search(r'\.(post|delete|put|patch)\(', seg[m.end():m.end() + 260])
            items.append((pos, 'http', [i, member_at(u, pos), mm.group(1).upper() if mm else 'GET', path, rid]))


def heur_slash_literal(R, rid, i, u, fi, items):
    """'/api/orders' 처럼 슬래시로 시작하는 문자열 전부를 후보로 본다. 실제 라우트와 맞는 것만 연결 단계에서 남는다.
    모바일 앱(Swift·Kotlin·Java)은 경로를 상수에 모아 두거나 주소에 더해 쓰는 일이 많아, 통신 라이브러리의 호출 꼴을 몰라도 이 그물로 잡는다.
    그래서 그쪽에서는 슬래시 없이 시작하는 경로("am/menu/list.do")와 주소가 통째로 적힌 문자열("https://host/x/y")도 함께 건진다."""
    src, app = fi['src'], fi['lang'] in ('swift', 'kotlin', 'java', 'dart')
    spans = [an['span'] for m in u['members'] for an in m.get('anns', [])] + [an['span'] for an in u.get('anns', [])]      # 어노테이션 인자 안의 문자열은 어노테이션 규칙이 읽는다(@POST("/x") 를 GET 호출로 한 번 더 세지 않게)
    for a, b in u['ranges']:
        for q, end in fi['strings']:
            if not (a <= q <= b) or src[q] not in '\'"`' or any(lo <= q < hi for lo, hi in spans):
                continue
            raw = src[q + 1:end - 1]
            if '.html' in raw or ' ' in raw.split('${')[0]:
                continue
            if not re.match(r'/[A-Za-z]' if app else r'/[a-z]', raw) and not (app and (_URL_PATH.match(raw) or (_REL_PATH.match(raw) and not _MIME.match(raw)))):
                continue
            path = template_to_path(raw, 'kotlin' if fi['lang'] in ('kotlin', 'java', 'dart') else 'js')
            near = src[end:end + 220]
            if path.endswith('/') and near.lstrip().startswith('+'):
                path += ':p'
            mm = re.search(r'method:\s*[\'"](\w+)[\'"]', near.split(');')[0])
            items.append((q, 'http', [i, member_at(u, q), mm.group(1).upper() if mm else 'GET', path, rid]))


HEURISTICS = {'path-arg': heur_path_arg, 'base-url': heur_base_url, 'slash-literal': heur_slash_literal}
RULE_FN = {'annotation': rule_annotation, 'call': rule_call, 'code': rule_regex, 'text': rule_regex,
           'unit-annotation': rule_unit, 'unit-text': rule_unit, 'service': rule_service, 'status': rule_status}

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
            if (R.get('langs') and lang not in R['langs']) or (R.get('systems') and u['sys'] not in R['systems']):
                continue
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


# ───────────────────────────── 읽지 못한 꼴 ─────────────────────────────
# 경로처럼 생긴 문자열이 어노테이션 인자나 호출 인자로 놓여 있는데 어느 규칙도 읽지 않은 자리를 꼴별로 센다.
# 규칙이 없는 프레임워크를 쓴 코드가 어디에 어떤 모양으로 있는지 드러내려는 것이고, 화면에서 그 꼴을 골라 규칙으로 만들 수 있다.
_PATHY = re.compile(r'^(?:/[A-Za-z_{:][\w\-{}:.$*]*(?:/[\w\-{}:.$*]*)*|[A-Za-z_{][\w\-{}.$*]*(?:/[\w\-{}:.$*]+)+)/?$', re.ASCII)     # 영문 경로만. 첫 조각에 ':' 가 있으면(Content-Type:… · file:/… · txt:…) 경로가 아니다
_NOT_API = re.compile(r'\.(jsp|html?|png|jpe?g|gif|svg|ico|css|js|xml|properties|txt|ya?ml|sql|jar|class|ttf|woff2?|mp[34]|pdf|xlsx?|zip|wsdl|xsd|tmp|log|dat|db|so|dll|exe)$', re.I)
_NOISE_CALL = re.compile(r'(?i)^(equals\w*|contains|startsWith|endsWith|replace\w*|split|matches|indexOf|lastIndexOf|format|append|compile|print\w*|log|debug|info|warn|error|trace|[deiwv]|'
                         r'File|Paths?|getResource\w*|FileInputStream|FileReader|FileWriter|readFile\w*|require|loadLibrary|ModelAndView|forward|sendRedirect|getRequestDispatcher|include|'
                         r'Intent|valueOf|join|trim|asList|assert\w*|expect|describe|it|test|getString|getProperty|getenv|getParameter|getHeader|getAttribute|setAttribute|addObject|addAttribute|'
                         r'import|from|package|use|if|elif|for|while|switch|case|when|return|catch|throw|new|typeof|await|yield|and|or|not|in|is)$')


def _pathy(v):
    return 2 <= len(v) <= 120 and _PATHY.match(v) and not _MIME.match(v) and not _NOT_API.search(v)


unread = {}        # (시스템, 'ann'|'call', 이름, 인자 번호) → [개수, 문자열 표본, {함께 붙은 어노테이션: 횟수}]


def _unread(sys_id, kind, name, argi, value, beside=()):
    t = unread.setdefault((sys_id, kind, name, argi), [0, [], {}])
    t[0] += 1
    if value not in t[1] and len(t[1]) < 40:        # 표본: 연결 단계가 '이미 다른 방법으로 이어진 꼴인가'를 가릴 때 쓴다
        t[1].append(value)
    for b in set(beside):                           # 같은 선언에 함께 붙은 어노테이션 이름: 어떤 방식인지 짐작하는 단서 (@POST 가 같이 있는가 등)
        t[2][b] = t[2].get(b, 0) + 1


unread_sites = []   # 읽지 못한 자리 하나하나: [종류, 이름, 인자 번호, 유닛, 멤버|None, 문자열, 클래스에 붙은 같은 어노테이션의 경로(접두)]
_vals = lambda an: an['pos'] + [x for k in ('value', 'path', 'url', 'uri') for x in an['kw'].get(k, [])]
for i, u in enumerate(units):
    if not u['file'] or is_schema(u):
        continue
    all_anns = [x for m in u['members'] for x in m.get('anns', [])] + list(u.get('anns', []))
    in_ann = lambda q: any(an['span'][0] <= q < an['span'][1] for an in all_anns)       # 어노테이션 인자 안의 문자열은 어노테이션 꼴로만 센다
    for mi, group in [(k, m.get('anns', [])) for k, m in enumerate(u['members'])] + [(None, u.get('anns', []))]:
        for an in group:
            if an.get('_used'):
                continue
            short = an['name'].split('.')[-1]
            for v in _vals(an):
                if _pathy(v):
                    _unread(u['sys'], 'ann', short, 0, v, [o['name'].split('.')[-1] for o in group if o is not an])
                    if mi is not None:
                        above = next((x for o in u.get('anns', []) if o['name'].split('.')[-1] == short for x in _vals(o)), '')
                        unread_sites.append(['ann', short, 0, i, mi, v, above])
    for sc in u.get('scalls', []):
        if sc.get('_used') or not sc['chain'] or sc['kw'] or not _pathy(sc['lit']) or in_ann(sc['q']):
            continue
        parts = [p for p in re.split(r'\s*\.\s*', sc['chain']) if p]
        if not parts or _NOISE_CALL.match(parts[-1]) or not re.match(r'[A-Za-z_]\w*$', parts[-1]):
            continue
        _unread(u['sys'], 'call', '.'.join(parts[-2:]), sc['argi'] or 0, sc['lit'])
        unread_sites.append(['call', '.'.join(parts[-2:]), sc['argi'] or 0, i, sc['mi'], sc['lit'], ''])
