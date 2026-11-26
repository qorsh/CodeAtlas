# 코드 아틀라스 분석기

여러 저장소의 코드를 분석해 구성도 화면(`code-atlas.html`)이 읽는 데이터를 만든다.
파이썬 표준 라이브러리만으로 동작하고(3.14에서 확인했다), tree-sitter 를 깔면 정식 파서로 읽는 언어가 늘어난다. 분석 대상 코드는 읽기만 하고, 결과에는 코드 본문을 넣지 않는다.

## 쓰는 법

```bash
python3 atlas/atlas.py -w <이름> init                   # 작업 공간 workspaces/<이름>/ 과 설정 파일 만들기
python3 atlas/atlas.py -w <이름> add <폴더 | git 주소>   # 소스 추가. 종류를 감지해 설정에 적는다 (--dry-run 으로 미리보기)
python3 atlas/atlas.py -w <이름> sync                   # 바뀐 묶음만 다시 분석하고 결과 교체
python3 atlas/atlas.py -w <이름> status                 # 소스별 기준 커밋, 마지막 결과
python3 atlas/atlas.py -w <이름> open                   # 이 컴퓨터에서만 열리는 서버를 띄우고 브라우저로 연다 (설정 패널 포함. 빈 작업 공간도 열린다)
python3 atlas/atlas.py -w <이름> site                   # 사내 웹 서버에 올릴 정적 파일 묶음 (workspaces/<이름>/site/)
python3 atlas/atlas.py -w <이름> ingest <접속 로그 | ->   # 실제 요청·응답을 지도에 얹는다 (--follow 로 계속 따라가기)
```

`-w <이름>` 은 이 저장소의 `workspaces/<이름>/atlas.config.json` 을 가리킨다(`workspaces/` 는 git 에서 제외돼 있다). 작업 공간을 저장소 밖에 두려면 `--config <설정 파일 경로>` 를 쓴다. 결과와 캐시는 설정 파일이 있는 폴더에 생긴다.

`sync` 가 만드는 것은 `atlas-data.json`(구성도 데이터)과 `atlas-manifest.json`(버전, 소스별 커밋, 지난 버전 대비 바뀐 유닛)이다.

## 구조: 층마다 따로 늘린다

| 층 | 위치 | 아는 것 | 늘리는 법 |
|---|---|---|---|
| 언어 어댑터 | `langs/*.py` | 문법. 유닛(클래스·파일)과 멤버(메서드)의 범위 | tree-sitter 문법이 있는 언어는 `langs/treesitter.py` 에 명세 한 덩어리, 아니면 파일 하나를 추가하고 `register_language` 호출 |
| 공통 엔진 | `engine/*.py` | 이름 풀기, 사실 추출(어노테이션, 문자열을 인자로 받는 호출) | 보통 손대지 않는다 |
| 규칙 팩 | `rules/*.json` | 프레임워크. 사실을 접점(제공·사용하는 API, 테이블, 토픽)으로 바꾼다 | JSON 파일 하나를 추가 |
| 연결기 | `atlas.py` 의 `link` | 접점 종류별로 제공과 사용을 맞춘다. 언어를 모른다 | 접점 종류가 새로 생길 때만 |

지금 들어 있는 것

- 언어: Swift, Kotlin, TypeScript/JavaScript, Java, Python, Prisma 스키마(이상 내장 파서), Go(tree-sitter 필요)
- 규칙: NestJS, Express, Spring(MVC·Data·RestTemplate·Kafka), JPA, Retrofit, FastAPI·Flask, SQLAlchemy, axios·fetch, requests·httpx, Go(net/http·gin·echo·chi 라우터, http 클라이언트), Prisma, 코드 속 SQL, 어느 제품에나 있을 만한 외부·기기 서비스
- 프레임워크 없이 경로 문자열을 직접 쓰는 클라이언트용 휴리스틱 3종(`client-*`). 넓게 잡으므로 시스템 설정의 `rules` 에 적었을 때만 켜진다

