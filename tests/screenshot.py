#!/usr/bin/env python3
"""실제 브라우저(헤드리스 Chrome)로 지도 화면을 찍는다. 가짜 DOM 시험(viewer-smoke.js)은 "그려지긴 하는가"를 못 본다.

  python3 tests/screenshot.py <작업 공간 폴더> <그림을 둘 폴더>
  예) python3 tests/screenshot.py workspaces/shop /tmp/shots

찍는 장면: 전체 보기, 비정상 응답 목록(실측이 있을 때), API 메서드 하나를 고른 가까운 화면.
작업 공간의 분석 결과가 그대로 찍히므로, 문서에 넣을 그림은 fixtures/shop 으로 만든 작업 공간에서만 찍는다.
"""
import functools
import http.server
import os
import shutil
import subprocess
import sys
import threading

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CHROME = [p for p in ('/Applications/Google Chrome.app/Contents/MacOS/Google Chrome', '/Applications/Chromium.app/Contents/MacOS/Chromium',
                      shutil.which('google-chrome') or '', shutil.which('chromium') or '', shutil.which('chromium-browser') or '') if p and os.path.exists(p)]
# 화면 안의 함수는 밖에서 부를 수 없으므로, 사본에만 시험용 손잡이를 단다
HOOK = "\nwindow.__T = {selectUnit, showAbnormal, cam, invalidate, get UNITS(){return UNITS}, get RT(){return RT}};\n})();\n</script>"
PICK = ("const us = T.UNITS, r = T.RT && T.RT.badUnits[0], u = r || us.find(u => u.m.some(m => m.route)) || us[0];"
        "const mi = r ? r.rt.routes.find(x => x.bad).mi : Math.max(0, u.m.findIndex(m => m.route));")
SHOTS = {
    '1-overview': '',
    '2-abnormal': 'if (T.RT) T.showAbnormal();',
    '3-method': PICK + 'T.selectUnit(u.i, u.m.length ? mi : null, false); T.cam.z = 1.05; T.cam.x = u.x + 330; T.cam.y = u.y + 120; T.invalidate();',
}


def main():
    if len(sys.argv) != 3:
        print(__doc__)
        return 2
    if not CHROME:
        print('Chrome 을 찾지 못했습니다.')
        return 2
    ws, out = sys.argv[1], os.path.abspath(sys.argv[2])
    site = os.path.join(out, '.site')
    os.makedirs(site, exist_ok=True)
    for f in ('atlas-data.json', 'atlas-manifest.json', 'atlas-notes.json', 'atlas-runtime.json'):
        if os.path.exists(os.path.join(ws, f)):
            shutil.copy(os.path.join(ws, f), site)
    body = open(os.path.join(REPO, 'code-atlas.html'), encoding='utf-8').read()
    assert body.count('})();\n</script>') == 1
    head = '<!doctype html><html lang="ko"><head><meta charset="utf-8"><style>body{margin:0}[hidden]{display:none!important}</style></head><body>\n'
    for name, js in SHOTS.items():
        page = body.replace('})();\n</script>', HOOK + '\n<script>setTimeout(() => { const T = window.__T; ' + js + ' }, 900);</script>')
        open(os.path.join(site, name + '.html'), 'w', encoding='utf-8').write(head + page + '</body></html>')
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=site)
    handler.log_message = lambda *a: None
    httpd = http.server.ThreadingHTTPServer(('127.0.0.1', 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    ok = True
    for name in SHOTS:
        png = os.path.join(out, name + '.png')
        if os.path.exists(png):
            os.remove(png)
        subprocess.run([CHROME[0], '--headless=new', '--disable-gpu', '--hide-scrollbars', '--window-size=1440,900', '--force-device-scale-factor=1',
                        '--user-data-dir=' + os.path.join(out, '.profile'), '--virtual-time-budget=5200', '--screenshot=' + png,
                        f'http://127.0.0.1:{httpd.server_address[1]}/{name}.html'], capture_output=True, text=True, timeout=180)
        print(('찍음   ' if os.path.exists(png) else '실패   ') + png)
        ok = ok and os.path.exists(png)
    httpd.shutdown()
    shutil.rmtree(site, ignore_errors=True)
    shutil.rmtree(os.path.join(out, '.profile'), ignore_errors=True)
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
