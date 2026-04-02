# MatterHub Flask .deb 설치 완료 보고서 (Konai 브랜치)

- **작업일**: 2026-04-01
- **빌드 브랜치**: `konai/20260211-v1.1` (커밋: `1e5dc5e`)
- **빌드 파일**: `matterhub_2026.04.01-1e5dc5e_arm64.deb`
- **작업자**: Claude Code 자동화

---

## 1. 대상 장비 및 결과

| 장비 | IP | matterhub_id | 터널 포트 | API | MQTT | 터널 |
|------|-----|-------------|-----------|-----|------|------|
| 2호기 | 192.168.1.96 | whatsmatter-nipa_SN-1775027020 | 22342 | 200 ✅ | 연결+구독+발행 ✅ | 정상 ✅ |
| 3호기 | 192.168.1.97 | whatsmatter-nipa_SN-1775028559 | 22343 | 200 ✅ | 연결+구독+bootstrap ✅ | 정상 ✅ |
| 4호기 | 192.168.1.101 | whatsmatter-nipa_SN-1775023879 | 22344 | 미설정 | Konai HANGUP | 정상 ✅ |

> 4호기(.101)는 HA 토큰 미설정 + MQTT 설정 미완 상태.

---

## 2. 설치 내용

각 장비에 아래 절차를 수행하였다 (`/matterhub-install` 스킬 기준):

1. `.deb` 전송 및 `dpkg -i` 설치
2. `python3-venv` + venv 생성 + `requirements.txt` pip 설치
3. `.pyc` 컴파일 → `.py` 삭제 (코드 보안)
4. `dpkg` 상태 수복 (`ii` 확인)
5. `.env` 생성 (Konai MQTT 설정 적용)
6. 소유권 수정 (`whatsmatter:whatsmatter`)
7. `systemd` 서비스 설정 (User 변경, API 우선 시작)
8. AWS IoT 프로비저닝 (matterhub_id 발급)
9. 인증서 심링크 생성
10. HA 토큰 설정
11. 리버스 SSH 터널 (relay 등록 포함)

---

## 3. 발견된 이슈 및 해결

### 3-1. `.env` 형식 — systemd EnvironmentFile 충돌 (Critical)

| 증상 | 원인 | 해결 |
|------|------|------|
| HA 401 Unauthorized (API 502) | `hass_token="eyJ..."` — systemd가 따옴표를 값에 포함 | `hass_token=eyJ...` (따옴표 없이) |
| HA_host 미인식 | `HA_host = "..."` — 등호 양쪽 공백 | `HA_host="..."` (공백 없이) |

**원인 분석**: systemd `EnvironmentFile`과 `python-dotenv`의 파싱 규칙이 다르다.
- systemd: 따옴표를 벗기지 않고 값에 포함. 공백 있는 키는 무시.
- python-dotenv: 따옴표를 자동으로 벗김. 공백 허용.
- 두 시스템이 동시에 `.env`를 읽으므로 **양쪽 모두 호환되는 형식** 필요.

### 3-2. MQTT UNEXPECTED_HANGUP — Konai 브로커 토픽 제한

| 증상 | 원인 | 해결 |
|------|------|------|
| 연결 성공 즉시 HANGUP 반복 | `SUBSCRIBE_MATTERHUB_TOPICS="1"`로 `matterhub/*` 토픽 구독 | `SUBSCRIBE_MATTERHUB_TOPICS="0"` |

**원인 분석**: Konai AWS IoT 정책이 `matterhub/*` 토픽 구독을 허용하지 않아, 구독 요청 시 브로커가 즉시 연결을 끊음.

### 3-3. 서비스 시작 순서 — MQTT bootstrap 실패

| 증상 | 원인 | 해결 |
|------|------|------|
| `❌ 코나이 bootstrap 실패: Connection refused` | MQTT가 API보다 먼저 시작 | API 먼저 시작 → 8초 대기 → MQTT 시작 |

### 3-4. 터널 port forwarding failed — 동일 SSH 키 충돌

| 증상 | 원인 | 해결 |
|------|------|------|
| `Error: remote port forwarding failed for listen port 22342` | 여러 장비가 동일 SSH 키 사용 → relay가 첫 번째 매칭 항목의 포트만 허용 | 장비별 고유 키 생성: `ssh-keygen -C 'matterhub-tunnel-<호스트>'` |

### 3-5. 재부팅 후 Permission denied

| 증상 | 원인 | 해결 |
|------|------|------|
| 서비스 시작 시 `.env` Permission denied | `matterhub-provision` 서비스가 root로 파일 소유권 변경 | 재부팅 후 `chown -R whatsmatter:whatsmatter /opt/matterhub/app/` 재실행 |

### 3-6. dpkg purge 후 /tmp 파일 소실

| 증상 | 원인 | 해결 |
|------|------|------|
| 재설치 시 `dpkg -i /tmp/matterhub_*.deb` 실패 | `dpkg --purge`가 `/tmp` 파일도 정리 | purge 후 .deb 재전송 |

---

## 4. 스킬 업데이트 사항

`/matterhub-install` 스킬(`SKILL.md`)에 아래 내용 반영:

- **사전 입력**: `GIT_BRANCH` 추가 — 브랜치별 MQTT 설정 자동 분기
- **브랜치별 환경 분기표**: master vs konai/* 별 `MQTT_CERT_PATH`, `MQTT_ENDPOINT`, `SUBSCRIBE_MATTERHUB_TOPICS` 값
- **`.env` 형식 규칙**: 공백 금지, hass_token 따옴표 금지
- **서비스 시작 순서**: API 우선 시작 + 8초 대기
- **알려진 이슈**: 6건 신규 추가 (HA 401, 공백 502, Konai HANGUP, bootstrap 실패, 터널 키 충돌, 재부팅 권한)
- **장비 대장**: 4호기 추가, 2·3호기 matterhub_id 업데이트

---

## 5. Relay 등록 현황

```
whatsmatter-nipa_SN-1775027020  22342  whatsmatter  (.96)
whatsmatter-nipa_SN-1775023879  22344  whatsmatter  (.101)
whatsmatter-nipa_SN-1775028559  22343  whatsmatter  (.97)
```

---

## 6. 잔여 작업

| 항목 | 장비 | 상태 |
|------|------|------|
| 4호기(.101) HA 토큰 설정 | 192.168.1.101 | 미완 |
| 4호기(.101) MQTT Konai 설정 확정 | 192.168.1.101 | `SUBSCRIBE_MATTERHUB_TOPICS="0"` 미적용 |
