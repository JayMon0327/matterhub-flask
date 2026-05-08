---
name: llm-intent-bridge
description: 일반 매터허브 디바이스에 LLM(기본 ollama) + 정규식 의도 분기 통합 클라이언트(slm.py)를 설치한다. SLM 서버 없이 단일 파이썬 파일로 "의도→matterhub-api 호출 + 고정 응답" / "미매칭→LLM 통과"를 모두 처리한다. 효돌 디바이스가 아닌 일반 Pi 허브에서 사용. "/llm-intent-bridge" 또는 "허브 LLM 연동", "ollama 의도 분기", "허브 음성 제어" 시 사용. 효돌 디바이스에 추가/수정할 때는 `slm-intent-control` 스킬 사용.
---

# LLM 의도 브리지 (일반 매터허브용)

매터허브가 설치된 일반 디바이스(Raspberry Pi / Ubuntu)에 사용자가 ollama 같은 LLM 데몬을 따로 설치한 뒤, 자연어 발화로 디바이스 제어 + 일반 대화까지 한 통로에서 처리하게 하는 통합 클라이언트.

효돌 디바이스의 `slm-intent-control` 스킬을 일반화한 버전이다. 차이는 다음과 같다.

| 항목 | `slm-intent-control` (효돌 디바이스) | `llm-intent-bridge` (일반 허브) |
|------|---------------------------------------|----------------------------------|
| 전제 SLM 서버 | 효돌이 만들어 둔 `slm-server-V2.py`(:8001) 기존 사용 | **없음** — `slm.py` 단일 파일이 모든 걸 처리 |
| 의도 분기 위치 | `slm-server-V2.py` `chat_stream()` 안 | `slm.py` `handle()` 안 |
| 응답 형식 | SSE (`/chat-stream`) | stdout 그대로 |
| 시연 인터페이스 | `~/slm.sh` (curl + python parser) | `python3 slm.py` 인터랙티브 |
| 의존성 | docker + 효돌 컨테이너 | python3 + requests + ollama 데몬 |

## 구조

```
사용자 발화 (stdin)
   ↓
slm.py
   ├─ 정규식 / 키워드 매칭
   │     매칭됨  →  matter.py.<func>(idx)  →  POST :8100/local/api/devices/<eid>/command
   │              →  고정 응답 출력 (LLM 우회)
   │
   └─ 매칭 안 됨  →  POST 127.0.0.1:11434/api/chat (ollama)
                  →  응답 출력
```

LLM 데몬은 OpenAI 호환 API를 노출하는 어떤 것이든 가능 (ollama, llama.cpp, vLLM, 외부 API 키 등). 기본은 ollama.

## 사전 조건

| 항목 | 확인 명령 |
|------|----------|
| matterhub-api 가동 (`:8100`) | `systemctl is-active matterhub-api` 또는 `curl http://127.0.0.1:8100/local/api/states` |
| python3 + requests | `python3 -c "import requests"` |
| LLM 데몬 가동 | ollama 기본: `curl http://127.0.0.1:11434/api/tags` |
| 모델 pull 완료 | `ollama list` 또는 사용 모델명 확인 |

LLM이 아직 없다면 사용자에게 ollama 설치를 안내하고, 모델은 디바이스 사양에 맞게 추천 (Pi5 8GB 권장: `qwen2.5:1.5b`, `llama3.2:1b`). 설치 자체는 본 스킬 범위 밖 — 사용자가 끝낸 뒤 진행한다.

## 설치 절차

### Step 1: 디바이스에 등록된 entity 조회

`slm-intent-control` Step 1과 동일 — `light/switch/cover/fan` 도메인 추리기:

```bash
curl -s http://127.0.0.1:8100/local/api/states | python3 -c '
import json, sys
for e in json.load(sys.stdin):
    eid = e["entity_id"]
    if eid.split(".",1)[0] in ("light","switch","cover","fan"):
        print(f"{e[\"state\"]:>6}  {eid:55s}  {e[\"attributes\"].get(\"friendly_name\",\"\")}")
'
```

domain별 service 매핑:

| domain | turn_on/open | turn_off/close | stop |
|--------|--------------|----------------|------|
| light, switch, fan | `turn_on` | `turn_off` | — |
| cover | `open_cover` | `close_cover` | `stop_cover` |

### Step 2: `~/llm-bridge/matter.py` 작성

`slm-intent-control`과 같은 형식. 디바이스 entity에 맞게 그룹/함수 정의:

```python
"""LLM Intent Bridge - matterhub-api 호출 모듈."""
import requests

API_BASE = 'http://127.0.0.1:8100/local/api'

# 사용자 디바이스에 맞게 수정 ↓
DEMO_SWITCHES = (
    'switch.<eid_1>',
    'switch.<eid_2>',
)
DEMO_CURTAINS = (
    'cover.<eid>',
)

def _post(entity_id, domain, service):
    return requests.post(
        f'{API_BASE}/devices/{entity_id}/command',
        json={'domain': domain, 'service': service},
        timeout=3,
    ).status_code

def _switch_targets(idx):
    if idx is None: return DEMO_SWITCHES
    if isinstance(idx, int) and 1 <= idx <= len(DEMO_SWITCHES):
        return (DEMO_SWITCHES[idx - 1],)
    return ()

def turn_on(idx=None):  return [(e, _post(e, 'switch', 'turn_on'))  for e in _switch_targets(idx)]
def turn_off(idx=None): return [(e, _post(e, 'switch', 'turn_off')) for e in _switch_targets(idx)]
def open_curtain():     return [(e, _post(e, 'cover',  'open_cover'))  for e in DEMO_CURTAINS]
def close_curtain():    return [(e, _post(e, 'cover',  'close_cover')) for e in DEMO_CURTAINS]
def stop_curtain():     return [(e, _post(e, 'cover',  'stop_cover'))  for e in DEMO_CURTAINS]
```

### Step 3: `~/llm-bridge/slm.py` 작성 (의도 분기 + LLM 통과)

```python
#!/usr/bin/env python3
"""LLM Intent Bridge — 의도 매칭 시 matter 호출, 미매칭 시 LLM 통과."""
import argparse
import json
import os
import re
import sys
import requests

import matter

OLLAMA_URL    = os.environ.get('OLLAMA_URL', 'http://127.0.0.1:11434/api/chat')
OLLAMA_MODEL  = os.environ.get('OLLAMA_MODEL', 'qwen2.5:1.5b')
SYSTEM_PROMPT = os.environ.get('SYSTEM_PROMPT', '너는 친절한 도우미야. 짧고 간결하게 한두 문장으로 답해.')

ON_KW  = ('불 켜', '불켜', '켜줘', '켜요', '불을 켜', '켜라', '조명 켜', '조명켜', '어둡', '어두워', '어두운')
OFF_KW = ('불 꺼', '불꺼', '꺼줘', '꺼요', '불을 꺼', '꺼라', '조명 꺼', '조명꺼', '너무 밝', '눈부', '밝네')
CURTAIN_OPEN_KW  = ('열', '올려', '걷어', '젖')
CURTAIN_CLOSE_KW = ('닫', '내려', '쳐줘', '치워')
CURTAIN_STOP_KW  = ('멈', '정지', '스톱', '스탑', '그만')


def detect_idx(text):
    m = re.search(r'([1-3])\s*번', text)
    if m:
        return int(m.group(1))
    for k, v in (('첫',1),('하나',1),('일번',1),('둘째',2),('이번',2),('셋째',3),('삼번',3)):
        if k in text:
            return v
    return None


def detect_intent(text):
    """Return (action, idx) or (None, None)."""
    if '커튼' in text or '블라인드' in text:
        if any(k in text for k in CURTAIN_STOP_KW):  return ('curtain_stop', None)
        if any(k in text for k in CURTAIN_CLOSE_KW): return ('curtain_close', None)
        if any(k in text for k in CURTAIN_OPEN_KW):  return ('curtain_open', None)
        return (None, None)
    idx = detect_idx(text)
    if any(k in text for k in OFF_KW): return ('off', idx)
    if any(k in text for k in ON_KW):  return ('on',  idx)
    return (None, None)


def execute(action, idx):
    if action == 'on':            return matter.turn_on(idx)
    if action == 'off':           return matter.turn_off(idx)
    if action == 'curtain_open':  return matter.open_curtain()
    if action == 'curtain_close': return matter.close_curtain()
    if action == 'curtain_stop':  return matter.stop_curtain()
    return None


def canned(action, idx):
    if action == 'curtain_open':  return '네! 커튼을 열어드렸어요~'
    if action == 'curtain_close': return '네! 커튼을 닫아드렸어요~'
    if action == 'curtain_stop':  return '네! 커튼을 멈춰드렸어요~'
    if idx is None:
        return '네! 불을 켜드렸어요~' if action == 'on' else '네! 불을 꺼드렸어요~'
    return f'네! {idx}번 스위치를 켜드렸어요~' if action == 'on' else f'네! {idx}번 스위치를 꺼드렸어요~'


def llm_chat(text):
    try:
        r = requests.post(OLLAMA_URL, json={
            'model': OLLAMA_MODEL,
            'messages': [
                {'role': 'system', 'content': SYSTEM_PROMPT},
                {'role': 'user',   'content': text},
            ],
            'stream': False,
        }, timeout=60)
        r.raise_for_status()
        return r.json().get('message', {}).get('content', '').strip()
    except Exception as e:
        return f'(LLM 오류: {e})'


def handle(text):
    action, idx = detect_intent(text)
    if action:
        result = execute(action, idx)
        print(f'[intent] {action} idx={idx} -> {result}', file=sys.stderr)
        return canned(action, idx)
    return llm_chat(text)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--once', action='store_true', help='stdin에서 한 줄 읽고 처리 후 종료')
    args = ap.parse_args()
    if args.once:
        line = sys.stdin.readline().strip()
        if line:
            print(handle(line))
        return
    print(f'LLM Intent Bridge — model={OLLAMA_MODEL}, exit: Ctrl-D 또는 /bye')
    while True:
        try:
            q = input('> ').strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not q: continue
        if q in ('/bye', '/quit', '/exit'): break
        print(' ', handle(q))


if __name__ == '__main__':
    main()
```