모든 연결에는 그것을 만든 규칙 이름이 `by` 로 남는다. 호출자를 찾지 못한 API 는 화면에 "열린 접점"으로 보인다.

### 규칙 팩 예

```json
{"id": "spring", "langs": ["java", "kotlin"], "rules": [
  {"id": "mvc", "type": "annotation", "kind": "http", "role": "provide",
   "names": {"GetMapping": "GET", "PostMapping": "POST", "RequestMapping": "*"},
   "prefix": {"names": ["RequestMapping"]}, "unit_kind": "controller"}
]}
```

규칙 종류는 `engine/rules.py` 맨 위에 정리돼 있다: `annotation`, `call`, `code`, `text`, `unit-annotation`, `unit-text`, `service`, `status`, `heuristic`.

`call` 규칙에서 자주 쓰는 선택 항목

- `lit_method`: `"POST /orders"` 처럼 문자열 안에 메서드가 함께 적힌 꼴을 메서드와 경로로 나눈다.
- `handler`: 라우트를 등록하는 곳과 처리하는 곳이 다를 때(`mux.HandleFunc("/x", server.Reserve)`), 마지막 인자로 넘긴 메서드를 찾아 거기에 라우트를 붙인다.
- 문자열을 조립해 곧바로 넘기는 호출(`client.Get(fmt.Sprintf("%s/items/%d", base, id))`)은 엔진이 한 겹 벗겨 바깥 호출을 본다. 규칙에서 따로 할 일은 없다.

### 응답 코드 규칙 (`status`)

어느 메서드가 어떤 상태 코드를 내려주는지 찾는 정규식이다. 첫 묶음이 코드 숫자나 이름(`NOT_FOUND`, `StatusConflict`, `NotFoundException`)이면 된다.

```json
{"id": "status-const", "type": "status", "regex": "\\bhttp\\.Status([A-Z]\\w+)"}
{"id": "status-class", "type": "status", "unit": true, "regex": "@ResponseStatus\\(\\s*HttpStatus\\.([A-Z_]+)"}
```

- `unit: true` 는 타입 선언에 붙은 것일 때 그 타입(예외 클래스)의 코드로 삼는다. 그 예외를 던지는 자리가 모두 그 코드의 자리가 된다.
- 예외를 던지는 자리(`throw new X(…)`, `raise X(…)`, `orElseThrow(() -> new X(…))`)는 규칙 없이 엔진이 찾는다. X 의 코드는 ① 규칙이 붙인 것 ② 상속한 프레임워크 예외의 이름 ③ `@ExceptionHandler` 가 정한 것 ④ 클래스 이름으로 어림한 것 순서로 정하고, ④ 는 결과에 '추정'으로 표시된다.
- 결과는 멤버의 `st: [[코드, 줄, 문구, 추정 여부]]` 로 나간다. 문구는 그 문장에 함께 적힌 문자열 하나다.

### 제품 전용 규칙은 작업 공간에

사내 프레임워크나 그 제품만 쓰는 외부 서비스처럼 밖에 내놓을 수 없는 규칙은 `atlas/rules/` 에 넣지 않는다.
설정 파일 옆 `rules/*.json`(또는 설정의 `rules_dir`)에 두면 내장 팩 뒤에 함께 읽힌다.
팩 `id` 가 내장 팩과 같으면 그 팩을 통째로 대신하고, `service` 규칙은 `id` 가 같으면 작업 공간 것이 이긴다(제품에 맞춘 설명으로 바꿀 때 쓴다).

### 파서: 내장과 tree-sitter

어댑터 층에는 앞단이 둘 있다. 뒤 단계(이름 풀기, 사실, 규칙, 연결)는 어느 쪽이 읽었는지 모른다.

