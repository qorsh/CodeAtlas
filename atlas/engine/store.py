"""분석 엔진 · 유닛 저장소. 어댑터는 load_file 로 파일을 읽고 add_unit 으로 유닛을 등록한다."""
# ───────────────────────────── 유닛 저장소 ─────────────────────────────
units = []          # dict
unit_index = {}     # id -> idx
by_name = defaultdict(lambda: defaultdict(list))   # namespace -> name -> [idx]
file_info = {}      # rel -> dict(code, src, lines, comments, strings, units=[idx])
families = defaultdict(list)  # (namespace, base) -> [idx]
NS = {s['id']: OPTS['ns'] for s in SYSTEMS}


def add_unit(sys_id, name, kind, rel, ranges, module, **kw):
    uid = f'{sys_id}:{name}'
    k = 2
    while uid in unit_index:
        uid = f'{sys_id}:{name}~{k}'
        k += 1
    u = dict(id=uid, sys=sys_id, name=name, kind=kind, file=rel, ranges=ranges, module=module, members=[],
             doc='', conforms=[], nested=[], attrs=[], refs=Counter(), exports=[], base=None, tests=[], loc=0)
    u.update(kw)
    unit_index[uid] = len(units)
    units.append(u)
    if rel:
        file_info[rel]['units'].append(len(units) - 1)
    return len(units) - 1


def load_file(rel, lang):
    src = read(rel)
    spec = LANGUAGES.get(lang, {})
    code, comments, strings = spec['strip'](src) if spec.get('strip') else strip_code(src, spec.get('syntax', lang))
    file_info[rel] = dict(src=src, code=code, comments=comments, strings=strings, lines=Lines(src), units=[],
                          blocks=parse_blocks(code), lang=lang)
    return file_info[rel]


def enclosing(blocks, opens, pos):
    """pos 를 감싸는 가장 안쪽 블록 인덱스(없으면 -1)."""
    k = bisect_right(opens, pos) - 1
    while k >= 0:
        b = blocks[k]
        if b['open'] < pos <= b['close']:
            return k
        k = b['parent'] if b['close'] < pos else k - 1 if b['open'] >= pos else b['parent']
    return -1


def func_ancestor(blocks, bi):
    """bi 블록의 조상 중 함수 블록이 있는가."""
    k = blocks[bi]['parent'] if bi >= 0 else -1
    while k >= 0:
        if blocks[k]['kind'] == 'func':
            return True
        k = blocks[k]['parent']
    return False


def type_parent(blocks, bi):
    """bi 블록을 직접 감싸는 블록이 타입이면 그 인덱스, 최상위면 -1, 아니면 None."""
    p = blocks[bi]['parent']
    if p < 0:
        return -1
    return p if blocks[p]['kind'] == 'type' else None




def is_schema(u):
    """스키마 어댑터가 만든 유닛(테이블 등)인가. 코드 참조·문자열 분석에서 뺀다."""
    return bool(u['file']) and LANGUAGES[file_info[u['file']]['lang']].get('schema', False)
