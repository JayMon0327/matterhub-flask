# 업데이트 파이프라인 전면 개편 — 트러블슈팅 보고서

**작성일:** 2026-03-23
**기간:** 2026-03-21 ~ 2026-03-23
**관련 커밋:** 19개 (`b7bfc10` ~ `1109f52`)

---

## Phase 1: PM2→systemd 마이그레이션 인프라 (6커밋)

### 문제 발생 상황

기존 PM2 기반 서비스 관리에서 다수의 구조적 한계가 드러났다:

- PM2 환경에서 `sudo` 실행 불가 — `update_server.sh`가 dpkg/systemctl을 호출할 수 없음
- PM2의 cgroup 안에서 `systemctl restart`를 호출하면 **자기 자신이 kill**됨
- venv 경로가 장비마다 달라 서비스 시작 실패
- `update_server.sh` 경로 탐색이 구형 레이아웃/`.deb` 설치를 커버하지 못함

### 원인

PM2는 Node.js 프로세스 매니저로, Linux systemd 서비스 관리와 근본적으로 다른 프로세스 모델을 사용한다. cgroup 격리, 권한 상승(sudo), 서비스 의존성 관리 등 시스템 수준 기능이 부재했다.

### 해결

| 커밋 | 내용 |
|------|------|
| `b7bfc10` | `update_server.sh` 경로 탐색 보완 — 구형 레이아웃 + `.deb` 설치 경로 대응 |
| `46a4cfe` | PM2→systemd 완전 마이그레이션 로직 보강 |
| `4d3cfa8` | nohup 환경 sudo 실패 대응 + `sub/` sys.path 보정 |
| `3ccd402` | `has_sudo`를 `systemctl` 기준으로 변경 + venv fallback |
| `3984431` | PM2 cgroup 탈출(`systemd-run --scope`) + 안전한 systemd 마이그레이션 순서 |
| `e4800eb` | systemd 케이스에서도 유닛 재렌더링 추가 |

**핵심 기법:**
- `systemd-run --scope`로 PM2 cgroup을 탈출한 후 systemd 마이그레이션 실행
- `has_sudo` 판정을 `which pm2` → `systemctl is-active` 기준으로 전환
- venv 경로 탐색 시 여러 후보(`/opt/matterhub/venv`, `~/venv`, `~/.venv`)를 순회하는 fallback 적용

---

## Phase 2: 자동 부트스트랩 + 일괄 배포 (2커밋)

### 문제 발생 상황

신규/기존 장비에 업데이트를 배포하려면 다음을 **수동으로** 설정해야 했다:

- AWS IoT 인증서 심링크
- `.env` 파일에 MQTT 관련 변수 추가
- sudoers NOPASSWD 설정
- 릴레이 경유 장비에 대한 일괄 배포 수단 부재

### 원인

초기 설계 시 단일 장비 수동 관리를 전제했으며, 다수 장비 운영 시나리오를 고려하지 않았다.

### 해결

| 커밋 | 내용 |
|------|------|
| `4738772` | `auto_bootstrap()` — cert 심링크, MQTT env, NOPASSWD sudoers 자동 설정. state-changed 구독 추가 |
| `ae46de7` | MQTT_ENDPOINT 자동탐지 버그 수정 → 기본값 고정 |

**핵심 기법:**
- `auto_bootstrap()` 함수가 최초 실행 시 인증서 심링크, `.env` MQTT 변수, sudoers를 자동 구성
- `bulk_initial_deploy.sh`로 릴레이 SSH 터널을 경유한 일괄 배포 지원

---

## Phase 3: MQTT 엔드포인트 + 인증서 통일 (2커밋)

### 문제 발생 상황

- Konai 벤더 하드코딩된 엔드포인트/인증서 경로가 남아있어 혼란
- 장비별 `.env`에 `MQTT_ENDPOINT`, 인증서 경로를 수동 설정 필요
- `SUBSCRIBE_MATTERHUB_TOPICS` 기본값이 0이라 MQTT 명령 구독이 꺼져 있는 장비 존재

### 원인

벤더 분리 리팩토링(`providers/`) 이후에도 Konai 특화 기본값이 잔존해 있었다.

### 해결

| 커밋 | 내용 |
|------|------|
| `c1806fc` | Konai 기본값 제거 → `certificates/` + `whatsmatter-nipa` 엔드포인트로 통일 |
| `8d6653e` | 시작 시 cert 심링크 자동 생성 + `SUBSCRIBE_MATTERHUB_TOPICS` 기본값 1 |

---

## Phase 4: 원격 명령 확장 (3커밋)

### 문제 발생 상황

- `git_update` 단일 명령만 지원하여 운영 유연성 부족
- 응답 메시지의 `command` 필드가 `"git_update"`로 하드코딩 — `set_env` 등 다른 명령 응답이 잘못 보고됨
- 단일 장비 대상 토픽만 존재하여 전체/지역 일괄 명령 불가

### 원인

초기 구현이 단일 명령(`git_update`) 전용으로 설계되었으며, 확장을 고려하지 않았다.

### 해결

| 커밋 | 내용 |
|------|------|
| `37d2750` | 팬아웃 토픽(`all`/`region`) 추가 + 레거시 토픽 제거 + status 검증 강화 |
| `77db750` | `set_env` 원격 명령 추가 — 허용 키 화이트리스트 + restart 옵션 |
| `d5e7b64` | 응답 `command` 필드 동적화 — 수신 명령을 그대로 반환 |

