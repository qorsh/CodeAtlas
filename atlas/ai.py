"""AI 연결. 로컬 서버(atlas.py open)가 화면의 TRACE·유닛 설명 요청을 받아, 작업 공간에 정해 둔 곳으로 넘긴다.

화면이 보내는 것은 분석 결과로 만든 글(유닛·메서드 이름, 시그니처, 주석, 호출 관계, API 경로, 코드 속 문자열)과 사람이 적은 문의다.
코드 본문은 분석 결과에 없으므로 나가지 않는다. 어디로 보낼지는 작업 공간마다 따로 정하고, 정하기 전에는 아무 데도 보내지 않는다.

연결 방식 (provider)
  claude    Claude API. 공식 SDK(anthropic 패키지)를 쓴다. 키를 비워 두면 SDK 가 환경 변수(ANTHROPIC_API_KEY)나 ant 로그인 정보를 찾는다.
            주소(base_url)를 적으면 같은 형식(Messages API)을 받는 사내 게이트웨이로 보낸다.
  foundry   Azure(Microsoft Foundry)에 배포한 Claude. 같은 SDK 의 Foundry 클라이언트를 쓴다. 리소스 이름과 키가 필요하다.
  openai    OpenAI 호환 형식(…/chat/completions)을 받는 사내 엔드포인트. 표준 라이브러리만 쓴다.

설정은 작업 공간의 atlas-ai.json 에 둔다(git 제외, 이 사용자만 읽는 권한). 키는 화면으로 돌려주지 않는다.
환경 변수 ATLAS_AI_KEY 가 있으면 파일의 키보다 먼저 쓴다.
"""
import importlib.util
import json
import os
import re
import socket
import urllib.error
import urllib.request

FILE = 'atlas-ai.json'
DEFAULT_MODEL = 'claude-opus-5-5'
PROVIDERS = ('claude', 'foundry', 'openai')
TIMEOUT = 300.0                  # 경로 추적은 구조 정보가 길어 1분쯤 걸릴 수 있다
MAX_CHARS = 240_000              # 한 번에 보내는 글의 기본 한도(글자 수). 화면이 이보다 길면 단계를 나눠 줄여 보낸다
NO_SDK = ('Claude 방식은 anthropic 패키지가 필요합니다. 저장소 폴더에서 '
          'python3 -m venv .venv && .venv/bin/pip install -r requirements-ai.txt 를 실행한 뒤 atlas.py open 을 다시 띄우세요.')
# 정책상 거절된 요청을 서버가 다른 모델로 다시 돌려 주는 기능(fallbacks)이 있는 모델군. Anthropic 의 API 에 직접 붙을 때만 켠다
FALLBACK_MODELS = ('claude-opus-5', 'claude-sonnet-5-5', 'claude-fable-5', 'claude-mythos-5')
_WORKS = {}                      # (방식, 주소, 모델) → 통한 요청 꼴의 순번. 한 번 거절된 선택 항목을 매번 다시 보내지 않는다


class AIError(Exception):
    """화면에 그대로 보여 줄 수 있는 실패. code 는 화면이 다시 시도할지 가리는 데 쓴다."""

    def __init__(self, code, message, status=502):
        super().__init__(message)
        self.code, self.message, self.status = code, message, status


def have_sdk():
    return importlib.util.find_spec('anthropic') is not None


def load(folder):
    path = os.path.join(folder, FILE)
    try:
        conf = json.load(open(path, encoding='utf-8')) if os.path.exists(path) else {}
    except (OSError, ValueError):
        conf = {}
    return conf if isinstance(conf, dict) else {}


def save(folder, conf):
    """키가 들어가는 파일이라 이 사용자만 읽고 쓰게 만든다."""
    path = os.path.join(folder, FILE)
    tmp = path + '.tmp'
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w', encoding='utf-8') as f:
        json.dump(conf, f, ensure_ascii=False, indent=2)
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        pass
    os.replace(tmp, path)


def api_key(conf):
    return os.environ.get('ATLAS_AI_KEY') or str(conf.get('key') or '')


def pick_model(conf, tier='default'):
    fallback = DEFAULT_MODEL if conf.get('provider') == 'claude' else ''
    return str((tier == 'quick' and conf.get('quick_model')) or conf.get('model') or fallback)


