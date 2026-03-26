# 110대 허브 SSH 일괄 업데이트 완료 보고서

**작성일:** 2026-03-26
**작업 시간:** 2026-03-26 10:36 ~ 11:30 (약 1시간)
**관련 커밋:** `a19b7fe`, `f50fcfd`, `168e3f9`
**배포 브랜치:** master (`168e3f9`)

---

## 1. 배경

MQTT 업데이트 토픽을 통한 원격 업데이트는 릴레이 불안정성, git fetch DNS 타임아웃, SUDO_PASSWORD 미설정 등 리스크가 많아 **릴레이 SSH 경유 수동 일괄 배포** 방식으로 진행하였다.

**대상:** 릴레이(4.230.8.65) 포트 15001~15102 (102대)

---

## 2. 사전 작업

### 2-1. 코드 커밋 및 master 병합

| 커밋 | 내용 |
|------|------|
| `a19b7fe` | `_launch_restart` systemd 유닛 자동 설치 로직 추가 — 유닛 미설치 장비에서 render → install → enable 자동 수행 |
| `f50fcfd` | `bulk_initial_deploy.sh` 병렬 배치 + 재시도 + 결과 수집 기능 추가 |
| `168e3f9` | SSH quoting 버그 수정 + stash 정리 단계 추가 |

### 2-2. 스크립트 개선 (`bulk_initial_deploy.sh`)

기존 스크립트에서 대규모 배포를 위해 다음 기능을 추가:

- **하드코딩 포트 제거** → `device_ports.txt` 파일 전용
- **재시도 로직**: 접속 실패 시 최대 3회 재시도 (ConnectTimeout=10s)
- **병렬 배치**: `BATCH_SIZE=8`로 8대씩 동시 배포 (릴레이 과부하 방지)
- **git fetch 타임아웃**: `timeout 120` 래핑 (DNS 행 방지)
- **stash/untracked 정리**: git reset 전 `stash drop` + `checkout` + `clean` 수행
- **결과 구조화 수집**: `results.csv`, `offline.txt`, `failed.txt`, 포트별 `.log`
- **heredoc SSH**: 중첩 인용부호 충돌 해결

### 2-3. 릴레이 접속 확인

- PEM 키: `/tmp/hyodol-slm-server-key.pem` (PPK → PEM 변환 완료)
- 릴레이 접속 테스트: `relay-ok` 확인

---

## 3. 배포 과정

### 3-1. dry-run 전수 조사

102대 전체에 접속 테스트 (3회 재시도, ConnectTimeout=10s):

| 구분 | 대수 |
|------|------|
| 접속 가능 | 42대 |
| 접속 불가 | 60대 |

### 3-2. 그룹별 배포

접속 가능 42대를 4그룹으로 나누어 순차 배포:

| 그룹 | 포트 범위 | 대수 | 성공 | 실패 | 실패 원인 |
|------|-----------|------|------|------|-----------|
| A | 15004~15023 | 12 | 12 | 0 | |
| B | 15027~15050 | 12 | 11 | 1 | 15027: 디스크 용량 부족 |
| C | 15056~15078 | 6 | 6 | 0 | |
| D | 15083~15102 | 12 | 9 | 3 | 15083,15090,15096: 디스크 용량 부족 |
| **소계** | | **42** | **38** | **4** | |

### 3-3. 1차 실패 대응 — SSH quoting 버그

그룹 A 최초 실행 시 12대 전원 실패:

- **원인:** `device_ssh` 함수에서 단일인용부호(`'$*'`)로 명령을 감싸 중첩 인용부호가 깨짐 → sudoers 파일 미생성 → sudo 불가 → systemd 마이그레이션 건너뜀 → PM2 healthcheck 실패 → 롤백
- **해결:** heredoc 방식(`bash -s <<EOF`)으로 변경 (`168e3f9`)
- **결과:** 수정 후 11대 재배포 성공, 15004는 수동 배포로 선행 완료

### 3-4. 2차 실패 대응 — 디스크 용량 부족

4대(15027, 15083, 15090, 15096)에서 `No space left on device` 오류:

- **원인:** `/var/tmp`과 `/tmp`에 nvidia 유저가 생성한 숨김 디렉토리 ~24,000개 (약 200GB) 적재
- **해결:** `sudo rm -rf /var/tmp/.??* /tmp/.??*` + 로그 정리 → 444GB(100%) → 42GB(10%)
- **결과:** 15083, 15090, 15096 정리 후 배포 성공. 15027은 접속 불안정으로 미해결

### 3-5. 오프라인 장비 재시도

