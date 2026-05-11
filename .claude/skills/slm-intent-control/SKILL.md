---
name: slm-intent-control
description: 효돌 SLM(slm-server-V2.py)에 디바이스 제어 의도 분기를 추가/수정한다. "불 켜줘"/"1번 꺼줘"/"조명 켜줘" 같은 발화를 정규식으로 감지하고 matterhub-api(:8100)를 호출한 뒤, LLM을 우회하고 고정 SSE 응답을 즉시 반환한다. "/slm-intent-control" 또는 "SLM 의도 제어", "효돌 기기 추가", "조명 추가", "matter.py 수정" 시 사용.
---

# SLM 의도 분기 제어 (matter.py + slm-server-V2.py 패치)

효돌 SLM(`slm-server-V2.py`)이 사용자 발화를 받았을 때, 정규식으로 디바이스 제어 의도를 식별해서 matterhub-api를 호출하고, **LLM을 우회한 고정 SSE 응답**을 돌려주는 구조. 효돌 인형(안드로이드)의 STT→정규식→API+고정응답 패턴과 동일.

새 entity나 그룹(커튼, 다른 라이트 등)을 추가할 때마다 이 스킬을 사용한다.

## 구조

```
"불 켜줘" / "1번 켜줘"  ─→  slm-server-V2.py /chat-stream
                       ─→  on_kw / off_kw 매칭 + idx 정규식
                       ─→  matter.py.turn_on(idx)/turn_off(idx)
                            ─→  POST :8100/local/api/devices/<eid>/command
                       ─→  고정 SSE yield (LLM 우회) → 셸/클라이언트
```

핵심 파일 두 개:
- `/home/hyodol/Hyodol/matter.py` — entity 그룹 정의 + `turn_on(idx)/turn_off(idx)`
- `/home/hyodol/Hyodol/slm-server-V2.py` — `chat_stream()` 안의 `[demo]` 분기 블록

## 사전 조건

| 항목 | 비고 |
|------|------|
| `slm-server` 컨테이너 가동 (`docker ps`) | host network, :8001 노출 |
| `matterhub-api.service` 가동 (`systemctl is-active matterhub-api`) | :8100 노출 |
| `/Hyodol` 호스트 마운트 (`/home/hyodol/Hyodol → /Hyodol`) | bind mount |
| ollama @ 호스트(`127.0.0.1:11434`) 정상 + `hyodol:latest` 존재 | LLM 통과 발화용 |
| 백업본 존재 — `slm-server-V2.py.demo-backup-<date>` | 롤백용 |

이 스킬을 처음 적용하는 디바이스라면 백업부터:
```bash
cp /home/hyodol/Hyodol/slm-server-V2.py /home/hyodol/Hyodol/slm-server-V2.py.demo-backup-$(date +%F)
```

## 새 기기/그룹 추가 절차

### Step 1: 실제 entity_id와 domain 확인

```bash
# light/switch/cover 만 추리기
curl -s http://127.0.0.1:8100/local/api/states | python3 -c '
import json, sys
for e in json.load(sys.stdin):
    eid = e["entity_id"]
    if eid.split(".",1)[0] in ("light","switch","cover","fan"):
        print(f"{e[\"state\"]:>6}  {eid:55s}  {e[\"attributes\"].get(\"friendly_name\",\"\")}")
'
```

domain별 service 매핑 (matter.py `_command()`에 사용):

| domain | turn_on | turn_off |
|--------|---------|----------|
| light, switch, fan | `turn_on` | `turn_off` |
| cover (커튼/블라인드) | `open_cover` | `close_cover` |

### Step 2: matter.py에 그룹/entity 추가

`/home/hyodol/Hyodol/matter.py`의 패턴:

```python
API_BASE = 'http://127.0.0.1:8100/local/api'

DEMO_SWITCHES = (
    'switch.zemismart_wifi_smart_switch_switch_1',
    'switch.zemismart_wifi_smart_switch_switch_2',
    'switch.zemismart_wifi_smart_switch_switch_3',
)

def _command(entity_id, service):
    r = requests.post(
        f'{API_BASE}/devices/{entity_id}/command',
        json={'domain': 'switch', 'service': service},  # ← domain은 entity_id 앞부분
        timeout=3,
    )
    return r.status_code

def _targets(idx):
    if idx is None: return DEMO_SWITCHES
    if isinstance(idx, int) and 1 <= idx <= len(DEMO_SWITCHES):
        return (DEMO_SWITCHES[idx - 1],)
    return ()

def turn_on(idx=None):  return [(eid, _command(eid, 'turn_on'))  for eid in _targets(idx)]
def turn_off(idx=None): return [(eid, _command(eid, 'turn_off')) for eid in _targets(idx)]
```

**그룹 1개 추가 (예: 커튼)** — 별도 그룹 함수로 분리하는 게 깔끔하다:

