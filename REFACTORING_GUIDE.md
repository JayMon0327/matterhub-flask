# MatterHub Flask 리팩토링 가이드

이 문서는 코드베이스 분석을 바탕으로 한 **디렉토리 역할**, **제거 후보 파일**, **디렉토리 배치 추천**을 정리한 리팩토링 가이드입니다.

---

## 1. 디렉토리별 역할

### 1.1 프로젝트 루트

| 파일/폴더 | 역할 |
|-----------|------|
| **app.py** | Flask 메인 앱. Home Assistant 프록시 API, devices/schedules/rules/rooms/notifications CRUD, 로그·히스토리 조회 API, 인증서·설정 API, webhook 등 제공. `sub`(scheduler, ruleEngine, collector, logs_api), `libs.edit` 사용. |
| **mqtt.py** | AWS IoT MQTT 클라이언트(**별도 프로세스**). Shadow 업데이트, HA 상태 동기화, 알림 감지·웹훅/IoT 발행, API 요청 처리, Git 업데이트 명령 처리. `matterhub.sh` 또는 PM2 `startup.json`으로 실행. |

---

### 1.2 `sub/` (서브 모듈)

| 파일 | 역할 |
|------|------|
| **scheduler.py** | 주기/일회 스케줄 실행. `resources/schedule.json`을 읽어 `schedule`로 주기 작업, `one_time_schedule`로 일회 작업 실행. `app.py`에서 import되어 사용. |
| **ruleEngine.py** | HA WebSocket으로 `state_changed` 구독, `resources/rules.json` 규칙에 따라 HA 서비스 호출. `rules_file_changed` 이벤트로 규칙 리로드. **별도 프로세스**로 실행. |
| **notifier.py** | HA WebSocket으로 `state_changed` 구독, `resources/notifications.json` 규칙에 따라 웹훅 알림. `notifications_file_changed`로 리로드. **별도 프로세스**로 실행. |
| **collector.py** | 상태 히스토리 수집. HA `/api/states`, `/api/history` 호출해 NDJSON(엣지 로그) 및 Period History JSON 저장. `app.py`에서 `start_collector()`로 스레드 기동. |
| **logs_api.py** | 로그·히스토리 조회 유틸. NDJSON·Period History 파일 읽기, 필터·페이지네이션. `app.py`의 `/local/api/logs`, `/local/api/history/period/*` 등에서 사용. |
| **configure.py** | `resources/devices.json`, `resources/roos.json`(오타)의 alias 변경 함수만 정의. **다른 코드에서 import/호출 없음.** |
| **localIp.py** | 로컬 IP 조회 후 `http://localhost:8000/matter?ip=...` 로 전송. **별도 프로세스**로 실행. |
| **mqtt.py** | AWS IoT 클라이언트의 이전/간소화 버전(엔드포인트·로직 상이). **실제로 실행되는 것은 루트 `mqtt.py`**이며, readme에서만 언급됨. |

---

### 1.3 `libs/`

| 파일 | 역할 |
|------|------|
| **edit.py** | 공통 유틸: `deleteItem`, `putItem`, `file_changed_request`, `update_env_file`. `app.py`, `mqtt.py`에서 사용. |

---

### 1.4 `lambda/`

| 파일 | 역할 |
|------|------|
| **http-to-mqtt.py** | HTTP → AWS IoT MQTT 발행 Lambda. 요청을 MQTT로 퍼블리시하고 응답 토픽 정보를 메시지에 넣음. |
| **mqtt-response-handler.py** | IoT Rule 등에서 MQTT 응답을 받아 DynamoDB에 저장하는 Lambda. 클라우드 쪽 MQTT 응답 수집용. |

---

### 1.5 `device_config/`

| 파일 | 역할 |
|------|------|
| **hass_install.sh** | Home Assistant 설치 스크립트. |
| **matterhub.service** | systemd 서비스 정의. |
| **matterhub.sh** | 앱·MQTT·ruleEngine·notifier·localIp 등 프로세스 기동 스크립트. (경로·`aws.py` 참조 등 루트와 상이) |
| **server_install.sh** | 서버 설치 스크립트. |
| **wifi_config.sh** | Wi‑Fi 설정 스크립트. |
| **wpa_supplicant.conf** | Wi‑Fi 인증 설정. |

---

### 1.6 `certificates/`

- AWS IoT 인증서 파일 보관. (실제 인증서는 보통 `.gitignore` 대상)

---

## 2. 제거/정리 후보 파일

### 2.1 삭제 후보 (미사용·중복)

| 파일 | 이유 |
|------|------|
| **sub/mqtt.py** | 실제로 동작하는 MQTT 프로세스는 루트 `mqtt.py`이며, `sub/mqtt.py`는 어디서도 import/실행되지 않음. readme 설명용으로만 등장. 기능은 루트 `mqtt.py`에 있음. |
| **sub/configure.py** | `change_entity_alias`, `change_room_alias`만 정의되어 있고, 다른 모듈에서 호출하지 않음. 경로도 `resources/roos.json` 오타, app은 env 기반 경로 사용. 미사용으로 보임. (나중에 API로 노출할 계획이 있으면 남겨두고 경로만 env로 맞출 수 있음.) |

### 2.2 샘플/문서용 (위치 정리 또는 삭제 검토)