| | 내장 파서 | tree-sitter 앞단 (`langs/treesitter.py`) |
|---|---|---|
| 방식 | 주석·문자열을 지우고 괄호·들여쓰기 구조를 읽는 근사 분석 | 정식 문법으로 만든 구문 트리 |
| 설치 | 필요 없음 | `pip install -r requirements-parsers.txt` |
| 언어 | Swift, Kotlin, TS/JS, Java, Python, Prisma | Java, Python, Go (명세를 더하면 늘어난다) |

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements-parsers.txt
```

- 파이썬 3.10 이상에서는 tree-sitter 0.27 묶음이, 3.9 에서는 그 판을 지원하는 마지막 묶음(0.23)이 깔린다. 두 묶음 모두 시험용 프로젝트에서 같은 결과를 낸다(3.9, 3.13, 3.14 에서 확인).
- 저장소의 `.venv` 에 tree-sitter 가 있으면 `atlas.py` 는 그 파이썬으로 스스로 다시 실행한다. 누가 어떤 파이썬으로 부르든 같은 파서로 분석돼야 결과와 캐시가 흔들리지 않기 때문이다(`ATLAS_NO_VENV=1` 로 끈다).
- 문법은 PyPI 의 언어별 패키지(`tree-sitter-java` 등)에 들어 있다. 실행 중에 내려받는 것이 없어 사내 PyPI 미러만으로 설치할 수 있다.
- 설정의 `"parser"`: `"auto"`(기본. 문법이 있으면 tree-sitter, 없으면 내장), `"builtin"`(항상 내장), `"tree-sitter"`(문법이 없으면 멈춘다).
- `auto` 에서 문법이 없는 언어의 시스템(내장 파서도 없는 Go 등)은 건너뛰고 `sync` 출력과 목록표의 `skipped` 에 남긴다.
- 어느 파서로 읽었는지는 `sync` 출력의 묶음마다 나온다. 설치된 파서의 판은 캐시 지문에 들어가므로, 파서를 올리면 그 묶음은 다시 분석된다.

새 언어를 tree-sitter 로 붙일 때 쓰는 것은 명세 하나다. 트리를 훑는 코드는 공용이다.

```python
SITTER_SPECS['go'] = dict(
    grammar='tree_sitter_go', exts=['.go'],
    types={'type_spec': ..., 'type_alias': 'type'},                 # 유닛이 되는 선언
    funcs=['function_declaration', 'method_declaration', 'method_elem'],   # 멤버가 되는 선언
    comments=['comment'], strings=['interpreted_string_literal', 'raw_string_literal'], chars=['rune_literal'],
    kind=sitter_kind, receiver=sitter_go_receiver,                  # 타입 밖에 선언하는 메서드의 소속
    file_unit=True, toplevel_calls=True, extensions=True)
