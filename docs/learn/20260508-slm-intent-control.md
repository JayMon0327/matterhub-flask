# 2026-05-08 — 효돌 SLM 의도 분기 + 시연 셸 통합

## 배경

효돌 디바이스(15103, 192.168.1.15)에서 시연용으로 "불 켜줘"/"커튼 닫아줘" 같은 자연어 발화 → 디바이스 제어가 실시간으로 동작하도록 만들어야 했다. 사용자가 SLM에 텍스트로 직접 던져 응답까지 보고 싶다는 요청에서 출발.

처음엔 단순 ollama CLI 사용을 시도했으나, ollama CLI는 우리 분기를 우회하므로 디바이스 제어가 안 됨이 확인됐고, slm-server(`/chat-stream`) 흐름 안에 의도 분기를 추가하는 방향으로 전환.

## 발견 사항

### 효돌 SLM 디바이스 구조 (15103)
- **slm-server**는 도커 컨테이너 (`dustynv/pytorch:2.6-r36.4.0-cu128-24.04`), **host network 모드**, `:8001`에서 Flask `slm-server-V2.py` 실행
- **ollama는 컨테이너 안이 아니라 호스트에 설치** (`/usr/local/bin/ollama` v0.9.5). 컨테이너가 host network라 컨테이너 내부 Python이 `127.0.0.1:11434`로 호스트 ollama 호출
- 모델: `hyodol:latest` (architecture=`llama`, 8B Q4_K_M, **Llama 3 chat template**) — qwen 아님
- `/Hyodol`은 호스트 `/home/hyodol/Hyodol`의 bind mount

### 코드 분석에서 나온 핵심
- `slm-server-V2.py` `/chat-stream` 분기: RAG / 일반대화 두 가지뿐. **MatterHub 호출 코드 0건** ("불 켜줘"는 LLM 답변만 만들고 끝나는 게 정상)
- `matter-mqtt.py`(`/Hyodol/matter-mqtt.py`)는 `hyodol/command/<dev>/<cmd>` MQTT 토픽으로 외부 클라우드(`20.39.195.245:1883`)에서 명령 받아 matterhub-api 호출하는 어댑터인데, URL이 옛 환경(`http://192.168.0.3:8000/local/api/`)으로 박혀 있어 동작 안 함 + systemd/PM2 등록도 안 돼 있음
- 효돌 인형(안드로이드)은 SLM의 LLM 응답을 안 쓰고 STT→정규식→API+고정응답 패턴이라는 점이 사용자 설명에서 확인됨
- `mysession`이 **글로벌 변수**라 모든 클라이언트가 default 세션을 공유 → `?new` 안 붙이면 다른 발화가 누적되어 LLM 응답 품질이 망가짐. V2 클라이언트 주석에 `chat-stream?new` 가 단서로 남아 있었음

### matterhub-flask 라우트와 일치성
matter-mqtt.py가 부르는 4개 엔드포인트(`states`, `devices`, `devices/<id>/command`, `schedules`)가 우리 `app.py`에 모두 정의되어 있음 — URL prefix만 `127.0.0.1:8100/local/api/`로 맞추면 그대로 사용 가능.

## 변경

### 디바이스 측 (15103)
- **신규 `/home/hyodol/Hyodol/matter.py`** — matterhub-api 호출 단일 모듈. `DEMO_SWITCHES` (3개 switch) + `DEMO_CURTAINS` (1개 cover). `_post(eid, domain, service)`로 통일, `turn_on(idx)/turn_off(idx)/open_curtain()/close_curtain()/stop_curtain()`
- **`slm-server-V2.py` `[demo]` 분기 추가** — `chat_stream()` 안 user_input 검증 직후, 매칭되면 matter.py 호출 + 고정 SSE yield + 즉시 return (LLM 호출 자체 우회). 매칭 안 되면 기존 RAG/일반대화 흐름 유지
- **`~/slm.sh` 시연 셸** — `read -e` 인터랙티브, `?new`로 매번 fresh session, SSE 파싱 후 한 줄씩 출력
- 백업 파일 `slm-server-V2.py.demo-backup-2026-05-08` 보존

