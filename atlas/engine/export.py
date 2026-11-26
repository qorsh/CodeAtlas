"""분석 엔진 · 결과 묶기. 유닛, 묶음 안 연결, 바깥 접점(ports)을 RESULT 에 담는다."""
# ───────────────────────────── 6) 결과 묶기 ─────────────────────────────
# 번호는 모두 이 묶음 안의 순번이다. 전체 번호로 바꾸는 일은 연결 단계가 한다.
for u in units:
    order = sorted(range(len(u['members'])), key=lambda k: u['members'][k]['a'])
    u['_order'] = order
    u['_remap'] = {old: new for new, old in enumerate(order)}


def _m(i, mi):
    return None if mi is None else units[i]['_remap'][mi]


out_units = []
for i, u in enumerate(units):
    ln = file_info[u['file']]['lines'] if u['file'] else None
    rec = dict(id=u['id'], sys=u['sys'], mod=u['module'], name=u['name'], kind=u['kind'], decl=u.get('decl'),
               file=u['file'], line=ln.line(min(a for a, _ in u['ranges'])) if ln and u['ranges'] else 0, loc=u['loc'])
    if u['doc']:
        rec['doc'] = u['doc']
    if u['conforms']:
        rec['conf'] = u['conforms'][:6]
    if u['nested']:
        rec['nested'] = u['nested'][:14]
    if u['exports']:
        rec['exports'] = u['exports'][:16]
    if u['tests']:
        rec['tests'] = sorted(set(u['tests']))[:8]
    if u.get('x'):
        rec['lit'] = u['x']
    if u.get('status'):
        rec['status'] = u['status']
    mem = []
    for old in u['_order']:
        m = u['members'][old]
        o = dict(n=m['n'], s=m['s'], l=ln.line(m['a']), e=ln.line(m['b']))
        if m['k'] != 'func':
            o['k'] = m['k']
        if m.get('d'):
            o['d'] = m['d'][:220]
        if m.get('route'):
            o['route'] = m['route']
        if m.get('st'):
            o['st'] = m['st']
        if m.get('c'):
            o['c'] = [[t, units[t]['_remap'][tm]] for t, tm in m['c']]
        if m.get('x'):
            o['lit'] = m['x']
        mem.append(o)
    if mem:
        rec['m'] = mem
    out_units.append(rec)

edges = []
for i, u in enumerate(units):
    for t, w in u['refs'].items():
        if isinstance(t, tuple):
            tt = by_name[NS[u['sys']]].get(t[1], [None])[0]
            if tt is not None:
                edges.append(dict(s=i, t=tt, k='rel', w=w))
        elif t != i:
            if u.get('ext_of') == t:
                continue
            edges.append(dict(s=i, t=t, k='use', w=w))
    if u.get('ext_of') is not None:
        edges.append(dict(s=i, t=u['ext_of'], k='ext', w=1))

RESULT = dict(
    units=out_units,
    edges=edges,
    ports=dict(
        routes=[[r['method'], r['path'], r['unit'], _m(r['unit'], r['member']), r.get('rule', '')] for r in routes],
        http=[[i, _m(i, mi), method, path, rule, pos] for i, mi, method, path, rule, pos in http_cands],
        db=[[i, _m(i, mi), kind, name, op, rule] for i, mi, kind, name, op, rule in db_access],
        tables=[[i, name, rule] for i, name, rule in table_ports],
        other=[[kind, role, key, i, _m(i, mi), rule] for kind, role, key, i, mi, rule in other_ports],
        svc={sid: sorted(h.items()) for sid, h in svc_hits.items()},
        mtok={str(i): t for i, t in mirror_toks.items()},
        hosts=host_mentions,
        unread=[[k[0], k[1], k[2], k[3], v[0], v[1]] for k, v in sorted(unread.items(), key=lambda kv: -kv[1][0])][:60],
    ),
    stats=dict(files=sum(1 for k in file_info if k), units=len(units), members=sum(len(u['members']) for u in units), literals=n_lit,
               annotations=n_ann, string_calls=n_scall, status_sites=n_status, rule_hits=dict(rule_hits),
               parsers={s['id']: SITTER_USED.get(s['lang'], 'builtin') for s in SYSTEMS}),
)
