# 기기 상태 히스토리 시스템 가이드

## 개요

이 시스템은 Home Assistant의 기기 상태를 **1시간 간격**으로 수집하여 로컬에 NDJSON 형식으로 저장하고, 조회 API를 제공합니다.

**핵심 특징:**
- ✅ 매시간 정시에 자동 수집
- ✅ NDJSON 형식으로 로컬 저장
- ✅ 다양한 조회 API 제공
- ✅ 자동 용량 관리 (20GB 초과 시 오래된 파일 자동 삭제)

## 파일 구조

```
로그 저장 경로:
/var/log/edge-history/
  └── yyyy/
      └── mm/
          └── dd/
              └── HH.ndjson   # 예: 2025/10/29/13.ndjson

개발 파일:
matterhub-flask/
├── app.py                          # Flask 앱 (로그 조회 API 포함)
├── sub/
│   ├── collector.py               # 상태 수집기 모듈
│   └── logs_api.py                # 로그 조회 유틸리티
├── prune_edge_logs.sh             # 자동 삭제 스크립트
└── HISTORY_GUIDE.md                # 이 문서
```

## 데이터 형식

### 저장 형식 (NDJSON)
```json
{"ts":"2025-10-29T13:00:00Z","device_id":"light.living_room","status":"on","metrics":{"brightness":255},"attributes":{...},"source":"edge-api","version":"1.0"}
{"ts":"2025-10-29T13:00:00Z","device_id":"sensor.temperature","status":"22.5","metrics":{"temperature":22.5},"attributes":{...},"source":"edge-api","version":"1.0"}
```

### 공통 필드
- `ts`: ISO8601 UTC 타임스탬프
- `device_id`: 기기 ID (entity_id)
- `status`: 현재 상태
- `metrics`: 주요 메트릭 (temperature, humidity, battery 등)
- `attributes`: 전체 속성 (선택적)
- `source`: "edge-api"
- `version`: "1.0"

## 환경 변수 설정

`.env` 파일에 추가할 환경 변수:

```bash
# 로그 저장 경로 (기본: /var/log/edge-history)
EDGE_LOG_ROOT=/var/log/edge-history

# 기본 조회 시간 범위 (기본: 24시간)
DEFAULT_WINDOW_HOURS=24

# 최대 조회 제한 (기본: 5000)
MAX_LIMIT=5000

# 기본 조회 제한 (기본: 200)
DEFAULT_LIMIT=200

# History 모드(증분 수집) 설정
USE_HISTORY_MODE=true
HISTORY_WINDOW_MINUTES=60
HISTORY_MINIMAL_RESPONSE=true
HISTORY_NO_ATTRIBUTES=true
HISTORY_SIGNIFICANT_ONLY=true
# 보조 엔티티 목록(옵션, 콤마 구분)
# HISTORY_ENTITIES=sensor.a,sensor.b
HISTORY_CHECKPOINT_PATH=/var/log/edge-history/.checkpoint
HISTORY_BACKFILL_MAX_DAYS=9
```

## 동작 방식

### 1. 자동 수집
- Flask 애플리케이션 시작 시 백그라운드 스레드가 자동으로 시작
- **매시간 정시(예: 13:00, 14:00, 15:00)**에 Home Assistant API 호출
- 상태 데이터를 수집하여 NDJSON 파일에 저장
- 실패 시 자동 재시도 (최대 3회, 지수 백오프)

### 2. 데이터 저장
- **원자적 쓰기**: 임시 파일(`.part`)에 쓰고 완료 후 rename하여 쓰기 중 읽기 충돌 방지
- **UTC 기준**: 모든 타임스탬프는 UTC로 저장
- **필터링**: devices.json에 등록된 기기만 저장 (선택사항)

### 3. 자동 삭제
- 디스크 용량이 20GB를 초과하면 가장 오래된 파일부터 순차적으로 삭제
- cron 또는 systemd timer로 주기적 실행

## API 엔드포인트

### 1. 로그 조회 (`GET /local/api/logs`)

시간 범위와 필터 조건으로 로그를 조회합니다.

**쿼리 파라미터:**
- `from` (선택): 시작 시간 (ISO8601 형식)
- `to` (선택): 종료 시간 (ISO8601 형식)
- `device_id` (선택): 기기 ID 필터 (다중 지정 가능: `?device_id=a&device_id=b`)
- `status` (선택): 상태 필터
- `q` (선택): 문자열 포함 검색
- `cursor` (선택): 페이지네이션 커서
- `limit` (선택): 반환 개수 제한 (기본: 200, 최대: 5000)