### 의도 → 응답 매핑 (최종)
| 분류 | 트리거 | 응답 |
|---|---|---|
| 조명 전체 켜기 | `불 켜`/`켜줘`/`조명 켜`/`어둡` 등 | "네! 불을 켜드렸어요~" |
| 조명 전체 끄기 | `불 꺼`/`꺼줘`/`조명 꺼`/`너무 밝`/`눈부` 등 | "네! 불을 꺼드렸어요~" |
| 조명 N번 켜기/끄기 | 위 + `1번/2번/3번` 또는 `첫/하나/일번/둘째/이번/셋째/삼번` | "네! N번 스위치를 켜/꺼드렸어요~" |
| 커튼 정지 | `커튼`(또는 `블라인드`) + `멈/정지/스톱/스탑/그만` | "네! 커튼을 멈춰드렸어요~" |
| 커튼 닫기 | 위 + `닫/내려/쳐줘/치워` | "네! 커튼을 닫아드렸어요~" |
| 커튼 열기 | 위 + `열/올려/걷어/젖` | "네! 커튼을 열어드렸어요~" |
| 그 외 | — | LLM (ollama hyodol:latest) |

분기 우선순위: 커튼/블라인드 단어 → stop > close > open. 커튼 키워드 없을 때 → off > on. 둘 다 안 걸리면 LLM.

## 운영상 지뢰 (실수에서 배운 점)

1. **SSH heredoc + Python heredoc 이중 escape**는 `\n` 같은 escape를 두 번 푼다. 결과적으로 `"\n\n"`이 실제 줄바꿈으로 박혀 SyntaxError. 한 번 패치하다 컨테이너가 안 떴고 백업 복원함. → **이후 모든 패치는 로컬에 `.py` 작성 후 `scp`로 전송하는 방식으로 전환**
2. heredoc 안 한국어 주석에 작은따옴표(`'불 켜/꺼'`)가 들어가면 Python 문자열이 깨짐. 영문 주석으로 회피
3. `mysession` 글로벌이라 `?new` 누락 시 LLM 답변에 이전 발화 컨텍스트가 섞여 망가지는 응답이 나옴 — 시연 셸엔 **반드시 `?new`**
4. 의도 매칭은 LLM 응답과 **완전 독립 트랙**으로 짜야 시연 안정성이 보장됨. LLM이 "감사해요~ CLIIIK" 같이 망가져도 디바이스는 정확히 동작
5. `on_kw`에 단일 문자(`"켜"`)는 절대 넣지 말 것 — 일반 단어 활용형에 false positive. 어미 포함(`"켜줘"`,`"켜라"`)으로
6. 커튼 분기에서 close/open 단일 문자 키워드(`"열"`,`"닫"`)는 매칭 폭이 넓지만, **상위 가드인 `"커튼"`/`"블라인드"` 단어 필수** 조건이 false positive를 막음
7. ollama CLI(`ollama run hyodol`)는 SLM 서버 흐름을 우회하므로 디바이스 제어 분기를 절대 안 탐. 시연은 무조건 `/chat-stream` 또는 `~/slm.sh` 사용
8. Llama 3 8B Q4 fine-tune 모델의 chat template 잔여물(`<|reserved_special_token_*|>`, `TokenNameIdentifier`, `PostalCodesNL`, `;`)은 후처리(`perfect_clean()`)로 일부만 잡음. 명령 발화에선 LLM 우회로 자동 회피. 일반 대화에선 여전히 일부 노출

## 산출물

- 디바이스 코드 변경: `/home/hyodol/Hyodol/{matter.py,slm-server-V2.py}`, `/home/hyodol/slm.sh`
- 백업: `/home/hyodol/Hyodol/slm-server-V2.py.demo-backup-2026-05-08`
- 신규 스킬:
  - [`slm-intent-control`](../../.claude/skills/slm-intent-control/SKILL.md) — 효돌 SLM 디바이스에 의도 분기 추가/수정
  - [`llm-intent-bridge`](../../.claude/skills/llm-intent-bridge/SKILL.md) — 일반 매터허브에 ollama + 의도 분기 통합 클라이언트 설치 (이 작업의 일반화 버전)