```

명세를 쓰거나 고친 뒤에는 두 파서의 결과를 나란히 비교해 본다(내장 파서가 있는 언어).

```bash
.venv/bin/python tests/parser_parity.py java fixtures/shop/order-service
```

### 언어 어댑터가 할 일 (내장 파서를 직접 쓸 때)

`parse(시스템 id, 파일 경로, 모듈 이름)` 안에서 `load_file` 로 파일을 읽고 `add_unit` 으로 유닛을 등록한 뒤,
멤버를 `dict(n=이름, s=시그니처, a=시작 위치, b=끝 위치, k='func', d=설명)` 으로 붙인다.
프레임워크 의미는 넣지 않는다. `langs/java.py`(중괄호 언어)와 `langs/python.py`(들여쓰기 언어)가 본보기다.

## 설정 파일

```json
{
  "name": "제품 이름",
  "api_hosts": ["api.example.com"],
  "sources": [{"name": "app", "git": "git@host:team/app.git", "branch": "main"},
              {"name": "local", "path": "~/work/other"}],
  "systems": [{"id": "server", "name": "주문 서버", "source": "app", "lang": "java",
               "roots": ["src/main/java"], "tests": [["src/test/java", ".java"]], "db": "orders-db",
               "hosts": ["api.example.com", "order-service"], "prefix": ["/shop"]}],
  "external": [{"id": "erp", "name": "레거시 ERP", "hosts": ["erp.example.com"], "paths": ["/erp/**"], "clients": "Erp\\w*Client"}],
  "link": {"twins": [["android", "ios"]], "calls": {"admin-web": ["server"]}, "never": {"admin-web": ["other-server"]}}
}
```

여러 제품을 한 작업 공간에 넣을 때 쓰는 것들이다. 화면의 설정 패널이 같은 값을 고친다.

- `systems[].hosts`: 이 서버에 닿는 주소. 호출에 이 주소가 적혀 있으면 경로가 다른 서버와 겹쳐도 이 서버 안에서만 찾는다. 포트를 안 적으면 어느 포트든 맞고 `*.example.com` 꼴도 된다. 개발·운영·검증 주소를 모두 적어 둔다. 코드가 주소를 실행할 때 고르더라도(`${base()}/x`, 환경별 상수), 그 시스템의 코드에 적힌 주소의 주인이 이 서버 하나면 겹치는 경로의 호출이 이 서버에 붙는다.
- `systems[].prefix`: 게이트웨이가 경로 앞에 붙이는 접두어. `/shop/api/orders` 로 부르는 호출을 이 서버의 `/api/orders` 에 맞춘다.
- `external`: 소스 없이 지도에 올리는 시스템(레거시). `hosts`(주소), `paths`(경로 꼴. `*` 는 조각 하나, `**` 는 나머지 전부), `clients`(호출하는 유닛 이름의 정규식) 가운데 하나라도 맞는 호출이 이쪽으로 간다. 시스템마다 유닛 하나가 생기고 우리가 부르는 API 가 멤버가 된다.
- `link.calls`: 경로가 여러 서버에 맞을 때 고를 서버(앞에 적은 것 먼저). `link.never`: 잇지 않을 서버.

호출 대상을 가리는 순서와 근거는 `atlas.py` 의 `resolve_calls` 에 적혀 있다. 끝내 못 가린 호출은 선을 긋지 않고 결과의 `meta.pending` 에 남기고, 지도에 없는 주소로 가는 호출은 `meta.offmap` 에 모은다. `sync` 출력의 "호출 대상:" 줄이 근거별 건수다.

- `ns` 가 같은 시스템들은 한 묶음으로 함께 분석된다(서로 이름을 풀 수 있다).
- `module` 은 폴더 경로를 모듈 이름으로 바꾸는 정규식 치환 목록이다. 없으면 루트 기준 상대 경로를 쓴다.
- `rules` 로 기본에 없는 팩을 켜고, `rules_off` 로 기본 팩을 끈다.

### 사람이 쓴 설명: `atlas-notes.json` (선택)

결과 파일 옆에 두면 화면이 함께 읽는다. `site` 명령도 이 파일이 있으면 묶음에 넣는다.

```json
{
  "notes": {"server:OrderService": {"role": "무엇을 하는 곳", "inp": "받는 것", "how": "처리", "out": "내보내는 곳"}},
  "scenarios": [{"id": "pay-fail", "type": "q", "label": "문의", "q": "결제가 실패해요", "summary": "…",
                 "causes": [{"t": "제목", "d": "설명", "at": "server:PaymentClient#charge"}],
                 "steps": [{"u": "web:api#createOrder", "d": "이 단계에서 일어나는 일"}], "kw": ["payment", "order"]}],
  "alias": {"정기결제": ["subscription", "billing"]},
  "colors": {"server": "#8FA6FF"},
  "columns": [["ios", "android"], ["server"], ["db"]],
  "positions": {"server": [5200, -800]}
}
```

`columns` 는 시스템을 놓을 열을 직접 정한다. 없으면 시스템 사이의 연결 방향(부르는 쪽 → 불리는 쪽)으로 자동 배치한다. `positions` 는 시스템이 놓인 자리다. 화면이 처음 놓은 자리와 사람이 끌어 옮긴 자리를 모두 적어 둔다(`open` 으로 열었을 때. 정적 파일로 올린 화면에서는 그 브라우저에만 저장된다). 적혀 있는 시스템은 그 자리에 그대로 두고, 나중에 추가돼 자리가 없는 시스템만 자동 배치를 따르되 겹치면 아래로 비켜 놓는다. 화면의 `자동 정렬`이 전체를 다시 놓고 이 값을 새로 적는다.

유닛은 `시스템id:유닛이름`, 메서드는 `#메서드이름`, API 는 `@GET /경로` 로 가리킨다. `type` 은 `q`(문의), `fix`(수정 요청), `err`(오류) 중 하나다.