```python
DEMO_CURTAINS = ('cover.zemismart_smart_curtain_cover',)

def _curtain_cmd(entity_id, service):
    return requests.post(
        f'{API_BASE}/devices/{entity_id}/command',
        json={'domain': 'cover', 'service': service},
        timeout=3,
    ).status_code

def open_curtain():  return [(e, _curtain_cmd(e, 'open_cover'))  for e in DEMO_CURTAINS]
def close_curtain(): return [(e, _curtain_cmd(e, 'close_cover')) for e in DEMO_CURTAINS]
```

### Step 3: slm-server-V2.py의 `[demo]` 분기에 키워드 + 매핑 추가

분기 위치는 `chat_stream()` 안 `if not user_input: return ...` 직후. 마커:
```python
        return jsonify({"error": "messages not provided"}), 400
```
바로 다음 줄부터 `# [demo] intent match -> matter.py call + canned SSE` 블록.

기존 패턴:

```python
on_kw  = ("불 켜", "불켜", "켜줘", "켜요", "불을 켜", "켜라", "조명 켜", "조명켜",
          "어둡", "어두워", "어두운")
off_kw = ("불 꺼", "불꺼", "꺼줘", "꺼요", "불을 꺼", "꺼라", "조명 꺼", "조명꺼",
          "너무 밝", "눈부", "밝네")
_idx = None
_m = _re.search(r"([1-3])\s*번", user_input)
if _m: _idx = int(_m.group(1))
else:
    for _k, _v in (("첫",1),("하나",1),("일번",1),("둘째",2),("이번",2),("셋째",3),("삼번",3)):
        if _k in user_input: _idx = _v; break
```

**새 그룹/액션 추가 시 체크리스트:**
1. 새 키워드 튜플 추가 (예: `open_kw = ("커튼 열", "커튼열어", "열어줘")`)
2. 매칭 분기 추가:
   ```python
   elif any(k in user_input for k in open_kw):
       _action = "curtain_open"
       _matter_demo.open_curtain()
   ```
3. 고정 응답 문구 매핑:
   ```python
   if _action == "curtain_open": _msg = "네! 커튼을 열어드렸어요~"
   elif _action == "curtain_close": _msg = "네! 커튼을 닫아드렸어요~"
   elif _action == "on" and _idx is None: _msg = "네! 불을 켜드렸어요~"
   elif _action == "on": _msg = f"네! {_idx}번 스위치를 켜드렸어요~"
   ...
   ```
4. **off 키워드를 on보다 먼저** 검사할 것 ("너무 밝네"의 "밝"이 on 키워드에 잡히지 않게).
5. 새 키워드는 **다른 키워드의 부분집합이 되지 않게** 짤 것 (예: "켜"만 단독 두면 다른 단어에 잡힘 — `"켜줘"`, `"켜라"` 처럼 어말 포함).
6. **DEMO_SWITCHES가 3개를 초과하면 idx 인식 범위도 같이 늘릴 것**: 정규식 `r"([1-3])\s*번"`과 한글 단어 매핑 (`첫/하나/일번/둘째/이번/셋째/삼번`) 둘 다. 안 늘리면 "4번 켜줘" 같은 발화가 idx=None으로 떨어져 전체 켜기로 잘못 매칭되거나 LLM으로 흘러가는 silent 누락이 발생.

**escape 안전한 패치 방법** — 직접 SSH heredoc으로 python 코드를 보내면 `\n` escape가 깨지기 쉬우니, **로컬에 패치 스크립트(.py)를 만들고 `scp`로 전송 후 디바이스에서 실행**한다:

```bash
# 로컬에서
cat > /tmp/intent_patch.py <<'EOF'
import re, sys
path = '/home/hyodol/Hyodol/slm-server-V2.py'
src = open(path).read()
# ... 마커 기반 replace ...
open(path, 'w').write(src)
print('patched')
EOF
sshpass -p tech8123 scp /tmp/intent_patch.py hyodol@192.168.1.15:/tmp/
sshpass -p tech8123 ssh hyodol@192.168.1.15 "python3 /tmp/intent_patch.py"
```

기존 `[demo]` 블록을 통째로 교체하는 정규식 (idempotent):
```python
existing = re.compile(
    r'\n    # \[(?:demo|matter demo)\][\s\S]*?print\(f"\[matter demo\] call failed: \{_e\}"\)\n',
    re.M,
)
src = existing.sub('\n', src)
# 그 다음 새 블록을 intent_marker 뒤에 삽입
```

### Step 4: 컨테이너 재시작 + ready 대기

```bash
sudo docker restart slm-server
# 모델 로딩 대기 (보통 60~90초)
for i in $(seq 1 48); do
  curl -s --max-time 2 http://127.0.0.1:8001/memory | grep -q 'models_loaded.*true' && \
    echo "ready ~$((i*5))s" && break
  sleep 5
done
```

### Step 5: 검증

reset 후 발화 → 상태 확인:

