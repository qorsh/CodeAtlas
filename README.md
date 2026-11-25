# CodeAtlas

여러 저장소(모바일 앱, 서버, 웹, DB 스키마)의 코드를 분석해 **한 장의 지도**로 보여 주는 도구다.
문의·수정 요청·오류가 들어왔을 때 "어디를 봐야 하는가"를 빨리 찾는 것이 목적이다.

- 시스템 → 모듈 → 클래스 → 메서드까지 끊김 없이 확대·축소한다(캔버스 2D, 핀치·휠·드래그).
- 클래스끼리의 호출뿐 아니라 **시스템을 넘는 연결**을 자동으로 잇는다: 앱이 부르는 API ↔ 서버 라우트, 코드 ↔ DB 테이블, 메시지 발행 ↔ 구독, 외부 서비스, 플랫폼 간 대응 구현.
- 검색창에 문의를 그대로 적으면 관련 위치가 지도에 켜진다. 화면에 보이는 문구나 오류 코드로도 찾는다.
- 유닛을 누르면 역할, 들어오고 나가는 곳, 메서드, 고쳤을 때 영향받는 범위를 보여 준다.
- 분석 결과에는 **코드 본문을 넣지 않는다.** 이름, 시그니처, 주석, 호출 관계, 코드 속 문자열(비밀 값처럼 보이는 것은 제외)만 담는다.

## 구성

| 경로 | 내용 |
|---|---|
| `code-atlas.html` | 지도 화면. 파일 하나로 된 뷰어이고, 옆에 놓인 `atlas-data.json` 을 읽는다. 어떤 제품의 내용도 들어 있지 않다 |
| `atlas/` | 분석기. `atlas.py`(명령줄), `langs/`(언어 어댑터), `engine/`(공통 엔진), `rules/`(프레임워크 규칙 팩). 자세한 설명은 [atlas/README.md](atlas/README.md) |
| `fixtures/shop/` | 시험용 가짜 프로젝트(Java Spring, Python FastAPI, Go net/http, React, Kotlin Retrofit) |
| `tests/` | 뷰어 연기 시험, 파서 비교, 반출 검사 |
| `requirements-parsers.txt` | 선택 설치(tree-sitter). 없어도 동작한다 |
| `workspaces/` | 분석 대상별 작업 공간. **git 에서 제외된다** (아래 참고) |

분석기는 층으로 나뉘어 있어 한 프로젝트의 모양에 묶이지 않는다. 새 언어는 tree-sitter 명세 한 덩어리(또는 어댑터 파일 하나), 새 프레임워크는 규칙 JSON 하나를 더하면 되고, 연결 단계는 언어를 모른다.

## 빨리 써 보기

git 과 파이썬 3.9 이상만 있으면 된다(맥 기본 파이썬으로 충분하고, 추가 설치는 없다). 저장소에 들어 있는 시험용 프로젝트로 지도를 만들어 본다.

```bash
python3 atlas/atlas.py -w shop init
```

```bash
python3 atlas/atlas.py -w shop add fixtures/shop
```

```bash
python3 atlas/atlas.py -w shop sync
```

```bash
python3 atlas/atlas.py -w shop open
```

마지막 명령이 브라우저를 연다. `sync` 출력의 연결 수가 `'http': 3`, `'db': 2`, `'msg': 1` 이면 정상이다. 이때 Go 로 된 재고 서비스는 "건너뜀"으로 나온다(아래 선택 설치를 하면 함께 분석돼 `'http': 6` 이 된다).

## 내 프로젝트에 적용하기

`-w <이름>` 은 작업 공간 `workspaces/<이름>/` 을 가리킨다. 제품 하나에 작업 공간 하나를 두고, 그 제품을 이루는 저장소를 하나씩 `add` 한다.

```bash
python3 atlas/atlas.py -w myproduct init
python3 atlas/atlas.py -w myproduct add ~/work/ios-app --dry-run     # 무엇을 알아보는지 미리보기 (설정을 바꾸지 않는다)
python3 atlas/atlas.py -w myproduct add ~/work/ios-app               # 폴더 (읽기만 한다)
python3 atlas/atlas.py -w myproduct add ~/work/server
python3 atlas/atlas.py -w myproduct add git@host:team/admin-web.git  # git 주소 (작업 공간 안에 전용 복제본을 둔다)
python3 atlas/atlas.py -w myproduct sync
python3 atlas/atlas.py -w myproduct open
```

- `add` 는 폴더를 훑어 시스템 종류(Xcode, Gradle, Nest, Next, Prisma, Spring, Python, Go 등)를 감지하고 `workspaces/myproduct/atlas.config.json` 에 적는다. 이름과 루트가 어색하면 그 파일을 고친다. 감지하지 못한 것은 [atlas/README.md](atlas/README.md) 의 설정 형식대로 직접 적는다.
- 코드가 바뀌면 `sync` 만 다시 돌린다. 바뀐 묶음만 다시 분석하고, 나중에 추가한 소스는 기존 접점(API, 테이블, 토픽)에 연결만 한다. 폴더 소스는 읽기만 하므로 최신으로 당기는 것은 직접 하거나 `sync --pull` 을 쓴다.
- `open` 은 이 컴퓨터(127.0.0.1)에서만 열리는 서버를 띄운다. 열어 둔 채 다른 터미널에서 `sync` 하면 화면에 새 버전 알림이 뜬다.
- 여럿이 볼 곳에 올릴 때는 `site` 로 만든 `workspaces/myproduct/site/` 폴더를 사내 웹 서버(로그인 뒤)에 둔다.
- `sync` 출력의 "호출자 없는 API" 가 많으면 그 제품이 쓰는 프레임워크의 규칙이 없다는 뜻이다. 규칙은 `workspaces/myproduct/rules/*.json` 에 더한다.

