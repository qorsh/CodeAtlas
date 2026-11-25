"""언어 어댑터 · Kotlin. class/interface/object 와 최상위 함수(파일 유닛)를 유닛·멤버로 만든다."""
# ───────────────────────────── Kotlin ─────────────────────────────
KT_TYPE = re.compile(r'\b(class|interface|object)\s+([A-Za-z_]\w*)')
KT_FUN = re.compile(r'\bfun\s+(?:<[^>]*>\s*)?(?:[A-Za-z_][\w<>?,. ]*\.)?([A-Za-z_]\w*)\s*\(')


def classify_kotlin(header):
    cands = []
    for m in KT_TYPE.finditer(header):
        cands.append((m.start(), 'type', m.group(2), m.group(1), m.end()))
    for m in re.finditer(r'\bcompanion\s+object\b(?!\s+[A-Za-z_])', header):
        cands.append((m.start(), 'type', 'Companion', 'companion', m.end()))
    for m in KT_FUN.finditer(header):
        cands.append((m.start(), 'func', m.group(1), 'fun', m.end() - 1))
    if not cands:
        return 'other', None, None, None
    pos, kind, name, sub, end = max(cands)
    tail = header[end:]
    if kind == 'func':
        q = close_paren(tail, 0) if tail[:1] == '(' else 0
        rest = tail[q + 1:]
        if '=' in re.sub(r'<[^<>]*>', '', rest) or STMT_BREAK.search(rest):
            return 'other', None, None, None
    else:
        p = tail.find('(')
        rest = tail
        if p >= 0 and not tail[:p].strip():
            rest = tail[close_paren(tail, p) + 1:]
        if STMT_BREAK.search(rest) or re.search(r'(?<![=!<>])=(?!=)', re.sub(r'\([^()]*\)', '', rest)):
            return 'other', None, None, None
    return kind, name, sub, pos


def kotlin_kind(name, header, body, rel, composable):
    if composable or re.search(r'(Screen|Dialog|Dialogs|Sheet|Card|Components|Ui|Theme)$', name):
        return 'view'
    if re.search(r'ViewModel$', name):
        return 'reducer'
    if re.search(r'(Activity|Application)$', name):
        return 'view'
    if re.search(r'(Repository|Database|Preferences|Dao|Store)$', name):
        return 'store'
    if re.search(r'(Fetcher|Client|Ads|Tester)$', name) and '/data/' in rel:
        return 'client'
    if re.search(r'\b(data|enum|sealed|value)\s+class\b|\bsealed\s+interface\b', header) or '/model/' in rel:
        return 'model'
    return 'logic'


