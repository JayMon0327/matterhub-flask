# 자연어 음성 디바이스 제어 (SLM Intent Control)

> 효돌 SLM 디바이스(15103, 192.168.1.15)에 의도 분기를 얹어 한국어 발화 한 줄로 조명/커튼을 제어할 수 있게 만든 데모. **디바이스 제어와 LLM 응답이 독립 트랙**으로 동작하므로 LLM이 답변을 망쳐도 제어는 정확히 수행됨.

## 1. 아키텍처 구조

### 1-1. 프로세스 매니저 계층 (systemd가 루트)

```
                    [Edge Device — Jetson Orin / 192.168.1.15]
                                       │
                                  systemd (init)
                                       │
        ┌──────────────────────────────┼────────────────────────────────┐
        │                              │                                │
   docker.service              ollama.service                  pm2-hyodol.service
   (호스트 데몬)              (호스트 LLM 데몬,                  (PM2 daemon
        │                       127.0.0.1:11434)                      복원기)
        │                                                              │
   Docker Daemon                                                  PM2 Daemon
        │                                                              │
   ┌────┼─────┐                                                  ┌─────┼─────────┐
   ▼    ▼     ▼                                                  ▼     ▼         ▼
slm-   matter- homeassistant_                                  check  heartbeat  mqtt-
server server  core                                            (.py)  (spy.py)   api
                                                                                 (.py)

   + matterhub-* 6개 서비스 (systemd 직접, 위 트리와 별개)
        matterhub-api / matterhub-mqtt / matterhub-rule-engine /
        matterhub-notifier / matterhub-update-agent / matterhub-support-tunnel
```

> **PM2와 Docker는 별개**입니다. PM2가 도커 컨테이너를 띄우지 않고, 둘 다 systemd가 부모로서 나란히 부트스트랩하는 다른 프로세스 매니저입니다 (PM2: 호스트 OS 위 Python/Node 스크립트, Docker: 격리된 컨테이너).

### 1-2. 도메인 분리 (효돌 vs 와츠매터)

한 디바이스에 두 시스템이 공존합니다. 외부 클라우드 채널도 분리되어 있고, 이번 작업은 **두 도메인 사이에 첫 번째 직접 연결을 만든 것**입니다.

```
┌──────────────────────────  Edge Device 한 대 안에 두 도메인 공존  ──────────────────────────┐
│                                                                                              │
│  ┌──── 효돌 (Hyodol) 도메인 ────────┐      ┌── 와츠매터 (WhatsMatter / MatterHub) 도메인 ──┐  │
│  │                                  │      │                                                │  │
│  │  [Docker]                        │      │  [Docker]                                      │  │
│  │   └─ slm-server (Flask :8001)    │      │   ├─ matter-server                             │  │
│  │      slm-server-V2.py            │      │   └─ homeassistant_core (:8123)                │  │
│  │      (의도분기 ★ 우리가 추가)    │      │                                                │  │
│  │                                  │      │  [systemd]                                     │  │
│  │  [systemd]                       │      │   ├─ matterhub-api (:8100) ◀──────┐            │  │
│  │   └─ ollama.service              │      │   ├─ matterhub-mqtt (AWS IoT)     │            │  │
│  │      :11434, hyodol:latest       │      │   ├─ matterhub-rule-engine        │            │  │
│  │      (Llama3 8B FT, 4.6GB)       │      │   ├─ matterhub-notifier           │            │  │
│  │                                  │      │   ├─ matterhub-update-agent       │            │  │
│  │  [PM2 — pm2-hyodol.service]      │      │   └─ matterhub-support-tunnel     │            │  │
│  │   ├─ check (10s polling)         │      │                                   │            │  │
│  │   ├─ heartbeat (alive)           │      │  [코드]                           │            │  │
│  │   └─ mqtt-api (효돌 클라우드     │      │   /home/whatsmatter/...           │            │  │
│  │       MQTT 라우터)               │      │   matterhub-flask repo 배포본     │            │  │
│  │                                  │      │                                   │            │  │
│  │  [코드]                          │      │  [외부 채널]                      │            │  │
│  │   /home/hyodol/Hyodol/...        │      │   AWS IoT Core                    │            │  │
│  │   ├─ slm-server-V2.py            │      │   (Konai vendor / matterhub_id)   │            │  │
│  │   ├─ matter.py    ★ 우리가 추가  │      │                                   │            │  │
│  │   └─ check/spy/mqtt-server.py    │      └───────────────────────────────────┼────────────┘  │
│  │   /home/hyodol/slm.sh ★ 우리 추가│                                          │                │
│  │                                  │                                          │                │
│  │  [외부 채널]                     │                                          │                │
│  │   b.hyodolms.com                 │                                          │                │
│  │   (효돌 클라우드 MQTT)           │                                          │                │
│  │                                  │                                          │                │
│  └─────────────────┬────────────────┘                                          │                │
│                    │                                                           │                │
│                    │ matter.py.turn_on() / open_curtain() / stop_curtain() ... │                │
│                    │ POST http://127.0.0.1:8100/local/api/devices/<eid>/command│                │
│                    └───────────────────────────────────────────────────────────┘                │
│                          ★ 이번 작업으로 새로 만든 도메인 간 연결 ★                              │
│                                                                                                  │
└──────────────────────────────────────────────────────────────────────────────────────────────────┘
```