## 선택 설치: 정식 파서(tree-sitter)

기본은 내장 파서(Swift, Kotlin, TypeScript/JavaScript, Java, Python, Prisma)다. tree-sitter 를 깔면 Java·Python 을 정식 파서로 읽고, Go 처럼 내장 파서가 없는 언어도 분석한다.

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements-parsers.txt
```

깔고 나면 따로 할 일은 없다. `atlas.py` 가 `.venv` 를 스스로 찾아 쓴다. 문법은 언어별 패키지에 들어 있고 실행 중에 내려받는 것은 없다.

## 작업 공간: 분석 대상의 내용은 저장소에 넣지 않는다

이 저장소에는 도구만 둔다. 분석 대상 코드에서 나온 것은 전부 작업 공간 폴더에 두고, 작업 공간은 `.gitignore` 로 제외한다.

```
workspaces/<이름>/
  atlas.config.json     소스(폴더·git 주소), 시스템, 연결 설정
  atlas-data.json       분석 결과 (sync 가 만든다)
  atlas-manifest.json   버전, 소스별 기준 커밋, 지난 버전 대비 바뀐 유닛
  atlas-store/          묶음별 캐시, git 주소 소스의 전용 복제본
  atlas-notes.json      (선택) 사람이 쓴 유닛 설명, 예시 문의, 제품 용어, 시스템 색
  rules/*.json          (선택) 이 제품에서만 쓰는 규칙 팩. 내장 팩에 더해진다
```

커밋·푸시 전에 반출 검사를 돌린다. 작업 공간의 분석 결과를 기준으로, 저장소에 올라갈 파일에 분석 대상의 이름(유닛·모듈·파일 이름, API 경로, 호스트)이 들어 있으면 실패한다.

```bash
python3 tests/leak_check.py
```

화면 캡처에는 분석 대상의 클래스 이름이 그대로 찍힌다. 문서에 넣을 그림은 `fixtures/shop` 으로 만든 것만 쓴다.

## AI 기능

검색창에서 Enter 를 누르면 AI 가 문의의 경로를 추적하고, 유닛 설명도 AI 로 생성할 수 있다. AI 에게는 구조 정보만 보낸다(코드 본문 없음).
지금은 화면을 claude.ai 아티팩트로 게시했을 때만 켜진다. 사내 웹 서버에 정적 파일로 올리면 AI 는 꺼지고 키워드·문구 검색으로 동작한다.

## 여럿이 함께 쓸 때

- 현행화(`sync`)는 서버나 CI 한곳에서 돌린다. 잠금 파일이 겹쳐 도는 것을 막고, 결과는 한 번에 교체된다.
- git 주소로 등록한 소스는 전용 복제본을 원격 끝으로 옮겨 분석한다. 폴더로 등록한 소스는 읽기만 한다.
- 열려 있는 화면은 1분마다 목록표를 보고 새 버전이 나왔음을 알린다.
- 접근 토큰은 설정 파일에 적지 않는다. 접근 제어는 이 도구가 하지 않으므로 `site` 로 만든 폴더를 사내 로그인 뒤에 올린다.

## 조작

| 동작 | 방법 |
|---|---|
| 이동 · 확대 | 드래그 · 휠 또는 핀치 · `+` `-` · `0`(전체 보기) |
| 검색 | `/` 또는 `Cmd/Ctrl+K` |
| 추적 단계·검색 결과 넘기기 | 방향키 |
| 연결 따라가기 | 유닛을 고른 뒤 `↑` `↓` 로 연결을 고르고 `→` 로 건너가기, `←` 로 돌아오기. 연결선을 직접 눌러도 된다 |
| 선택 해제 | `Esc` |

## 시험

```bash
node tests/viewer-smoke.js code-atlas.html workspaces/shop
```

가짜 DOM 으로 뷰어를 띄워 모든 유닛·확대 단계·검색·규모 시험(×10)을 통과하는지 본다. 화면 모양은 실제 브라우저에서 확인한다.

```bash
.venv/bin/python tests/parser_parity.py java fixtures/shop/order-service
```

같은 소스를 내장 파서와 tree-sitter 로 각각 분석해 유닛·멤버·접점이 어디서 달라지는지 보여 준다(선택 설치를 했을 때). 시험용 프로젝트에서는 차이가 0건이어야 한다.

## 알아 둘 한계

- 내장 파서는 주석·문자열 제거와 괄호·들여쓰기 구조에 기댄 근사 분석이다. Swift, Kotlin, TypeScript/JavaScript 는 아직 내장 파서로만 읽는다.
- 어느 파서든 매크로, 리플렉션, 의존성 주입 설정처럼 문법에 드러나지 않는 연결은 놓친다.
- 규칙 팩이 없는 프레임워크의 접점은 잡히지 않는다. `sync` 출력의 "호출자 없는 API" 수가 빠진 곳을 찾는 실마리다.
- 실제 요청·응답 값(런타임 흐름)은 보여 주지 않는다. 코드에서 읽어 낸 구조만 보여 준다.

## 라이선스

MIT. [LICENSE](LICENSE) 참고.