| 시도 | 조건 | 새로 접속 가능 |
|------|------|---------------|
| 1차 | 3회 재시도, 10s | 15097 → 배포 성공 |
| 2차 (15027 단독) | 5회, 10s | 접속 불가 |
| 3차 (60대 전체) | 5회, 8s | 0대 |

---

## 4. 디바이스별 실행 흐름

각 디바이스에서 수행된 작업:

| 단계 | 내용 | 타임아웃 |
|------|------|---------|
| 1 | 접속 테스트 (relay → device) | 10s + 15s |
| 2 | stash drop + checkout + clean | — |
| 3 | `timeout 120 git fetch origin master && git reset --hard` | 120s |
| 4 | NOPASSWD sudoers 설정 (없으면 자동 생성) | — |
| 5 | `update_server.sh master false bulk-update-... unknown` | 300s |
| 6 | 검증: commit, mqtt status, api status, matterhub_id 수집 | — |

`update_server.sh`가 수행한 핵심 작업:
- `.env` 백업 → git pull → `.env` 복원
- 자동 부트스트랩: 인증서 심링크, `.env` 변수 추가, MQTT vendor 설정
- **PM2 → systemd 마이그레이션**: 유닛 렌더링 → 설치 → enable → PM2 프로세스 삭제
- healthcheck 통과 확인 (30초 내 2개 이상 서비스 active)

---

## 5. 최종 결과

### 배포 현황

| 구분 | 대수 | 비율 |
|------|------|------|
| **배포 성공** | **42** | 41.2% |
| 오프라인 (미배포) | 60 | 58.8% |
| **합계** | **102** | 100% |

### 성공 장비 목록 (42대)

| 포트 | matterhub_id | mqtt | api |
|------|-------------|------|-----|
| 15004 | whatsmatter-nipa_SN-1752558407 | active | active |
| 15005 | whatsmatter-nipa_SN-1752555902 | active | active |
| 15007 | whatsmatter-nipa_SN-1752563020 | active | active |
| 15008 | whatsmatter-nipa_SN-1752561540 | active | active |
| 15009 | whatsmatter-nipa_SN-1752560699 | active | active |
| 15015 | whatsmatter-nipa_SN-1752560890 | active | active |
| 15016 | whatsmatter-nipa_SN-1752557449 | active | active |
| 15019 | whatsmatter-nipa_SN-1752563060 | active | active |
| 15020 | whatsmatter-nipa_SN-1752566194 | active | active |
| 15021 | whatsmatter-nipa_SN-1752560698 | active | active |
| 15022 | whatsmatter-nipa_SN-1752560684 | active | active |
| 15023 | whatsmatter-nipa_SN-1752557444 | active | active |
| 15029 | whatsmatter-nipa_SN-1752557192 | active | active |
| 15030 | whatsmatter-nipa_SN-1752555151 | active | active |
| 15036 | whatsmatter-nipa_SN-1752560871 | active | active |
| 15039 | whatsmatter-nipa_SN-1752559138 | active | active |
| 15042 | whatsmatter-nipa_SN-1752566199 | active | active |
| 15043 | whatsmatter-nipa_SN-1752566184 | active | active |
| 15044 | whatsmatter-nipa_SN-1752564475 | active | active |
| 15046 | whatsmatter-nipa_SN-1752564465 | active | active |
| 15047 | whatsmatter-nipa_SN-1752564471 | active | active |
| 15048 | whatsmatter-nipa_SN-1752564551 | active | active |
| 15050 | whatsmatter-nipa_SN-1752564581 | active | active |
| 15056 | whatsmatter-nipa_SN-1752566232 | active | active |
| 15057 | whatsmatter-nipa_SN-1752566201 | active | active |
| 15063 | whatsmatter-nipa_SN-1752567855 | active | active |
| 15065 | unknown | active | active |
| 15076 | whatsmatter-nipa_SN-1752647960 | active | active |
| 15078 | whatsmatter-nipa_SN-1752657992 | active | active |
| 15083 | unknown | active | active |
| 15084 | whatsmatter-nipa_SN-1752654214 | active | active |
| 15089 | whatsmatter-nipa_SN-1752661385 | active | active |
| 15090 | unknown | active | active |
| 15091 | whatsmatter-nipa_SN-1752661379 | active | active |
| 15093 | whatsmatter-nipa_SN-1752661822 | active | active |
| 15095 | whatsmatter-nipa_SN-1752660192 | active | active |
| 15096 | unknown | active | active |
| 15097 | whatsmatter-nipa_SN-1752659978 | active | active |
| 15098 | whatsmatter-nipa_SN-1752660010 | active | active |
| 15100 | whatsmatter-nipa_SN-1752660022 | active | active |
| 15101 | whatsmatter-nipa_SN-1755577146 | active | active |
| 15102 | whatsmatter-nipa_SN-1755502460 | active | active |