**핵심 기법:**
- `set_env` 명령은 변경 가능한 키를 화이트리스트로 제한하여 보안 확보
- `restart` 옵션으로 설정 변경 후 즉시 서비스 재시작 가능

---

## Phase 5: 번들 관리 + 배포 안정화 (4커밋)

### 문제 발생 상황

- `.deb` 번들을 원격으로 배포할 수단이 없음
- PM2→systemd 마이그레이션을 위한 독립 스크립트 부재
- venv 생성 실패 시 부서진 디렉토리가 남아 후속 시도도 실패
- `update-agent`의 `CapabilityBoundingSet=`가 root 실행과 충돌

### 원인

배포 자동화가 `git_update`(git pull) 단일 경로에 의존했으며, 패키지 기반 배포 경로가 미구현이었다.

### 해결

| 커밋 | 내용 |
|------|------|
| `b02c66a` | `migrate_pm2_to_systemd.sh` 독립 마이그레이션 스크립트 추가 |
| `d1d28f2` | venv 생성 실패 시 부서진 venv 자동 삭제 |
| `6f6dc59` | `update-agent` CapabilityBoundingSet 분리 — root 서비스에서 capability 제한 제거 |
| `0713751` | `bundle_update`/`bundle_check` MQTT 명령 지원 — 원격 번들 배포 트리거 |

**핵심 기법:**
- `bundle_update`: S3 URL에서 `.deb` 다운로드 → `dpkg -i` 설치 → 결과 응답
- `bundle_check`: 현재 설치된 패키지 버전 조회 → 응답

---

## Phase 6: PUBACK 교착 해소 (2커밋)

### 문제 발생 상황

`set_env`, `bundle_update`, `bundle_check` 세 명령이 **100% PUBACK 10초 타임아웃**으로 실패했다. `git_update`만 정상 동작.

### 원인

awscrt SDK의 `EventLoopGroup(1)` 단일 이벤트 루프 스레드가 **MQTT 콜백 실행**과 **PUBACK 수신**을 모두 담당한다. 콜백 내에서 `publish().result()` 호출 시 PUBACK 수신을 위해 같은 스레드를 기다리므로 **교착(deadlock)** 발생.

`git_update`는 `subprocess` + `nohup`으로 외부 프로세스에서 응답을 발행하여 우연히 교착을 회피하고 있었다.

### 해결

| 커밋 | 내용 |
|------|------|
| `3a89ca2` | AWS IoT Thing 정책 업데이트 요청서 — update/response Publish 권한 추가 |
| `1109f52` | 모든 명령 핸들러를 큐 워커 스레드로 이동 — 콜백 즉시 반환 보장 |

**핵심 기법:**
- `queue.Queue` + 워커 스레드로 명령 처리를 콜백 밖으로 분리
- 콜백은 큐에 메시지를 넣고 즉시 반환 → 이벤트 루프 차단 없음
- 상세 분석은 [`puback-timeout-deadlock-fix-20260323.md`](puback-timeout-deadlock-fix-20260323.md) 참조

---

## 최종 수치 요약

| 항목 | 개편 전 | 개편 후 |
|------|---------|---------|
| 지원 원격 명령 | 1개 (`git_update`) | 4개 (`git_update`, `set_env`, `bundle_update`, `bundle_check`) |
| 서비스 관리 | PM2 | systemd (5개 유닛) |
| 수동 설정 항목 | ~8개 (cert, env, sudoers 등) | 0개 (`auto_bootstrap` 자동화) |
| PUBACK 타임아웃 | 3개 명령 100% 실패 | 0건 |
| 팬아웃 토픽 | 미지원 | `all` / `region` 지원 |
| 번들 원격 배포 | 불가 | `.deb` 다운로드+설치 자동화 |

---

## 변경 파일 요약

### 핵심 파일

| 파일 | 역할 |
|------|------|
| `mqtt_pkg/update.py` | 원격 명령 핸들러 (set_env, bundle_update, bundle_check 추가) |
| `mqtt_pkg/callbacks.py` | MQTT 콜백 → 큐 워커 분리 |
| `mqtt_pkg/runtime.py` | 연결 설정, cert 심링크 자동 생성 |
| `mqtt_pkg/settings.py` | 토픽/엔드포인트 기본값 통일 |
| `mqtt.py` | 큐 워커 스레드 시작, auto_bootstrap 호출 |
| `device_config/update_server.sh` | PM2→systemd 마이그레이션, 경로 탐색 보완 |
| `device_config/service_definitions.py` | systemd 유닛 정의 (CapabilityBoundingSet 분리) |
| `update_agent.py` | 업데이트 에이전트 (venv fallback, 유닛 재렌더링) |

### 신규 파일

| 파일 | 역할 |
|------|------|
| `device_config/migrate_pm2_to_systemd.sh` | PM2→systemd 독립 마이그레이션 스크립트 |
| `device_config/bulk_initial_deploy.sh` | 릴레이 경유 일괄 배포 스크립트 |
| `docs/operations/aws-iot-policy-request.md` | AWS IoT 정책 업데이트 요청서 |

### 테스트

| 파일 | 역할 |
|------|------|
| `tests/mqtt_pkg/test_update.py` | update.py 단위 테스트 |
| `tests/test_mqtt.py` | mqtt.py 통합 테스트 |
| `tests/test_update_agent.py` | update_agent.py 테스트 |