**예시:**
```bash
# 최근 24시간 로그 조회
curl "http://localhost:8100/local/api/logs"

# 특정 기기 로그 조회
curl "http://localhost:8100/local/api/logs?device_id=light.living_room"

# 시간 범위 지정
curl "http://localhost:8100/local/api/logs?from=2025-10-29T00:00:00Z&to=2025-10-29T23:59:59Z"
```

### 2. 최근 일주일 로그 조회 (`GET /local/api/logs/weekly`)

최근 일주일(7일) 동안 매일 12:00시의 대표 로그를 조회합니다.

**쿼리 파라미터:**
- `limit` (선택): 반환 개수 제한 (기본: 200, 최대: 5000)
- `device_id` (선택): 기기 ID 필터 (다중 지정 가능)
- `status` (선택): 상태 필터
- `q` (선택): 문자열 포함 검색

**예시:**
```bash
# 최근 일주일 로그 조회 (매일 12:00시)
curl "http://localhost:8100/local/api/logs/weekly"

# 특정 기기만 조회
curl "http://localhost:8100/local/api/logs/weekly?device_id=light.living_room"
```

### 3. 최근 한달 로그 조회 (`GET /local/api/logs/monthly`)

최근 한달(30일) 동안 매일 12:00시의 대표 로그를 조회합니다.

**쿼리 파라미터:**
- `limit` (선택): 반환 개수 제한 (기본: 200, 최대: 5000)
- `device_id` (선택): 기기 ID 필터 (다중 지정 가능)
- `status` (선택): 상태 필터
- `q` (선택): 문자열 포함 검색

**예시:**
```bash
# 최근 한달 로그 조회 (매일 12:00시)
curl "http://localhost:8100/local/api/logs/monthly"

# 특정 기기만 조회
curl "http://localhost:8100/local/api/logs/monthly?device_id=light.living_room"
```

### 4. 최근 로그 조회 (`GET /local/api/logs/tail`)

최근 N초간의 로그를 조회합니다 (최신순).

**쿼리 파라미터:**
- `since` (선택): 초 단위 (기본: 3600 = 1시간)
- `limit` (선택): 반환 개수 (기본: 200)
- `device_id`, `status`, `q`: 필터 조건

**예시:**
```bash
# 최근 1시간 로그 조회
curl "http://localhost:8100/local/api/logs/tail?since=3600"
```

### 5. 로그 통계 (`GET /local/api/logs/stats`)

시간별 로그 개수 통계를 조회합니다.

**쿼리 파라미터:**
- `from`, `to`: 시간 범위 (ISO8601 형식)

**예시:**
```bash
curl "http://localhost:8100/local/api/logs/stats?from=2025-10-29T00:00:00Z&to=2025-10-29T23:59:59Z"
```

**응답 형식:**
```json
{
  "items": [
    {"hour": "2025-10-29T13:00:00Z", "count": 1500},
    {"hour": "2025-10-29T14:00:00Z", "count": 1520}
  ]
}
```

### 6. 파일 목록 (`GET /local/api/logs/files`)

시간 범위에 해당하는 로그 파일 목록을 조회합니다.

**쿼리 파라미터:**
- `from`, `to`: 시간 범위 (ISO8601 형식)

**예시:**
```bash
curl "http://localhost:8100/local/api/logs/files?from=2025-10-29T00:00:00Z"
```

**응답 형식:**
```json
{
  "files": [
    {
      "path": "/var/log/edge-history/2025/10/29/13.ndjson",
      "size": 512000,
      "mtime": "2025-10-29T13:00:00Z"
    }
  ]
}
```

**응답 형식 (공통):**
```json
{
  "items": [
    {
      "ts": "2025-10-29T13:00:00Z",
      "device_id": "light.living_room",
      "status": "on",
      "metrics": null,
      "attributes": null,
      "source": "ha-history-api",
      "version": "2.0"
    }
  ],
  "next_cursor": "base64_encoded_cursor"  // 페이지네이션용 (일부 API만)
}
```

참고: states 모드일 때는 `metrics`가 포함될 수 있고 `source=\"edge-api\"`, `version=\"1.0\"`가 될 수 있습니다.

## 자동 삭제 정책

### 동작 방식
**에지 히스토리 디렉토리(`/var/log/edge-history`)의 용량이 20GB를 초과**하면 가장 오래된 파일부터 순차적으로 자동 삭제합니다.