## 실측 응답: `ingest`

접속 로그의 요청을 분석해 둔 API(멤버의 `route`)에 맞춰 `atlas-runtime.json` 을 만든다. 화면은 이 파일을 10초마다 다시 읽는다.

```json
{"generated": "2026-11-26T10:00:00", "source": "access.log", "window": "최근 300초", "total": 125, "matched": 120, "abnormal_from": 400,
 "routes": [{"id": "inventory-service:Server", "m": "Reserve", "route": "POST /inventory/reserve", "n": 19, "codes": {"204": 12, "409": 4, "500": 3}, "p95": 1440.0}],
 "unmatched": [["GET /healthz", 5]]}
```

- 맞추는 법: 쿼리 문자열과 호스트를 떼고, 조각 수와 메서드가 같은 라우트 가운데 글자가 그대로 맞는 조각이 가장 많은 것을 고른다(`/orders/recent` 가 `/orders/{id}` 보다 먼저).
- 맞는 라우트가 없는 경로는 값(숫자·긴 식별자)을 가린 모양으로 상위 15개만 남긴다. 많이 남으면 `--strip-prefix` 가 필요하거나, 그 API 를 제공하는 코드가 아직 분석되지 않은 것이다.
- 형식을 자동으로 못 알아보면 설정 파일에 `"runtime": {"regex": "…", "strip_prefix": ["/v1"]}` 를 적어 두고 쓴다.
- 다른 출처(APM, 게이트웨이 통계)의 숫자를 얹고 싶으면 위 모양의 파일을 직접 만들어 결과 파일 옆에 두면 된다.

## 여럿이 함께 쓸 때

- **현행화는 한곳에서**: 서버나 CI 에서 `sync` 를 주기적으로(또는 푸시 때) 돌린다. 잠금 파일(`atlas-store/.lock`)이 겹쳐 도는 것을 막는다.
- **git 주소 소스**는 `atlas-store/sources/` 에 전용 복제본을 두고 원격 끝으로 옮긴다. 경로 소스는 읽기만 하고, `--pull` 을 줬을 때만 당겨 온다.
- **접근 토큰은 설정에 적지 않는다.** git 의 자격 증명 도우미나 배포 키를 실행 환경에 둔다. 비밀번호를 물으며 멈추지 않도록 프롬프트는 꺼 둔다.
- **결과 교체는 한 번에**: 임시 파일에 다 쓴 뒤 이름을 바꾼다. 목록표를 마지막에 쓰고, 열려 있는 화면은 1분마다 그것을 보고 "새 버전이 있습니다"를 띄운다.
- **캐시**: 묶음마다 엔진·규칙 버전, 설정, 파일 내용의 해시를 기록한다. 하나라도 바뀐 묶음만 다시 분석하고, 연결 단계는 저장된 접점만으로 다시 맞춘다.
- **접근 제어**는 이 도구가 하지 않는다. `site` 로 만든 폴더를 사내 로그인 뒤에 올린다. 혼자 볼 때는 `open` 을 쓴다(127.0.0.1 에서만 열린다). `python3 -m http.server` 는 기본으로 같은 네트워크의 다른 컴퓨터에도 열리므로 쓰지 않는다.
- **분석 대상의 내용은 도구 저장소에 넣지 않는다.** 설정·결과·캐시·메모·제품 전용 규칙은 작업 공간에만 두고, 커밋 전에 `python3 tests/leak_check.py` 로 확인한다.

## 알아 둘 한계

