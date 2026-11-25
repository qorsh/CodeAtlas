# 코드 아틀라스 분석기

여러 저장소의 코드를 분석해 구성도 화면(`code-atlas.html`)이 읽는 데이터를 만든다.
파이썬 표준 라이브러리만으로 동작하고(3.14에서 확인했다), tree-sitter 를 깔면 정식 파서로 읽는 언어가 늘어난다. 분석 대상 코드는 읽기만 하고, 결과에는 코드 본문을 넣지 않는다.

## 쓰는 법

```bash
python3 atlas/atlas.py init <이름>              # atlas.config.json 만들기
python3 atlas/atlas.py add <폴더 | git 주소>     # 소스 추가. 종류를 감지해 설정에 적는다 (--dry-run 으로 미리보기)
python3 atlas/atlas.py sync                    # 바뀐 묶음만 다시 분석하고 결과 교체
python3 atlas/atlas.py status                  # 소스별 기준 커밋, 마지막 결과
python3 atlas/atlas.py site code-atlas.html site/   # 사내 웹 서버에 올릴 정적 파일 묶음
```

설정 파일은 기본으로 현재 폴더의 `atlas.config.json` 이다. 분석 대상마다 작업 공간 폴더를 따로 두고 `--config workspaces/<이름>/atlas.config.json` 으로 가리키는 것을 권한다(`workspaces/` 는 git 에서 제외돼 있다). 결과와 캐시는 설정 파일이 있는 폴더에 생긴다.

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

규칙 종류는 `engine/rules.py` 맨 위에 정리돼 있다: `annotation`, `call`, `code`, `text`, `unit-annotation`, `unit-text`, `service`, `heuristic`.

`call` 규칙에서 자주 쓰는 선택 항목

- `lit_method`: `"POST /orders"` 처럼 문자열 안에 메서드가 함께 적힌 꼴을 메서드와 경로로 나눈다.
- `handler`: 라우트를 등록하는 곳과 처리하는 곳이 다를 때(`mux.HandleFunc("/x", server.Reserve)`), 마지막 인자로 넘긴 메서드를 찾아 거기에 라우트를 붙인다.
- 문자열을 조립해 곧바로 넘기는 호출(`client.Get(fmt.Sprintf("%s/items/%d", base, id))`)은 엔진이 한 겹 벗겨 바깥 호출을 본다. 규칙에서 따로 할 일은 없다.

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
               "roots": ["src/main/java"], "tests": [["src/test/java", ".java"]], "db": "orders-db"}],
  "link": {"twins": [["android", "ios"]]}
}
```

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
  "colors": {"server": "#8FA6FF"}
}
```

유닛은 `시스템id:유닛이름`, 메서드는 `#메서드이름`, API 는 `@GET /경로` 로 가리킨다. `type` 은 `q`(문의), `fix`(수정 요청), `err`(오류) 중 하나다.

## 여럿이 함께 쓸 때

- **현행화는 한곳에서**: 서버나 CI 에서 `sync` 를 주기적으로(또는 푸시 때) 돌린다. 잠금 파일(`atlas-store/.lock`)이 겹쳐 도는 것을 막는다.
- **git 주소 소스**는 `atlas-store/sources/` 에 전용 복제본을 두고 원격 끝으로 옮긴다. 경로 소스는 읽기만 하고, `--pull` 을 줬을 때만 당겨 온다.
- **접근 토큰은 설정에 적지 않는다.** git 의 자격 증명 도우미나 배포 키를 실행 환경에 둔다. 비밀번호를 물으며 멈추지 않도록 프롬프트는 꺼 둔다.
- **결과 교체는 한 번에**: 임시 파일에 다 쓴 뒤 이름을 바꾼다. 목록표를 마지막에 쓰고, 열려 있는 화면은 1분마다 그것을 보고 "새 버전이 있습니다"를 띄운다.
- **캐시**: 묶음마다 엔진·규칙 버전, 설정, 파일 내용의 해시를 기록한다. 하나라도 바뀐 묶음만 다시 분석하고, 연결 단계는 저장된 접점만으로 다시 맞춘다.
- **접근 제어**는 이 도구가 하지 않는다. `site` 로 만든 폴더를 사내 로그인 뒤에 올린다.
- **분석 대상의 내용은 도구 저장소에 넣지 않는다.** 설정·결과·캐시·메모·제품 전용 규칙은 작업 공간에만 두고, 커밋 전에 `python3 tests/leak_check.py` 로 확인한다.

## 알아 둘 한계

- 내장 파서는 주석·문자열 제거와 괄호·들여쓰기 구조에 기댄 근사 분석이다. 매크로, 리플렉션, 의존성 주입 설정처럼 문법에 드러나지 않는 연결은 놓친다.
- Swift, Kotlin, TypeScript/JavaScript 는 아직 내장 파서로만 읽는다. tree-sitter 명세를 더하면 바꿀 수 있지만, 기존 결과와 달라지는 곳을 `tests/parser_parity.py` 로 확인한 뒤에 바꿔야 한다.
- 주소를 변수에 담아 두었다가 다른 줄에서 호출하는 코드(`url := ...; http.Get(url)`)는 호출과 주소를 잇지 못한다. 값의 흐름은 따라가지 않는다.
- 유닛 종류(화면·서비스·저장소 등)는 이름 끝말로 어림한다. 이름 규칙이 다른 팀에서는 종류가 덜 맞을 수 있다(연결에는 영향이 없다).
- 규칙이 없는 프레임워크의 접점은 잡히지 않는다. `sync` 출력의 "호출자 없는 API" 수와 화면의 열린 접점이 빠진 곳을 찾는 실마리다.

## 시험

`fixtures/shop/` 은 Java Spring 서버, Python FastAPI 서비스, Go 재고 서비스, React 웹, Kotlin Retrofit 앱으로 이루어진 작은 시험용 프로젝트다.

```bash
mkdir -p workspaces/shop
python3 atlas/atlas.py --config workspaces/shop/atlas.config.json init shop
python3 atlas/atlas.py --config workspaces/shop/atlas.config.json add fixtures/shop
python3 atlas/atlas.py --config workspaces/shop/atlas.config.json sync      # tree-sitter 가 있으면 http 6 · db 2 · msg 1 · ext 1, 없으면 Go 를 건너뛰고 http 3 · db 2 · msg 1
node tests/viewer-smoke.js code-atlas.html workspaces/shop                  # 그 결과로 화면이 끝까지 그려지는지
.venv/bin/python tests/parser_parity.py java fixtures/shop/order-service    # 내장 파서와 tree-sitter 의 결과가 같은지 (차이 0건)
.venv/bin/python tests/parser_parity.py python fixtures/shop/payment-service
python3 tests/leak_check.py                                                 # 분석 대상의 내용이 저장소에 섞이지 않았는지
```