> **참고**: 이 정책은 **에지 히스토리 디렉토리만** 대상으로 하며, 전체 디스크 용량이 아닙니다. 전체 디스크 용량 모니터링이 필요한 경우 별도의 디스크 모니터링 솔루션을 사용하세요.

**환경 변수:**
- `EDGE_LOG_ROOT`: 로그 디렉토리 (기본: `/var/log/edge-history`)
- `CAP_BYTES`: 디렉토리 크기 제한 (기본: 21474836480 bytes = 20 GiB)

### 스크립트 설정

#### cron 설정 예시
```bash
# 매시간 5분에 실행
5 * * * * /path/to/prune_edge_logs.sh >> /var/log/prune_edge_logs.log 2>&1
```

#### systemd timer 설정 (권장)
```ini
# /etc/systemd/system/prune-edge-logs.timer
[Unit]
Description=Prune edge logs hourly

[Timer]
OnCalendar=hourly
RandomizedDelaySec=300

[Install]
WantedBy=timers.target
```

```ini
# /etc/systemd/system/prune-edge-logs.service
[Unit]
Description=Prune edge logs

[Service]
Type=oneshot
Environment="EDGE_LOG_ROOT=/var/log/edge-history"
Environment="CAP_BYTES=21474836480"
ExecStart=/path/to/prune_edge_logs.sh
```

**활성화:**
```bash
sudo systemctl enable prune-edge-logs.timer
sudo systemctl start prune-edge-logs.timer
```

## 용량 추정 및 관리

### 예상 용량 (연간)
- **수집 주기**: 매시간 1회
- **시간당 라인 수**: 1,500 ~ 2,000개
- **연간 누적 (20 GiB cap 기준)**:
  - 200B/줄: ~2.45 ~ 3.26 GiB/년
  - 400B/줄: ~4.90 ~ 6.53 GiB/년
  - 800B/줄: ~9.79 ~ 13.05 GiB/년
  - 1.2KB/줄: ~14.69 ~ 19.58 GiB/년

**권장 설정**: 10~20 GiB/년 예상 + 30% 이상 운영 버퍼

### 현재 용량 확인
```bash
# 전체 디렉토리 크기 확인
du -sh /var/log/edge-history

# 시간별 파일 크기 확인
find /var/log/edge-history -name "*.ndjson" -exec ls -lh {} \;
```

## 모니터링

### 수집 상태 확인
```bash
# 수집 성공 로그 확인
grep "상태 저장 완료" /var/log/your_app.log

# 수집 실패 로그 확인
grep "상태 수집 실패" /var/log/your_app.log

# Flask 애플리케이션 로그에서 "상태 히스토리 수집기 시작됨" 메시지 확인
```

### 디스크 사용량 체크
```bash
# 디스크 사용량 확인
df -h /var/log/edge-history

# 로그 디렉토리 크기 확인
du -sh /var/log/edge-history
```

## 문제 해결

### API 응답이 비어있는 경우 (items: [])

#### 1. 수집기 실행 상태 확인
```bash
# PM2 프로세스 확인
pm2 list

# app.py 로그 확인 (수집기 시작 메시지 확인)
pm2 logs wm-app | grep -i "히스토리\|history\|수집"
```

#### 2. 로그 파일 생성 확인
```bash
# 로그 디렉토리 확인 (기본: /var/log/edge-history)
ls -la /var/log/edge-history/

# 또는 환경 변수로 지정한 경로 확인
# EDGE_LOG_ROOT 환경 변수 확인 필요

# 시간별 파일 확인
find /var/log/edge-history -name "*.ndjson" -type f | head -10
```

#### 3. 환경 변수 확인
```bash
# .env 파일 확인 또는 환경 변수 확인
echo $USE_HISTORY_MODE  # true면 History 모드, false면 States 모드
echo $EDGE_LOG_ROOT     # 로그 저장 경로
echo $HA_host
echo $hass_token
echo $devices_file_path  # 필터링 사용 시
```

#### 4. 수집 모드별 확인
- **History 모드 (USE_HISTORY_MODE=true)**:
  - 체크포인트 파일 확인: `/var/log/edge-history/.checkpoint`
  - 백필이 정상 실행되었는지 로그 확인
  - entities 필터 확인 (devices.json 또는 HISTORY_ENTITIES)
  
- **States 모드 (USE_HISTORY_MODE=false 또는 미설정)**:
  - 매 정시마다 수집 실행 확인
  - HA API 호출 성공 여부 확인

#### 5. 즉시 수집 테스트
```bash
# Python으로 직접 수집 테스트
cd /path/to/matterhub-flask
python3 -m sub.collector
```