> **참고:** matterhub_id가 `unknown`인 4대(15065, 15083, 15090, 15096)는 AWS IoT 프로비저닝이 미완료 상태. 서비스는 정상 기동 중이나 MQTT 토픽 구독/발행이 불가하므로 별도 프로비저닝 필요.

### 오프라인 장비 목록 (60대)

3회 재시도(10s) + 5회 재시도(8s) 모두 접속 불가:

```
15001 15002 15003 15006 15010 15011 15012 15013 15014 15017
15018 15024 15025 15026 15027 15028 15031 15032 15033 15034
15035 15037 15038 15040 15041 15045 15049 15051 15052 15053
15054 15055 15058 15059 15060 15061 15062 15064 15066 15067
15068 15069 15070 15071 15072 15073 15074 15075 15077 15079
15080 15081 15082 15085 15086 15087 15088 15092 15094 15099
```

**추정 원인:** 릴레이 reverse tunnel 미등록, 장비 전원 OFF, 네트워크 단절

---

## 6. 발견된 이슈 및 조치

### 6-1. SSH 중첩 인용부호 깨짐

- **증상:** sudoers 파일 미생성 → systemd 마이그레이션 실패 → PM2 healthcheck 실패 → 자동 롤백
- **원인:** `device_ssh` 함수가 `'$*'`로 명령을 감싸면서 내부 단일인용부호와 충돌
- **조치:** heredoc(`bash -s <<EOF`) 방식으로 변경 → 인용부호 중첩 문제 해소

### 6-2. git stash 충돌

- **증상:** 이전 배포에서 남은 stash가 `update_server.sh` 파일 충돌 유발
- **원인:** 구형 레이아웃(루트의 `update_server.sh`)이 stash에 남아있었고, 현재 master에서는 `device_config/`로 이동됨
- **조치:** git reset 전 `stash drop` + `checkout -- .` + `clean -fd` 단계 추가

### 6-3. 디스크 용량 부족 (4대)

- **증상:** `No space left on device` — git fetch/reset 및 update_server.sh 실패
- **원인:** `/var/tmp`과 `/tmp`에 nvidia 유저가 생성한 숨김 디렉토리 ~24,000개, 약 200GB 적재
- **조치:** `sudo rm -rf /var/tmp/.??* /tmp/.??*` + 로그 truncate → 100% → 10%
- **권고:** 전체 장비에 대해 `/var/tmp`, `/tmp` nvidia 임시파일 정리 cron 또는 모니터링 추가 필요

### 6-4. matterhub_id 미설정 장비 (4대)

- **해당 포트:** 15065, 15083, 15090, 15096
- **증상:** 서비스 정상 기동되나 matterhub_id가 `unknown` → MQTT 토픽 구독/발행 불가
- **원인:** AWS IoT 프로비저닝(claim 인증서 → Thing 등록) 미수행
- **권고:** `/device-provision` 스킬 또는 수동 프로비저닝 필요

---

## 7. 후속 작업

| 우선순위 | 작업 | 대상 |
|----------|------|------|
| 높음 | 오프라인 60대 원인 파악 (현장 확인 또는 터널 재등록) | 60대 |
| 높음 | matterhub_id 미설정 장비 프로비저닝 | 15065, 15083, 15090, 15096 |
| 중간 | nvidia 임시파일 정리 자동화 (cron) | 전체 장비 |
| 낮음 | MQTT 원격 업데이트 정상 동작 확인 (성공 장비 대상 테스트) | 42대 |

---

## 8. 결과 파일 위치

배포 과정에서 생성된 결과 파일:

| 디렉토리 | 내용 |
|----------|------|
| `/tmp/bulk_deploy_20260326_104101/` | dry-run 전수 조사 (102대) |
| `/tmp/bulk_deploy_20260326_105625/` | 그룹 A 배포 (11대) |
| `/tmp/bulk_deploy_20260326_105823/` | 그룹 B 배포 (12대) |
| `/tmp/bulk_deploy_20260326_105931/` | 그룹 C 배포 (6대) |
| `/tmp/bulk_deploy_20260326_110445/` | 그룹 D 배포 (12대) |
| `/tmp/bulk_deploy_20260326_112021/` | 디스크풀 3대 재배포 |
| `/tmp/bulk_deploy_20260326_112621/` | 15097 배포 |

각 디렉토리에 `results.csv`, `offline.txt`, `failed.txt`, `<port>.log` 포함.