def public(conf):
    """화면에 내주는 설정. 키는 있는지만 알려 준다."""
    p = conf.get('provider') if conf.get('provider') in PROVIDERS else ''
    key = api_key(conf)
    why = ''
    if not p:
        why = '아직 연결을 정하지 않았습니다.'
    elif p in ('claude', 'foundry') and not have_sdk():
        why = NO_SDK
    elif p == 'foundry' and not conf.get('resource'):
        why = 'Azure 리소스 이름을 적어 주세요.'
    elif p == 'foundry' and not key:
        why = 'Azure 에서 받은 키를 넣어 주세요.'
    elif p == 'foundry' and not conf.get('model'):
        why = 'Azure 에 만든 배포 이름을 모델 칸에 적어 주세요.'
    elif p == 'openai' and not re.match(r'https?://', str(conf.get('base_url') or '')):
        why = '엔드포인트 주소를 적어 주세요.'
    elif p == 'openai' and not conf.get('model'):
        why = '모델(또는 배포) 이름을 적어 주세요.'
    enabled = bool(conf.get('enabled', True))
    return dict(provider=p, enabled=enabled, base_url=str(conf.get('base_url') or ''), model=str(conf.get('model') or ''), quick_model=str(conf.get('quick_model') or ''),
                resource=str(conf.get('resource') or ''), auth=conf.get('auth') if conf.get('auth') in ('bearer', 'api-key') else 'bearer',
                max_chars=int(conf.get('max_chars') or MAX_CHARS), has_key=bool(key), key_from='env' if os.environ.get('ATLAS_AI_KEY') else 'file' if conf.get('key') else '',
                sdk=have_sdk(), ready=not why, on=enabled and not why, why=why, default_model=DEFAULT_MODEL, using=pick_model(conf) if p else '')


def update(folder, body):
    """화면에서 온 값으로 설정을 고친다. 모르는 값은 버리고, 키는 새로 적었을 때만 바꾼다."""
    conf = load(folder)
    if 'provider' in body:
        p = str(body.get('provider') or '')
        if p and p not in PROVIDERS:
            raise SystemExit('모르는 연결 방식입니다.')
        conf['provider'] = p
    if 'base_url' in body:
        url = str(body.get('base_url') or '').strip()
        if url and not re.match(r'https?://[^\s]{3,300}$', url):
            raise SystemExit('주소는 http:// 또는 https:// 로 시작해야 합니다.')
        conf['base_url'] = url
    for k, pat, msg in (('model', r'[\w.:@/-]{0,120}$', '모델 이름'), ('quick_model', r'[\w.:@/-]{0,120}$', '빠른 모델 이름'), ('resource', r'[\w.-]{0,80}$', 'Azure 리소스 이름')):
        if k in body:
            v = str(body.get(k) or '').strip()
            if not re.match(pat, v):
                raise SystemExit(f'{msg}에 쓸 수 없는 글자가 있습니다.')
            conf[k] = v
    if 'auth' in body:
        conf['auth'] = 'api-key' if body.get('auth') == 'api-key' else 'bearer'
    if 'enabled' in body:
        conf['enabled'] = bool(body.get('enabled'))
    if 'max_chars' in body:
        try:
            conf['max_chars'] = max(20_000, min(2_000_000, int(body.get('max_chars') or MAX_CHARS)))
        except (TypeError, ValueError):
            raise SystemExit('한 번에 보내는 크기는 숫자로 적어 주세요.')
    if body.get('clear_key'):
        conf.pop('key', None)
    elif str(body.get('key') or '').strip():
        key = str(body['key']).strip()
        if len(key) > 4000 or re.search(r'\s', key):
            raise SystemExit('키에 빈칸이 들어 있습니다. 다시 붙여 넣어 주세요.')
        conf['key'] = key
    save(folder, {k: v for k, v in conf.items() if v not in ('', None)})
    return load(folder)


def ask(conf, prompt, tier='default'):
    """글 하나를 보내고 답 글을 받는다. 돌려주는 것: text, model, truncated(길이 한도에 잘림), usage."""
    view = public(conf)
    if not view['provider']:
        raise AIError('off', 'AI 연결이 설정되어 있지 않습니다. 설정의 "AI 연결"에서 정해 주세요.', 409)
    if not view['enabled']:
        raise AIError('off', 'AI 연결이 꺼져 있습니다.', 409)
    if view['why']:
        raise AIError('no_sdk' if view['why'] == NO_SDK else 'config', view['why'], 409)
    prompt = str(prompt or '')
    if not prompt.strip():
        raise AIError('bad_request', '보낼 내용이 없습니다.', 400)
    if len(prompt) > max(view['max_chars'], MAX_CHARS) * 2:
        raise AIError('prompt_too_large', 'AI 에 보낼 구조 정보가 너무 큽니다.', 413)
    return (_openai if view['provider'] == 'openai' else _claude)(conf, prompt, 'quick' if tier == 'quick' else 'default')