### 수집이 되지 않는 경우

#### 권한 오류 해결 (Permission denied)
```bash
# 에러: [Errno 13] Permission denied: '/var/log/edge-history'

# 해결 방법 1: 디렉토리 생성 및 권한 설정
sudo mkdir -p /var/log/edge-history
sudo chown -R $USER:$USER /var/log/edge-history
sudo chmod -R 755 /var/log/edge-history

# 해결 방법 2: 환경 변수로 다른 경로 지정 (권장)
# .env 파일 또는 환경 변수 설정
export EDGE_LOG_ROOT=$HOME/edge-history
# 또는
export EDGE_LOG_ROOT=/home/hyodol/edge-history

# 디렉토리 생성
mkdir -p $EDGE_LOG_ROOT
```

#### 기타 확인 사항
1. 환경 변수 확인:
   ```bash
   echo $HA_host
   echo $hass_token
   echo $EDGE_LOG_ROOT  # 로그 저장 경로 확인
   ```
2. 권한 확인:
   ```bash
   ls -ld $EDGE_LOG_ROOT
   mkdir -p $EDGE_LOG_ROOT  # 디렉토리가 없으면 생성
   ```
3. 로그 확인: Flask 애플리케이션 로그에서 "상태 히스토리 수집기 시작됨" 메시지 확인
4. PM2 재시작:
   ```bash
   pm2 restart wm-app
   pm2 logs wm-app  # 실시간 로그 확인
   ```

### API 조회가 느린 경우
- `limit` 파라미터로 조회 개수 제한
- 시간 범위를 좁게 지정
- `device_id` 필터 활용

### 디스크 용량 부족
- `CAP_BYTES` 환경 변수로 제한 축소
- 수동으로 오래된 파일 삭제:
  ```bash
  find /var/log/edge-history -name "*.ndjson" -type f -mtime +180 -delete
  ```

## 테스트

### 수집기 테스트
```bash
cd /path/to/matterhub-flask
python -m sub.collector
```

### API 테스트
```bash
# 최근 로그 조회
curl "http://localhost:8100/local/api/logs?limit=10"

# 특정 기기 필터
curl "http://localhost:8100/local/api/logs?device_id=light.living_room&limit=10"

# 통계 조회
curl "http://localhost:8100/local/api/logs/stats"

# 일주일 로그 조회
curl "http://localhost:8100/local/api/logs/weekly"

# 한달 로그 조회
curl "http://localhost:8100/local/api/logs/monthly"
```

## 주의사항

1. **저장 경로**: `/var/log/edge-history`는 루트 권한이 필요할 수 있습니다. 개발 환경에서는 `EDGE_LOG_ROOT` 환경 변수로 다른 경로 지정 가능합니다.

2. **네트워크 장애**: HA API 호출 실패 시 자동 재시도(최대 3회, 지수 백오프)합니다.

3. **동시성**: 파일 쓰기는 원자적이므로 동시 쓰기 충돌은 발생하지 않습니다.

4. **백업**: 장기 보관이 필요한 경우 별도 백업 전략 필요 (예: 일 단위 tar + 오브젝트 스토리지)

## 구현 세부사항

### 1. 상태 수집기 (`sub/collector.py`)
- ✅ 1시간 간격으로 Home Assistant API 호출
- ✅ NDJSON 형식으로 파일 저장
- ✅ 원자적 쓰기 보장 (임시 파일 + rename)
- ✅ 재시도 로직 (지수 백오프)
- ✅ devices.json 기반 필터링 (선택사항)
- ✅ 백그라운드 스레드로 자동 실행

### 2. 로그 조회 API (`sub/logs_api.py` + `app.py`)
- ✅ `GET /local/api/logs` - 로그 조회 (페이지네이션, 필터링)
- ✅ `GET /local/api/logs/weekly` - 일주일 로그 (매일 12:00시)
- ✅ `GET /local/api/logs/monthly` - 한달 로그 (매일 12:00시)
- ✅ `GET /local/api/logs/tail` - 최근 로그 조회
- ✅ `GET /local/api/logs/stats` - 시간별 통계
- ✅ `GET /local/api/logs/files` - 파일 목록
- ✅ 커서 기반 페이지네이션
- ✅ device_id, status, 문자열 검색 필터링

### 3. 자동 삭제 스크립트 (`prune_edge_logs.sh`)
- ✅ 총량 기반 삭제 (CAP_BYTES)
- ✅ 오래된 파일부터 순차 삭제
- ✅ systemd timer / cron 설정 예시 제공