| 도메인 | 사용하는 프로세스 매니저 | 외부 클라우드 |
|---|---|---|
| **효돌(Hyodol)** | Docker(slm-server) + 호스트 systemd(ollama) + PM2(check/heartbeat/mqtt-api) | `b.hyodolms.com` MQTT |
| **와츠매터(MatterHub)** | Docker(matter-server, homeassistant_core) + 호스트 systemd(matterhub-* 6개) | AWS IoT Core (Konai vendor) |

### 1-3. 사용자 발화 처리 흐름 (요약)

```
사용자 → ~/slm.sh → POST /chat-stream?new (slm-server :8001)
   │
   slm-server-V2.py 의 [demo] 분기
   ├─ 의도 매칭 ────→ matter.py ──→ matterhub-api(:8100) ──→ HA(:8123) ──→ 실제 디바이스
   │                  └→ 고정 SSE 응답 즉시 반환 (LLM 우회)
   │
   └─ 미매칭   ────→ ollama(:11434) ──→ hyodol:latest 추론 ──→ SSE 스트림
```

### 1-4. 핵심 컴포넌트

| 컴포넌트 | 도메인 | 형태 | 역할 |
|---|---|---|---|
| `slm-server-V2.py` | 효돌 | Docker (host network) | Flask :8001, 의도 분기 + LLM 호출 |
| `matter.py` ★ | 효돌→와츠매터 다리 | Python 모듈 | matterhub-api 호출하는 단일 파일 (이번 작업) |
| `matterhub-api.service` | 와츠매터 | systemd | HA REST proxy (:8100) |
| `homeassistant_core` | 와츠매터 | Docker | Matter/Wi-Fi 어댑터 + 실제 디바이스 제어 |
| `ollama.service` | 효돌 | 호스트 systemd | LLM 추론 엔진 (모델 별도 4.6GB) |
| `~/slm.sh` ★ | 효돌 | bash 스크립트 | 인터랙티브 시연 셸 (이번 작업) |

> **설계 의도**: 의도 매칭 트랙과 LLM 트랙을 완전히 분리. 시연 안정성을 위해 명령은 LLM을 우회하고, 일반 대화만 LLM 사용.

## 2. 시퀀스

### A. "불 켜줘" — 의도 매칭 트랙

```
사용자                    ~/slm.sh           slm-server-V2.py        matter.py        matterhub-api(:8100)        HA(:8123)         디바이스
  │  "불 켜줘"               │                      │                     │                    │                      │                  │
  ├──────────────────────────▶                     │                     │                    │                      │                  │
  │                          │  POST /chat-stream?new                    │                    │                      │                  │
  │                          ├──────────────────────▶                    │                    │                      │                  │
  │                          │                      │                    │                    │                      │                  │
  │                          │       ?new → mysession 리셋                                     │                      │                  │
  │                          │       정규식 매칭: "불 켜" → on_kw                              │                      │                  │
  │                          │                      ├───── turn_on(idx=None) ─────▶            │                      │                  │
  │                          │                      │                    │  POST /devices/<eid>/command (×3)          │                  │
  │                          │                      │                    ├─────────────────────▶                      │                  │
  │                          │                      │                    │                    │   POST /api/services/switch/turn_on      │
  │                          │                      │                    │                    ├──────────────────────▶                   │
  │                          │                      │                    │                    │                      │  switch ON       │
  │                          │                      │                    │                    │                      ├─────────────────▶│
  │                          │                      │   고정 SSE 즉시 yield (★ LLM 호출 없음 ★)                                          │
  │                          │  data: "네! 불을 켜드렸어요~"                                                                              │
  │                          ◀──────────────────────┤                                                                                    │
  │   "네! 불을 켜드렸어요~"  │                                                                                                            │
  ◀──────────────────────────┤                                                                                                            │
```