- 안내 문서: [`docs/slm-intent-overview.md`](../slm-intent-overview.md) — 4섹션 (아키텍처/시퀀스/AI 모델/실 사용 시나리오) 한 페이지 요약. 타인 안내·시연 자료용

## 효돌 디바이스 아키텍처 — 후속 파악

데모 작업 이후 사용자 질의로 확인한 시스템 구조. 이후 신규 디바이스 분석/대응 시 참고.

### 프로세스 인벤토리

| 계층 | 단위 | 역할 |
|---|---|---|
| 호스트 systemd | `docker.service` | 도커 데몬 (모든 컨테이너 부모) |
| 호스트 systemd | `ollama.service` (v0.9.5, `/usr/local/bin/ollama`) | LLM 추론 엔진. `127.0.0.1:11434` OpenAI 호환 API. `hyodol:latest` 모델 서빙 |
| 호스트 systemd | `pm2-hyodol.service` | hyodol 사용자 PM2 데몬 부팅 시 복원기 |
| MatterHub systemd | `matterhub-api.service` | Flask `:8100`, HA REST proxy. **우리 matter.py가 호출하는 그 서버** |
| MatterHub systemd | `matterhub-mqtt.service` | AWS IoT Core MQTT (vendor-neutral, default Konai) |
| MatterHub systemd | `matterhub-rule-engine`, `matterhub-notifier`, `matterhub-update-agent` | 룰 실행 / WebSocket→webhook / OTA |
| MatterHub systemd | `matterhub-support-tunnel` | reverse SSH (현재 inactive) |
| PM2 (효돌) | `check` (`Hyodol/check.py`) | 10초 polling — 효돌 클라우드 원격 명령 채널 |
| PM2 (효돌) | `heartbeat` (`Hyodol/spy.py`) | alive 신호 송신 |
| PM2 (효돌) | `mqtt-api` (`Hyodol/mqtt-server.py`) | 효돌 클라우드 MQTT 라우터 (`hyodol2edge/<port>/<cmd>` 구독, 59s heartbeat, 1s `device.one_sec`) |
| Docker | `slm-server` (host network) | Flask `:8001`, slm-server-V2.py. **restart=always** |
| Docker | `matter-server` | Matter 프로토콜 서버 (HA가 사용) |
| Docker | `homeassistant_core` | HA 본체 `:8123` |

### 부팅 시퀀스 (전원 ON 후 자동 복원)

```
docker.service ─┬─ slm-server (모델 로딩 60~90s)
                ├─ matter-server
                └─ homeassistant_core (30~60s)
ollama.service ─── hyodol:latest preload (slm-server가 11434 hit하면 메모리 적재)
matterhub-api.service ── :8100 ready
pm2-hyodol.service  ── check / heartbeat / mqtt-api 복원
```

ready 판정: `curl :8001/memory`에 `models_loaded: true`.

### 핵심 의존성 — ollama.service ≠ 잉여

- **컨테이너 안에는 ollama 바이너리 없음** (`docker exec slm-server ollama` not found)
- 컨테이너에 있는 건 `pip install ollama` Python 클라이언트뿐
- host network 덕에 컨테이너 안 Python의 `127.0.0.1:11434`가 호스트 ollama 데몬에 닿음
- ollama.service 중단 시:
  - 의도 매칭 트랙(matter.py 호출): **정상** (matterhub-api만 사용)
  - 일반 대화 트랙(LLM 통과): **실패** (ConnectionError / timeout)
- 표준 패턴은 docker-compose로 ollama 컨테이너 + 앱 컨테이너 분리. 효돌은 모델 교체 편의·다른 앱과 ollama 공유 목적으로 호스트 설치 선택 (`nvidia-container-toolkit`이 깔려 있어 GPU 패스스루는 가능했음에도 호스트 분리를 택함)