def parse_kotlin(sys_id, rel, module):
    fi = load_file(rel, 'kotlin')
    src, code, blocks, lines = fi['src'], fi['code'], fi['blocks'], fi['lines']
    opens = [b['open'] for b in blocks]
    base_file = os.path.splitext(os.path.basename(rel))[0]
    for b in blocks:
        b['kind'], b['name'], b['sub'], b['decl'] = classify_kotlin(code[b['hstart']:b['open']])
    decl_pos = {b['hstart'] + b['decl']: i for i, b in enumerate(blocks) if b['decl'] is not None}
    local, block_unit = {}, {}
    # 본문이 있는 최상위 타입
    for bi, b in enumerate(blocks):
        if b['kind'] == 'type' and b['parent'] < 0:
            hs = lines.start_of(b['hstart'] + b['decl'])
            header = code[hs:b['open']]
            ui = add_unit(sys_id, b['name'], kotlin_kind(b['name'], header, '', '/' + rel, False), rel, [(hs, b['close'])], module, decl=b['sub'])
            units[ui]['doc'] = doc_before(src, fi['comments'], hs, lines)
            m = re.search(r'\)\s*:\s*([^{]+)$|' + re.escape(b['name']) + r'\s*:\s*([^{(]+)', header, re.S)
            if m:
                units[ui]['conforms'] = [re.sub(r'[<(].*', '', x).strip() for x in (m.group(1) or m.group(2)).split(',') if x.strip()][:4]
            local[b['name']] = ui
            block_unit[bi] = ui
    # 본문 없는 최상위 타입 (data class 등)
    for m in KT_TYPE.finditer(code):
        if m.start() in decl_pos or enclosing(blocks, opens, m.start()) != -1:
            continue
        if m.group(2) in local or code[max(0, m.start() - 2):m.start()] == '::':
            continue
        hs = lines.start_of(m.start())
        j = m.end()
        while j < len(code) and code[j] in ' \t':
            j += 1
        if code[j:j + 1] == '<':
            j = code.find('>', j) + 1
        if code[j:j + 1] == '(':
            j = close_paren(code, j) + 1
        end = code.find('\n', j)
        end = len(code) if end < 0 else end
        ui = add_unit(sys_id, m.group(2), kotlin_kind(m.group(2), code[hs:end], '', '/' + rel, False), rel, [(hs, end)], module, decl=m.group(1))
        units[ui]['doc'] = doc_before(src, fi['comments'], hs, lines)
        local[m.group(2)] = ui
    # 함수들
    file_unit = None
    composable_top = False

    def owner_for_top():
        nonlocal file_unit
        if file_unit is None:
            file_unit = local.get(base_file)
            if file_unit is None:
                file_unit = add_unit(sys_id, base_file, 'logic', rel, [], module, decl='file')
                local[base_file] = file_unit
        return file_unit

    for m in KT_FUN.finditer(code):
        pos = m.start()
        bi = decl_pos.get(pos)
        hs = lines.start_of(pos)
        if bi is not None and blocks[bi]['kind'] == 'func':
            enc = blocks[bi]['parent']
            end = blocks[bi]['close']
            sig_end = blocks[bi]['open']
        else:
            enc = enclosing(blocks, opens, pos)
            q = close_paren(code, m.end() - 1)
            end = statement_end(code, q + 1)
            eq = code.find('=', q, end)
            sig_end = eq if eq > 0 else end
        # enc 가 최상위이거나 타입 체인이어야 한다
        chain, k = [], enc
        while k >= 0 and blocks[k]['kind'] == 'type':
            chain.append(k)
            k = blocks[k]['parent']
        if k >= 0:
            continue
        pre = src[max(0, hs - 200):pos]
        if not chain:
            owner = owner_for_top()
            units[owner]['ranges'].append((hs, end))
            if '@Composable' in pre[-120:]:
                composable_top = True
            prefix = ''
        else:
            if chain[-1] not in block_unit:
                continue
            owner = block_unit[chain[-1]]
            prefix = '.'.join(blocks[c]['name'] for c in reversed(chain[:-1]) if blocks[c]['name'] != 'Companion')
        name = (prefix + '.' if prefix else '') + m.group(1)
        units[owner]['members'].append(dict(n=name, s=squeeze(src[hs:sig_end]), a=hs, b=end, k='func', top=not chain,
                                            d=doc_before(src, fi['comments'], hs, lines)))
    if file_unit is not None and units[file_unit]['decl'] == 'file':
        units[file_unit]['kind'] = kotlin_kind(base_file, '', '', '/' + rel, composable_top)
        d = doc_before(src, fi['comments'], min((a for a, _ in units[file_unit]['ranges']), default=0), lines)
        units[file_unit]['doc'] = d
    elif file_unit is not None and composable_top:
        units[file_unit]['kind'] = 'view'
    for bi, ui in block_unit.items():
        for nb in blocks:
            if nb['kind'] == 'type' and nb['parent'] == bi and nb['name'] not in ('Companion',) and nb['name'] not in units[ui]['nested']:
                units[ui]['nested'].append(nb['name'])
    for ui in fi['units']:
        families[(NS[sys_id], units[ui]['name'])].append(ui)



register_language('kotlin', exts=['.kt'], parse=parse_kotlin, toplevel_calls=True)