# ───────────────────────────── Claude (공식 SDK) ─────────────────────────────
def _claude(conf, prompt, tier):
    try:
        import anthropic
    except ImportError:
        raise AIError('no_sdk', NO_SDK, 409)
    model, key = pick_model(conf, tier), api_key(conf)
    first_party = conf.get('provider') == 'claude' and not conf.get('base_url')
    try:
        if conf.get('provider') == 'foundry':
            client = anthropic.AnthropicFoundry(api_key=key, resource=conf.get('resource'), timeout=TIMEOUT, max_retries=2)
        else:
            opts = dict(timeout=TIMEOUT, max_retries=2)
            if key:
                opts['api_key'] = key
            if conf.get('base_url'):
                opts['base_url'] = conf['base_url']
            client = anthropic.Anthropic(**opts)
    except Exception as e:                    # 키도 로그인 정보도 없을 때 SDK 가 여기서 알려 준다
        raise AIError('auth', f'Claude 연결을 만들지 못했습니다. 키를 넣었는지 확인해 주세요. ({type(e).__name__})', 401)

    # 길고 오래 걸릴 수 있는 요청이라 스트리밍으로 받고 끝난 메시지만 쓴다. 생각 깊이는 effort 로 조절한다(후보 고르기·유닛 설명은 낮게).
    base = dict(model=model, max_tokens=64000, messages=[{'role': 'user', 'content': prompt}])
    rich = dict(base, thinking={'type': 'adaptive'}, output_config={'effort': 'low' if tier == 'quick' else 'medium'})
    shapes = [('plain', rich), ('plain', base)]          # 뒤로 갈수록 선택 항목을 뺀 꼴: 옛 모델이나 사내 게이트웨이가 모르는 항목을 거절하면 빼고 다시 보낸다
    if first_party and model.startswith(FALLBACK_MODELS):
        shapes.insert(0, ('beta', dict(rich, betas=['server-side-fallback-2026-07-01'], fallbacks='default')))
    memo = (conf.get('provider'), conf.get('base_url') or '', model, len(shapes))
    msg, start = None, min(_WORKS.get(memo, 0), len(shapes) - 1)
    for i in range(start, len(shapes)):
        how, params = shapes[i]
        try:
            api = client.beta.messages if how == 'beta' else client.messages
            with api.stream(**params) as stream:
                msg = stream.get_final_message()
            _WORKS[memo] = i
            break
        except (anthropic.BadRequestError, TypeError) as e:
            text = str(getattr(e, 'message', e))
            if isinstance(e, TypeError) and re.search(r'authentication|api_key|auth_token', text):      # 키도 환경 변수도 로그인 정보도 없다
                raise AIError('auth', 'API 키가 없습니다. 설정의 "AI 연결"에 키를 넣거나 환경 변수 ANTHROPIC_API_KEY 를 두세요.', 401)
            if re.search(r'too long|too large|context (window|length)|maximum.*tokens', text, re.I):
                raise AIError('prompt_too_large', 'AI 에 보낼 구조 정보가 모델이 받는 길이를 넘었습니다.', 413)
            if i == len(shapes) - 1:
                raise AIError('bad_request', f'요청이 거절되었습니다: {text[:300]}', 400)
        except anthropic.AuthenticationError:
            raise AIError('auth', 'API 키가 맞지 않습니다.', 401)
        except anthropic.PermissionDeniedError:
            raise AIError('auth', '이 키로는 이 모델을 쓸 수 없습니다.', 403)
        except anthropic.NotFoundError:
            raise AIError('not_found', f'모델 이름({model})이나 주소를 확인해 주세요.', 404)
        except anthropic.RateLimitError:
            raise AIError('rate_limited', 'AI 호출 한도에 걸렸습니다. 잠시 뒤 다시 시도해 주세요.', 429)
        except anthropic.APIStatusError as e:
            raise AIError('unavailable', f'AI 서버가 오류를 돌려주었습니다 ({e.status_code}). 잠시 뒤 다시 시도해 주세요.', 502)
        except anthropic.APIConnectionError:
            raise AIError('network', 'AI 서버에 닿지 않습니다. 네트워크나 프록시 설정을 확인해 주세요.', 502)
    if msg.stop_reason == 'refusal':          # 거절된 답은 중간까지 나온 글이 있어도 쓰지 않는다
        raise AIError('refused', 'AI 가 이 입력에는 답하지 않았습니다. 표현을 바꿔 보세요.', 422)
    text = ''.join(b.text for b in msg.content if b.type == 'text')
    if not text.strip():
        raise AIError('empty', 'AI 가 빈 답을 돌려주었습니다.', 502)
    return dict(text=text, model=msg.model, truncated=msg.stop_reason == 'max_tokens',
                usage=dict(input=getattr(msg.usage, 'input_tokens', 0), output=getattr(msg.usage, 'output_tokens', 0)))