- 내장 파서는 주석·문자열 제거와 괄호·들여쓰기 구조에 기댄 근사 분석이다. 매크로, 리플렉션, 의존성 주입 설정처럼 문법에 드러나지 않는 연결은 놓친다.
- Swift, Kotlin, TypeScript/JavaScript 는 아직 내장 파서로만 읽는다. tree-sitter 명세를 더하면 바꿀 수 있지만, 기존 결과와 달라지는 곳을 `tests/parser_parity.py` 로 확인한 뒤에 바꿔야 한다.
- 주소를 변수에 담아 두었다가 다른 줄에서 호출하는 코드(`url := ...; http.Get(url)`)는 호출과 주소를 잇지 못한다. 값의 흐름은 따라가지 않는다. 다만 `{BASE}/x` · `${BASE}/x` · `$BASE_URL/x` 처럼 문자열 맨 앞에 놓인 이름이 같은 시스템 안의 주소 상수(`BASE = "https://…"`) 하나를 가리키면 그 주소를 붙여 준다.
- 서버 주소가 코드에 없는 호출(환경 변수, 배포 설정)은 주소 근거 없이 가린다. 경로가 여러 서버에 맞고 다른 근거도 없으면 "확인이 필요한 호출"로 남는다.
- 실측 응답은 접속 로그 수준이다. 서버 안의 어느 메서드에서 실패했는지, 요청 한 건이 시스템 사이를 어떻게 지났는지는 분산 추적 기록이 있어야 알 수 있고 아직 읽지 못한다.
- 유닛 종류(화면·서비스·저장소 등)는 이름 끝말로 어림한다. 이름 규칙이 다른 팀에서는 종류가 덜 맞을 수 있다(연결에는 영향이 없다).
- 규칙이 없는 프레임워크의 접점은 잡히지 않는다. `sync` 출력의 "호출자 없는 API" 수와 화면의 열린 접점이 빠진 곳을 찾는 실마리다.

## 시험

`fixtures/shop/` 은 Java Spring 서버, Python FastAPI 서비스, Go 재고 서비스, React 웹, Kotlin Retrofit 앱으로 이루어진 작은 시험용 프로젝트다. `fixtures/booking/`(예약 서버와 웹: 쇼핑과 같은 경로의 API 가 있고, 쇼핑의 결제 서버를 함께 쓰며, 소스 없는 레거시 ERP 를 부른다)과 `fixtures/backoffice/`(겹치는 경로만 부르는 관리자 화면)는 여러 제품을 한 지도에 넣는 경우를 시험한다.

```bash
python3 atlas/atlas.py -w shop init
python3 atlas/atlas.py -w shop add fixtures/shop
python3 atlas/atlas.py -w shop sync      # tree-sitter 가 있으면 http 6 · db 2 · msg 1 · ext 1, 없으면 Go 를 건너뛰고 http 3 · db 2 · msg 1
python3 atlas/atlas.py -w shop ingest fixtures/shop/logs/access-nginx.txt   # 시험용 접속 로그: 비정상 14건이 3곳에서 나와야 한다
node tests/viewer-smoke.js code-atlas.html workspaces/shop                  # 그 결과로 화면이 끝까지 그려지는지 (좌표·응답 표시 포함)
.venv/bin/python tests/parser_parity.py java fixtures/shop/order-service    # 내장 파서와 tree-sitter 의 결과가 같은지 (차이 0건)
.venv/bin/python tests/parser_parity.py python fixtures/shop/payment-service
python3 tests/link_check.py                                                 # 여러 제품을 한 지도에: 호출 대상 가리기, 확인 필요, 함께 쓰는 서버, 레거시
python3 tests/server_check.py                                               # 설정 패널이 쓰는 로컬 서버 주소 (토큰·Host 확인 포함)
node tests/ui-check.js /tmp/atlas-ui                                        # 실제 Chrome 으로 시스템 목록·배치 편집·설정 패널을 눌러 본다
python3 tests/leak_check.py                                                 # 분석 대상의 내용이 저장소에 섞이지 않았는지
```