### B. "안녕" — LLM 통과 트랙

```
사용자        slm-server-V2.py        ollama(:11434)
  │  "안녕"        │                       │
  ├────────────────▶                       │
  │                │  의도 미매칭 → RAG/일반대화 분기          
  │                ├─── ollama.chat(model='hyodol:latest', ...) ───▶
  │                │                       │  Llama 3 8B 추론
  │                ◀───────────────────────┤  스트리밍 토큰
  │   "안녕하세요!  │  perfect_clean() 후처리                    
  │   오늘도..."   │  SSE chunk yield                           
  ◀────────────────┤                       
```

### 시퀀스 핵심 포인트

| 항목 | 설명 |
|---|---|
| **두 트랙은 독립** | 의도 매칭되면 LLM 호출 자체를 안 함. 매칭 안 되면 LLM만 동작 |
| **`?new` 쿼리** | `mysession`(글로벌)을 매번 리셋해서 이전 발화 컨텍스트가 섞이지 않게 |
| **고정 응답** | 의도 매칭 시 항상 동일 문구. 시연 안정성 확보 |
| **응답 시간** | 의도 매칭: ≤ 1s / LLM: 2~10s (모델·문장 길이 따라) |

## 3. AI 모델 관련 사용 설명

### 모델 vs 런타임 — 분리 개념

| 영역 | 모델 (LLM 본체) | 런타임 (서빙 도구) |
|---|---|---|
| 비유 | 영화 파일 (`inception.mp4`) | 미디어 플레이어 (VLC) |
| 우리 디바이스 | **`hyodol:latest`** (Llama 3 8B 기반 fine-tune, Q4_K_M 양자화, 4.6GB) | **`ollama` v0.9.5** (호스트 systemd, OpenAI 호환 API @ :11434) |

> "LLM 뭐 썼어요?"에 대한 정확한 답: **"Llama 3 8B를 fine-tune한 hyodol 모델, ollama로 서빙"**

### 런타임 비교 (참고)

| 런타임 | 특징 | 적합 환경 |
|---|---|---|
| **ollama** ✅ | 단순함·이식성, 모델 갈아끼우기 쉬움 | 엣지/1인 데모 (지금 우리 케이스) |
| llama.cpp | C++ 단일 바이너리, 메모리 최강 | 초경량 |
| vLLM | continuous batching + PagedAttention으로 throughput ×수십 | 데이터센터 GPU, 동시 수백 요청 |
| OpenAI / Anthropic API | 호스팅 (자체 GPU 불필요) | 클라우드 의존 가능 |

### 우리 디바이스의 ollama 구성 (특이점)

```
slm-server 컨테이너 (host network)        호스트 (Jetson)
  ├─ pip install ollama (Python client)     ├─ /usr/local/bin/ollama (데몬)
  └─ slm-server-V2.py                       │     └─ :11434 OpenAI 호환 API
        └─ ollama.chat(...)                 └─ /usr/share/ollama/.ollama/models/
              └─ POST 127.0.0.1:11434 ─────────▶ (4.6GB hyodol:latest)
```

- **컨테이너에 모델 가중치 없음** — 호스트 ollama 데몬이 보관·서빙
- **host network** 덕분에 컨테이너 안 Python의 `127.0.0.1:11434`가 호스트 데몬에 닿음
- 표준은 docker-compose로 ollama 컨테이너+앱 컨테이너 분리지만, 효돌은 모델 교체 편의·다른 앱과 ollama 공유 목적으로 호스트 설치 선택
- 결과: **ollama.service가 죽으면 일반 대화 트랙은 실패하지만, 의도 매칭(디바이스 제어)은 정상 동작** (matterhub-api만 부르므로)

### 컨테이너 사이즈

| 항목 | 크기 |
|---|---|
| `slm-server` 컨테이너 (총) | **11.3GB** |
| ㄴ 베이스 이미지 `dustynv/pytorch:2.6-r36.4.0-cu128-24.04` | 7.92GB (Jetson PyTorch + CUDA 12.8) |
| ㄴ pip 추가 (langchain, sentence-transformers, chromadb, ollama client 등) | 3.34GB |
| ㄴ 모델 가중치 | **0 byte** (호스트에 별도) |
| 호스트 ollama 모델 디렉토리 | 4.6GB |