# ───────────────────────────── OpenAI 호환 엔드포인트 (표준 라이브러리) ─────────────────────────────
def _openai(conf, prompt, tier):
    base, model, key = str(conf.get('base_url') or '').strip(), pick_model(conf, tier), api_key(conf)
    url = base if '/chat/completions' in base else base.rstrip('/') + '/chat/completions'
    headers = {'Content-Type': 'application/json', 'Accept': 'application/json'}
    if key:
        headers.update({'api-key': key} if conf.get('auth') == 'api-key' else {'Authorization': 'Bearer ' + key})
    req = urllib.request.Request(url, data=json.dumps(dict(model=model, messages=[{'role': 'user', 'content': prompt}])).encode(), headers=headers, method='POST')
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            d = json.loads(r.read().decode('utf-8', 'replace'))
    except urllib.error.HTTPError as e:
        try:
            err = json.loads(e.read(4000).decode('utf-8', 'replace')).get('error')
            detail = str(err.get('message') if isinstance(err, dict) else err or '')[:300]
        except Exception:
            detail = ''
        if e.code in (401, 403):
            raise AIError('auth', '키가 맞지 않거나 이 모델을 쓸 권한이 없습니다.', e.code)
        if e.code == 404:
            raise AIError('not_found', f'주소나 모델 이름({model})을 확인해 주세요.', 404)
        if e.code == 429:
            raise AIError('rate_limited', 'AI 호출 한도에 걸렸습니다. 잠시 뒤 다시 시도해 주세요.', 429)
        if e.code == 413 or re.search(r'context|too long|too large|maximum.*tokens', detail, re.I):
            raise AIError('prompt_too_large', 'AI 에 보낼 구조 정보가 모델이 받는 길이를 넘었습니다. 설정의 "한 번에 보내는 크기"를 줄여 보세요.', 413)
        raise AIError('bad_request' if e.code < 500 else 'unavailable', f'엔드포인트가 오류를 돌려주었습니다 ({e.code}){": " + detail if detail else ""}', e.code if e.code < 500 else 502)
    except (urllib.error.URLError, socket.timeout, TimeoutError, ConnectionError) as e:
        raise AIError('network', f'엔드포인트에 닿지 않습니다. 주소와 네트워크를 확인해 주세요. ({getattr(e, "reason", e)})', 502)
    except ValueError:
        raise AIError('empty', '엔드포인트의 답을 읽지 못했습니다(JSON 이 아님). 주소가 …/chat/completions 형식을 받는지 확인해 주세요.', 502)
    choice = (d.get('choices') or [{}])[0] if isinstance(d, dict) else {}
    content = (choice.get('message') or {}).get('content')
    if isinstance(content, list):             # 조각 목록으로 주는 서버도 있다
        content = ''.join(str(p.get('text') or '') for p in content if isinstance(p, dict))
    if choice.get('finish_reason') == 'content_filter':
        raise AIError('refused', 'AI 가 이 입력에는 답하지 않았습니다. 표현을 바꿔 보세요.', 422)
    if not str(content or '').strip():
        raise AIError('empty', 'AI 가 빈 답을 돌려주었습니다.', 502)
    usage = d.get('usage') or {}
    return dict(text=str(content), model=str(d.get('model') or model), truncated=choice.get('finish_reason') == 'length',
                usage=dict(input=usage.get('prompt_tokens', 0), output=usage.get('completion_tokens', 0)))
