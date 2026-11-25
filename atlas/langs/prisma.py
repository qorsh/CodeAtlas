"""스키마 어댑터 · Prisma. model 은 테이블 유닛, 필드는 멤버, 모델 간 참조는 관계(rel)가 된다."""
# ───────────────────────────── Prisma ─────────────────────────────
def parse_prisma(sys_id, rel):
    DB_GROUPS = [tuple(x) for x in (SYS[sys_id].get('db_groups') or [['테이블', '.']])]
    if not any(p == '.' for _, p in DB_GROUPS):
        DB_GROUPS.append(('기타', '.'))
    default_group = DB_GROUPS[-1][0]
    src = read(rel)
    file_info[rel] = dict(src=src, code=src, comments=[], strings=[], lines=Lines(src), units=[], blocks=[], lang='prisma')
    lines = file_info[rel]['lines']
    model_names = set(re.findall(r'^model\s+(\w+)', src, re.M))
    enum_names = set(re.findall(r'^enum\s+(\w+)', src, re.M))
    enum_group = {}
    made = []
    for m in re.finditer(r'^(model|enum)\s+(\w+)\s*\{(.*?)^\}', src, re.M | re.S):
        kind, name, body = m.groups()
        group = next(g for g, pat in DB_GROUPS if re.search(pat, name)) if kind == 'model' else None
        ui = add_unit(sys_id, name, 'table' if kind == 'model' else 'enum', rel, [(m.start(), m.end())], group or default_group, decl=kind)
        made.append(ui)
        off = m.start(3)
        for fm in re.finditer(r'^[ \t]*(\w+)[ \t]+([\w\[\]?]+)([^\n]*)$' if kind == 'model' else r'^[ \t]*(\w+)[ \t]*()()$', body, re.M):
            fname, ftype, rest = fm.groups()
            a = off + fm.start()
            units[ui]['members'].append(dict(n=fname, s=squeeze(f'{fname}  {ftype}  {rest.strip()}' if kind == 'model' else fname, 110), a=a, b=off + fm.end(), k='field', d=''))
            base = ftype.rstrip('?').rstrip(']').rstrip('[')
            if base in model_names and base != name:
                units[ui]['refs'][('db', base)] += 1
            elif base in enum_names:
                units[ui]['refs'][('db', base)] += 1
                enum_group.setdefault(base, group)
        idx = re.findall(r'@@(\w+)\(([^)]*)\)', body)
        units[ui]['nested'] = [f'@@{k}({v})' for k, v in idx]
    for ui in made:
        if units[ui]['kind'] == 'enum' and units[ui]['name'] in enum_group:
            units[ui]['module'] = enum_group[units[ui]['name']]



register_language('prisma', exts=['.prisma'], schema=True, parse=lambda sid, rel, mod: parse_prisma(sid, rel))