```bash
# 발화 (?new로 매번 fresh session)
payload=$(python3 -c 'import json,sys; print(json.dumps({"user_input": sys.argv[1]}, ensure_ascii=False))' '커튼 열어줘')
curl -sN -X POST 'http://127.0.0.1:8001/chat-stream?new' \
  -H 'Content-Type: application/json' -d "$payload"

# 응답 기대값:
# data: {"type": "sentence", "data": "네! 커튼을 열어드렸어요~"}
# data: {"type": "end", "data": "<END>"}

# 상태 확인 (entity_id 본인 것으로)
curl -s http://127.0.0.1:8100/local/api/states/cover.zemismart_smart_curtain_cover \
  | python3 -c 'import json,sys; print(json.load(sys.stdin)["state"])'
# 기대: open / closed / on / off

# 서버 로그
sudo docker logs --tail 50 slm-server | grep -E '\[matter demo\]'
# 기대: [matter demo] canned: 네! 커튼을 열어드렸어요~
```

LLM 통과 발화 ("안녕")도 함께 검증:
```bash
curl -sN -X POST 'http://127.0.0.1:8001/chat-stream?new' \
  -H 'Content-Type: application/json' -d '{"user_input":"안녕"}' | head -4
# matter demo 로그에 안 찍혀야 정상 (LLM 흐름 그대로)
```

## 시연용 인터랙티브 셸 — `~/slm.sh`

```bash
#!/bin/bash
URL="${SLM_URL:-http://127.0.0.1:8001/chat-stream?new}"   # ← ?new 필수
echo "효돌 SLM 시연 셸 (URL=$URL) — 종료: Ctrl-D 또는 /bye"
while :; do
  if ! IFS= read -er -p '> ' q; then echo; break; fi
  case "$q" in /bye|/quit|/exit) break ;; '') continue ;; esac
  payload=$(python3 -c 'import json,sys; print(json.dumps({"user_input": sys.argv[1]}, ensure_ascii=False))' "$q")
  curl -sN --max-time 60 -X POST "$URL" -H 'Content-Type: application/json' -d "$payload" \
  | python3 -c 'import sys, json
for line in sys.stdin:
    if not line.startswith("data: "): continue
    try: d = json.loads(line[6:])
    except: continue
    if d.get("type") == "sentence": print("  " + d.get("data", ""), flush=True)
    elif d.get("type") == "end": break'
  echo
done
```

`?new` 없이 호출하면 글로벌 `mysession`에 누적되어 일반 대화 응답 품질이 떨어진다 — **반드시 `?new` 유지**.

## 알려진 주의점

- **모델 토큰 누수 (LLM 통과 시)**: `hyodol:latest`는 Llama3 8B Q4 fine-tune. chat template 잔여물(`<|reserved_special_token_*|>`, `;` 잔류, `PostalCodesNL`, `TokenNameIdentifier` 등)이 가끔 흘러나옴. `perfect_clean()`이 일부만 잡음. 명령 발화는 LLM을 우회하므로 영향 없음.
- **명령은 항상 LLM 우회**: `_action is not None` 이면 `Response(_canned(), mimetype="text/event-stream")`로 즉시 return. mysession에도 안 쌓임.
- **`mysession`은 글로벌**: 같은 서버 인스턴스의 모든 클라이언트가 default 세션을 공유한다. `?new` 안 붙이면 다른 사용자 발화가 섞일 수 있음.
- **컨테이너 안에 ollama 없음**: ollama는 호스트 설치 (`/usr/local/bin/ollama`). `docker exec slm-server ollama`는 실패. host network라서 컨테이너 안 Python이 `127.0.0.1:11434`로 호스트 ollama 호출.
- **ollama CLI(`ollama run hyodol`)는 우리 분기를 안 탐**: chat-stream 안 의도 분기를 우회하므로 디바이스 제어 안 됨. 시연은 무조건 `/chat-stream` 또는 `~/slm.sh` 사용.

## 롤백

```bash
# 가장 가까운 backup 사용
ls -lt /home/hyodol/Hyodol/slm-server-V2.py.demo-backup-* | head
cp /home/hyodol/Hyodol/slm-server-V2.py.demo-backup-<date> /home/hyodol/Hyodol/slm-server-V2.py
sudo docker restart slm-server
```

matter.py만 되돌리려면 git 추적이 없으므로 직접 편집하거나 백업해 두고 작업할 것.

## 참고: matterhub-flask 라우트

`matter.py`에서 호출하는 4개 엔드포인트가 모두 `app.py`에 정의되어 있다:

| matter.py 호출 | matterhub-flask 라우트 |
|----------------|------------------------|
| `GET states/<eid>` | `app.py:173` `@app.route('/local/api/states/<entity_id>')` |
| `GET devices` | `app.py:216` `@app.route('/local/api/devices', ...)` |
| `POST devices/<eid>/command` | `app.py:179` `@app.route('/local/api/devices/<entity_id>/command', methods=["POST"])` |
| `PUT schedules` | `app.py:245` `@app.route('/local/api/schedules', ...)` |