### Step 4: 실행 권한 + 동작 확인

```bash
mkdir -p ~/llm-bridge
# matter.py, slm.py 위 내용으로 작성
chmod +x ~/llm-bridge/slm.py

cd ~/llm-bridge

# 단발 테스트 (의도 매칭)
echo '불 켜줘'   | python3 slm.py --once
echo '커튼 열어줘' | python3 slm.py --once

# 단발 테스트 (LLM 통과)
echo '안녕'     | python3 slm.py --once

# 인터랙티브
python3 slm.py
```

### Step 5 (선택): systemd로 상시 가동

만약 외부 접근 가능한 HTTP API로 노출하고 싶다면 Flask wrapper 또는 FastAPI로 감싸 systemd 등록. 그게 아니면 SSH 후 인터랙티브로 충분.

## 사용법

| 형태 | 명령 |
|------|------|
| 인터랙티브 | `python3 ~/llm-bridge/slm.py` |
| 단발 (파이프 입력) | `echo "불 켜줘" \| python3 ~/llm-bridge/slm.py --once` |
| 모델 변경 | `OLLAMA_MODEL=llama3.2:1b python3 slm.py` |
| 외부 LLM (URL 변경) | `OLLAMA_URL=http://other-host:11434/api/chat python3 slm.py` |

## 새 entity 추가

`slm-intent-control` 스킬의 Step 2~3과 동일하지만 더 단순:
1. `matter.py`에 그룹/함수 추가
2. `slm.py`의 `*_KW` 튜플 + `detect_intent()` 분기 + `execute()` + `canned()` 4곳에 새 액션 추가
3. (재시작 불필요 — 인터랙티브 종료 후 다시 실행하면 반영)

## 차이점/주의점 vs `slm-intent-control`

- 컨테이너 재시작 없음. 효돌의 `docker restart slm-server`가 필요 없어 반복 수정이 빠름
- LLM 응답 텍스트 후처리(`perfect_clean`)가 없음. 모델이 토큰 누수가 심하면 `llm_chat()` 안에 정규식 후처리 한 줄 추가하는 게 가장 간단:
  ```python
  text = re.sub(r'<\|[^|]*\|>', '', text).strip()
  ```
- ollama API의 `messages` 배열에 history를 누적하면 멀티턴 대화도 가능. 본 기본 스크립트는 단발 호출만 (시연용 안정성 우선)
- `:8001` 같은 포트 노출 안 함. 외부에서 부르려면 직접 SSH 후 인터랙티브 또는 별도 wrapper 필요
- ollama 외 LLM (vLLM, llama.cpp의 OpenAI 호환 모드 등) 사용 시 payload 구조가 다를 수 있음. `llm_chat()` 한 함수만 갈아끼우면 됨

## 검증

`slm-intent-control`과 같은 7개 발화 시나리오로 회귀 검증:
1. "불 켜줘" → "네! 불을 켜드렸어요~" + 모든 switch on
2. "1번 켜줘" → "네! 1번 스위치를 켜드렸어요~" + switch_1만 on
3. "조명 꺼줘" → "네! 불을 꺼드렸어요~" + 모든 switch off
4. "커튼 열어줘" → "네! 커튼을 열어드렸어요~" + cover opening
5. "커튼 멈춰줘" → "네! 커튼을 멈춰드렸어요~"
6. "안녕" → LLM 응답 (모델별 다름)
7. "오늘 날씨 어때" → LLM 응답

stderr에 `[intent] <action> idx=<n> -> <result>` 라인이 명령 발화에서만 찍히는 것까지 확인.

## 관련 파일 / 참조

- `slm-intent-control` 스킬 — 효돌 디바이스 전용 (이미 SLM 서버 있을 때)
- matterhub-flask `app.py` 라우트:
  - `app.py:179` `POST /local/api/devices/<entity_id>/command`
  - `app.py:151` `GET /local/api/states`
  - `app.py:216` `GET/POST/PUT/DELETE /local/api/devices`