| 파일 | 설명 |
|------|------|
| **notifications_hybrid_format.json** | 알림 포맷 예시/문서용. 런타임에서는 env의 `notifications_file_path`가 가리키는 파일만 사용. |
| **notifications_new_format.json** | 위와 동일. |
| **notifications_production.json** | 프로덕션용 샘플로 보임. 실제 동작은 env에 설정된 경로의 파일. 문서/배포 가이드용으로만 쓰는지 확인 후, 샘플 정책에 맞춰 유지·이동·삭제 결정. |

### 2.3 중복 정리

- **루트 vs device_config**  
  `hass_install.sh`, `matterhub.service`, `matterhub.sh`, `server_install.sh`, `wifi_config.sh`, `wpa_supplicant.conf`가 루트와 `device_config/`에 둘 다 있음.  
  **한쪽만 두고(예: `device_config/`만 유지), 루트는 `device_config/`를 참조하거나 심볼릭 링크로 정리**하는 것을 권장.

---

## 3. 디렉토리 배치 추천 (루트 파일 정리)

루트에 흩어져 있는 파일을 역할별로 묶어두는 추천입니다.

| 현재 위치(루트) | 추천 디렉토리 | 비고 |
|-----------------|----------------|------|
| app.py | **루트 유지** 또는 `src/` | 엔트리포인트. 루트 유지가 무난. 정리하고 싶으면 `src/app.py` 등으로 이동 후 실행 경로만 맞추면 됨. |
| mqtt.py | **루트 유지** 또는 `src/` | `app.py`와 동일한 정책 적용. |
| update_server.sh | **scripts/** 또는 **deploy/** | 배포/업데이트 전용. |
| hass_install.sh | **device_config/** 로 통합 | 루트 복사본 제거, `device_config/`만 유지. |
| server_install.sh | **device_config/** 또는 **scripts/** | 디바이스 설정이면 `device_config/`, 일반 서버 설정이면 `scripts/`. |
| wifi_config.sh, wpa_supplicant.conf | **device_config/** 로 통합 | 디바이스/와이파이 설정. |
| matterhub.service, matterhub.sh | **device_config/** 로 통합 | 서비스·실행 스크립트는 `device_config/`에만 두고, 배포 시 여기서 복사하도록 하면 정리됨. |
| startup.json | **config/** 또는 **deploy/** | PM2 설정. |
| API_SPEC.json | **docs/** | API 스펙 문서. |
| HISTORY_GUIDE.md | **docs/** | 가이드 문서. |
| notifications_*.json (샘플들) | **docs/samples/** 또는 **config/samples/** | 포맷/샘플용. |
| Dockerfile | **루트 유지** | 일반적인 관례. |
| requirements.txt | **루트 유지** | Python 프로젝트 관례. |
| .env.example | **루트** 또는 **config/** | 루트가 더 흔함. |

### 3.1 추천 루트 구조 요약

```
matterhub-flask/
├── app.py
├── mqtt.py
├── requirements.txt
├── Dockerfile
├── config/              # 선택: startup.json, env 예시 등
├── docs/                # API_SPEC.json, HISTORY_GUIDE.md, readme 보조
├── scripts/             # 또는 deploy/: update_server.sh 등
├── device_config/       # 디바이스용 스크립트·설정만 (루트 중복 제거)
├── sub/
├── libs/
├── lambda/
└── certificates/
```

---

## 4. 추가로 맞추면 좋은 점

### 4.1 경로 일관성 (resources vs env)

- **app.py**는 env의 `schedules_file_path`, `rules_file_path`, `notifications_file_path`를 사용.
- **sub/scheduler.py**, **sub/ruleEngine.py**, **sub/notifier.py**는 각각 `resources/schedule.json`, `resources/rules.json`, `resources/notifications.json`을 하드코딩.
- env 경로와 `resources/`가 다르면, 스케줄/규칙/알림이 서로 다른 파일을 참조하게 됨.

**권장:**  
`scheduler.py`, `ruleEngine.py`, `notifier.py`도 env(또는 공통 설정)에서 해당 파일 경로를 읽도록 변경하여 app과 동일한 경로를 쓰게 하면 유지보수와 배포가 쉬워집니다.

### 4.2 configure.py 오타

- `sub/configure.py`의 `room_file_path = "resources/roos.json"` → `rooms.json` 오타 수정이 필요합니다. (파일을 유지할 경우)

---

## 5. 리팩토링 체크리스트

- [ ] `sub/mqtt.py` 삭제 또는 readme에서만 참조로 명시
- [ ] `sub/configure.py` 삭제 또는 env 경로 적용 후 API로만 사용
- [ ] notifications 샘플 JSON → `docs/samples/` 또는 `config/samples/`로 이동/정리
- [ ] 루트의 device 관련 스크립트 → `device_config/`로 통합, 루트 중복 제거
- [ ] `API_SPEC.json`, `HISTORY_GUIDE.md` → `docs/`로 이동
- [ ] `startup.json` → `config/` 또는 `deploy/`로 이동
- [ ] `update_server.sh` → `scripts/` 또는 `deploy/`로 이동
- [ ] sub 모듈(scheduler, ruleEngine, notifier)의 JSON 경로를 env/공통 설정으로 통일

---

*이 가이드는 코드베이스 스냅샷 기준으로 작성되었습니다. 적용 전 배포 스크립트·PM2·systemd 경로를 한 번씩 확인하는 것을 권장합니다.*