## 4. 실 사용 시나리오

### 4-1. 시연 시작

```bash
ssh hyodol@192.168.1.15        # pw: tech8123
bash ~/slm.sh
```

```
효돌 SLM 시연 셸 (URL=http://127.0.0.1:8001/chat-stream?new)
종료: Ctrl-D 또는 /bye

> 불 켜줘
  네! 불을 켜드렸어요~
```

### 4-2. 인식되는 발화 — 빠른 표

| 의도 | 발화 예시 | 응답 | 동작 |
|---|---|---|---|
| 조명 전체 켜기 | "불 켜줘", "조명 켜줘", "좀 어둡네" | "네! 불을 켜드렸어요~" | switch 1·2·3 ON |
| 조명 전체 끄기 | "불 꺼줘", "조명 꺼줘", "너무 밝네", "눈부셔" | "네! 불을 꺼드렸어요~" | switch 1·2·3 OFF |
| 조명 N번 켜기 | "1번 켜줘", "둘째 켜줘", "삼번 켜라" | "네! N번 스위치를 켜드렸어요~" | switch_N만 ON |
| 조명 N번 끄기 | "2번 꺼줘", "첫 번째 꺼줘" | "네! N번 스위치를 꺼드렸어요~" | switch_N만 OFF |
| 커튼 열기 | "커튼 열어줘", "커튼 올려줘", "블라인드 걷어줘" | "네! 커튼을 열어드렸어요~" | cover open |
| 커튼 닫기 | "커튼 닫아줘", "커튼 내려줘", "커튼 쳐줘" | "네! 커튼을 닫아드렸어요~" | cover close |
| 커튼 정지 | "커튼 멈춰줘", "커튼 정지", "커튼 스톱" | "네! 커튼을 멈춰드렸어요~" | cover stop |
| **그 외 (일반 대화)** | "안녕", "오늘 기분 어때", "배고파" | LLM 응답 (매번 다름) | 디바이스 변화 없음 |

### 4-3. 우선순위

```
사용자 발화
  │
  ├─ "커튼"/"블라인드" 포함?
  │     └─ stop > close > open  (멈/정지/스톱 > 닫/내려/쳐줘 > 열/올려/걷어)
  │
  ├─ 조명 끄기(off_kw) 매칭?  → turn_off(idx)
  ├─ 조명 켜기(on_kw)  매칭?  → turn_on(idx)
  │
  └─ 어디에도 안 걸리면        → LLM (일반 대화)
```

### 4-4. 시연 데모 추천 발화 흐름

```
> 안녕                       (인사 — LLM 응답으로 캐릭터성 보여주기)
> 좀 어둡네                  (간접 표현 — 자연스럽게 조명 켜짐)
> 너무 밝네                  (반대도 동작)
> 1번 스위치 켜줘            (개별 제어)
> 커튼 열어줘                (다른 도메인)
> 커튼 멈춰줘                (정지)
> 오늘 기분 어때             (다시 LLM 응답으로 자연스럽게 마무리)
> /bye
```

## 부록: 운영 정보

| 항목 | 값 |
|---|---|
| 디바이스 | 효돌 15103, 192.168.1.15 (hyodol / tech8123) |
| 자동 복원 | 호스트 재부팅 시 docker.service + ollama.service + matterhub-* + PM2 + 컨테이너 모두 자동 시작. 단 SLM 모델 로딩 60~90초 소요 |
| 변경 파일 | `/home/hyodol/Hyodol/matter.py` (신규), `/home/hyodol/Hyodol/slm-server-V2.py` (분기 추가), `/home/hyodol/slm.sh` (신규) |
| 백업 | `/home/hyodol/Hyodol/slm-server-V2.py.demo-backup-2026-05-08` |
| 롤백 | `cp <백업> /home/hyodol/Hyodol/slm-server-V2.py && sudo docker restart slm-server` |
| 관련 스킬 | `.claude/skills/slm-intent-control/` (효돌용), `.claude/skills/llm-intent-bridge/` (일반 허브용) |
| 상세 기록 | [`docs/learn/20260508-slm-intent-control.md`](learn/20260508-slm-intent-control.md) |
