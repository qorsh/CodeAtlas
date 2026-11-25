"""코드 아틀라스 분석 엔진 · 머리말.

묶음(같은 이름공간을 쓰는 시스템들) 하나를 분석할 때마다 engine/ 과 langs/ 의 파일들이 이 순서로 한 이름공간에서 실행된다:
  prelude → text → store → (필요한 언어 어댑터들) → scan → facts → rules → extras → export
실행 전에 atlas.py 가 세 값을 넣어 준다.
  ROOT     소스 루트 경로
  SYSTEMS  이 묶음에 속한 시스템 설정 목록 (atlas.config.json 의 systems 항목)
  OPTS     {'ns': 이름공간, 'api_hosts': [...], 'services': [...], 'strings_xml': [...], 'skip_dirs': [...]}
실행이 끝나면 RESULT 에 유닛, 묶음 안 연결, 바깥 접점(ports)이 담긴다. 소스 본문은 결과에 넣지 않는다.

뽑아내는 것
  · 시스템 → 모듈(폴더) → 유닛(클래스/구조체/파일) → 멤버(메서드/함수)
  · 유닛 간 참조(use), 확장(ext), 메서드 간 호출(calls), 테이블 관계(rel)
  · 접점: 제공하는 API 라우트, 호출하는 경로 후보, DB 접근, 외부·기기 서비스 사용, 주석 속 타 플랫폼 이름
언어별 정식 파서 대신 주석/문자열 제거 + 중괄호 구조 분석을 쓰는 근사 분석이다.
"""
import os
import re
from bisect import bisect_right
from collections import Counter, defaultdict

SKIP_DIRS = {'node_modules', '.git', 'build', '.build', 'dist', '.next', '.spm', 'vendor', '.vinext',
             '.wrangler', '.sites-runtime', 'outputs', '.local', '.openai', '.gradle', '.kotlin',
             'DerivedData', 'Pods', '.claude'} | set(OPTS.get('skip_dirs', []))
SYS = {s['id']: s for s in SYSTEMS}
TEST_ROOTS = [(s['id'], root, ext) for s in SYSTEMS for root, ext in s.get('tests', [])]

# 언어 어댑터 등록부. 어댑터 파일(langs/*.py)이 register_language 로 자기를 등록한다.
#   exts            기본 확장자
#   parse           (시스템 id, 파일 경로, 모듈 이름) → add_unit 으로 유닛·멤버를 만든다
#   syntax / strip  주석·문자열 규칙 이름, 또는 직접 만든 제거 함수
#   imports         import 로 이름을 푸는 언어면 (파일, 별칭 루트) → {이름: 유닛}
#   toplevel_calls  파일 밖에서 함수 이름만으로 부르는 언어 (Kotlin, Python, Go …)
#   extensions      타입을 여러 파일에서 확장하는 언어 (Swift)
#   schema          코드가 아니라 스키마를 읽는 어댑터 (Prisma …)
LANGUAGES = {}


def register_language(name, **spec):
    LANGUAGES[name] = spec


routes = []        # 제공하는 API: dict(method, path, unit, member, rule)