### slm-server 컨테이너 11.3GB 출처

| 항목 | 크기 | 비고 |
|---|---|---|
| 베이스 이미지 `dustynv/pytorch:2.6-r36.4.0-cu128-24.04` | 7.92GB | Jetson용 PyTorch + CUDA 12.8 + Ubuntu 24.04 |
| writable layer (pip 추가분) | 3.34GB | langchain, sentence-transformers, chromadb, transformers, ollama client 등 |
| **모델 가중치** | **0 byte** | 호스트 `/usr/share/ollama/.ollama/models/` 4.6GB 별도 보관 |

→ 9~11GB는 GPU 추론용 라이브러리 스택. 모델 분리 보관 구조라 컨테이너 자체엔 모델 미포함.

### LLM 개념 분리 (모델 vs 런타임)

| 영역 | 모델 (LLM 본체) | 런타임 (서빙 도구) |
|---|---|---|
| 비유 | 영화 파일 | 미디어 플레이어 |
| 우리 디바이스 | `hyodol:latest` (Llama 3 8B fine-tune, Q4_K_M) | ollama v0.9.5 (호스트 systemd) |

질문 "LLM 뭐 썼어요?"에 대한 정확한 답: **"Llama 3 8B fine-tune `hyodol`, ollama로 서빙"**. ollama 자체는 런타임이라 모델 답이 아님.

### ollama vs vLLM (참고용 정리)

|  | ollama | vLLM |
|---|---|---|
| 출처 | ollama Inc. (Go) | UC Berkeley 오픈소스 (Python) |
| 내부 | llama.cpp wrapper | 자체 CUDA kernel |
| GPU | CPU/GPU 둘 다 | NVIDIA GPU 필수 |
| 동시성 | 직렬 (queue) | continuous batching + PagedAttention으로 수백 동시 |
| 적합처 | 엣지/1인 데모 (← 우리 케이스) | 데이터센터 GPU, 동시 수백 요청 |

핵심 알고리즘 두 개:
- **Continuous batching**: 매 토큰 step마다 새 요청 합류·완료 가능. GPU forward pass 빈 cycle 없앰
- **PagedAttention**: KV cache를 OS 가상메모리처럼 페이지 단위로 관리. 메모리 단편화 줄이고 시스템 프롬프트 같은 공통 부분 공유

vLLM은 NVIDIA 제품이 아니라 오픈소스. 다만 NVIDIA GPU(CUDA) 위에서 동작. 효돌 Jetson 1인 디바이스는 ollama가 정답이고, vLLM은 throughput이 본전을 뽑는 데이터센터 환경용.

## 검증

| 발화 | 의도 분기 | 응답 | 디바이스 결과 |
|---|---|---|---|
| "불 켜줘" | turn_on idx=None | 네! 불을 켜드렸어요~ | switch 1·2·3 on |
| "1번 켜줘" | turn_on idx=1 | 네! 1번 스위치를 켜드렸어요~ | switch_1만 on |
| "조명 꺼줘" | turn_off idx=None | 네! 불을 꺼드렸어요~ | switch 1·2·3 off |
| "너무 밝네" | turn_off idx=None | 네! 불을 꺼드렸어요~ | switch 1·2·3 off |
| "커튼 열어줘" | open_curtain | 네! 커튼을 열어드렸어요~ | cover → opening |
| "커튼 닫아줘" | close_curtain | 네! 커튼을 닫아드렸어요~ | cover → closing |
| "커튼 멈춰줘" | stop_curtain | 네! 커튼을 멈춰드렸어요~ | stop_cover 200 |
| "안녕" | (LLM 통과) | "안녕하세요! 오늘도 함께할 수 있어 행복해요~" | (제어 없음) |

서버 로그에 `[matter demo] canned: ...` 라인이 명령 발화에서만 찍히는 것까지 확인.
