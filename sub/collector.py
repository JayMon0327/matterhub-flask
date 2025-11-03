"""
상태 히스토리 수집기
매시간 Home Assistant API를 호출하여 기기 상태를 NDJSON 형식으로 저장
"""
import os
import json
import time
import requests
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Any, Optional, Iterator, Tuple, Set
from dotenv import load_dotenv
import threading
import logging
from urllib.parse import urlencode

# 로깅 설정
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

load_dotenv()

# 환경 변수
HA_host = os.environ.get('HA_host')
hass_token = os.environ.get('hass_token')
devices_file_path = os.environ.get('devices_file_path')
EDGE_LOG_ROOT = os.environ.get('EDGE_LOG_ROOT', '/var/log/edge-history')
COLLECTION_INTERVAL = int(os.environ.get('COLLECTION_INTERVAL', '3600'))  # 기본 1시간

# History 모드 환경 변수 (기본값 포함)
USE_HISTORY_MODE = os.environ.get('USE_HISTORY_MODE', 'false').lower() == 'true'
HISTORY_WINDOW_MINUTES = int(os.environ.get('HISTORY_WINDOW_MINUTES', '60'))
HISTORY_MINIMAL_RESPONSE = os.environ.get('HISTORY_MINIMAL_RESPONSE', 'true').lower() == 'true'
HISTORY_NO_ATTRIBUTES = os.environ.get('HISTORY_NO_ATTRIBUTES', 'true').lower() == 'true'
HISTORY_SIGNIFICANT_ONLY = os.environ.get('HISTORY_SIGNIFICANT_ONLY', 'true').lower() == 'true'
HISTORY_ENTITIES = os.environ.get('HISTORY_ENTITIES', '')  # comma-separated
HISTORY_CHECKPOINT_PATH = os.environ.get('HISTORY_CHECKPOINT_PATH', os.path.join(EDGE_LOG_ROOT, '.checkpoint'))
HISTORY_BACKFILL_MAX_DAYS = int(os.environ.get('HISTORY_BACKFILL_MAX_DAYS', '9'))

# 재시도 설정
MAX_RETRIES = 3
RETRY_DELAY_BASE = 2  # 지수 백오프 기본 지연(초)


def ensure_directory(path: str) -> None:
    """디렉토리가 없으면 생성"""
    os.makedirs(path, exist_ok=True)


def get_hour_path(dt: datetime) -> str:
    """시간에 해당하는 파일 경로 반환"""
    return os.path.join(
        EDGE_LOG_ROOT,
        dt.strftime("%Y/%m/%d/%H.ndjson")
    )


def get_temp_path(dt: datetime) -> str:
    """임시 파일 경로 반환"""
    hour_path = get_hour_path(dt)
    return f"{hour_path}.part"


# =========================
# 공통 유틸리티
# =========================

def hour_floor(dt: datetime) -> datetime:
    return dt.astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0)

def to_utc_iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat().replace('+00:00', 'Z')


def collect_device_states() -> Optional[List[Dict[str, Any]]]:
    """Home Assistant에서 기기 상태 수집"""
    if not HA_host or not hass_token:
        logger.error("HA_host 또는 hass_token이 설정되지 않았습니다")
        return None
    
    headers = {"Authorization": f"Bearer {hass_token}"}
    
    # 재시도 로직
    for attempt in range(MAX_RETRIES):
        try:
            response = requests.get(
                f"{HA_host}/api/states",
                headers=headers,
                timeout=30
            )
            
            if response.status_code == 200:
                states = response.json()
                logger.info(f"상태 수집 성공: {len(states)}개 기기")
                return states
            else:
                logger.warning(f"API 응답 오류: {response.status_code}")
                if attempt < MAX_RETRIES - 1:
                    time.sleep(RETRY_DELAY_BASE ** attempt)
                    
        except requests.exceptions.RequestException as e:
            logger.error(f"상태 수집 실패 (시도 {attempt + 1}/{MAX_RETRIES}): {e}")
            if attempt < MAX_RETRIES - 1:
                time.sleep(RETRY_DELAY_BASE ** attempt)
    
    logger.error("상태 수집 최종 실패")
    return None


# =========================
# entities 로딩 (devices.json + HISTORY_ENTITIES)
# =========================

def build_entity_list() -> Set[str]:
    entities: Set[str] = set()
    # devices.json 우선
    try:
        if devices_file_path and os.path.exists(devices_file_path):
            with open(devices_file_path, 'r', encoding='utf-8') as f:
                content = f.read().strip()
                if content:
                    devices_data = json.loads(content)
                    for device in devices_data:
                        eid = device.get('entity_id')
                        if isinstance(eid, str) and eid:
                            entities.add(eid)
    except Exception as e:
        logger.warning(f"devices.json 읽기 실패: {e}")
    # HISTORY_ENTITIES 보조
    if HISTORY_ENTITIES:
        for eid in [x.strip() for x in HISTORY_ENTITIES.split(',') if x.strip()]:
            entities.add(eid)
    return entities


def filter_states(states: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """devices.json에 등록된 기기만 필터링 (선택사항)"""
    if not states:
        return []
    
    managed_devices = set()
    try:
        if devices_file_path and os.path.exists(devices_file_path):
            with open(devices_file_path, 'r', encoding='utf-8') as f:
                content = f.read().strip()
                if content:
                    devices_data = json.loads(content)
                    for device in devices_data:
                        if 'entity_id' in device:
                            managed_devices.add(device['entity_id'])
    except Exception as e:
        logger.warning(f"devices.json 읽기 실패: {e}")
        # 실패 시 전체 기기 포함
        return states
    
    # managed_devices가 비어있으면 전체 포함
    if not managed_devices:
        return states
    
    # 필터링
    filtered = [s for s in states if s.get('entity_id', '') in managed_devices]
    logger.info(f"필터링: 전체 {len(states)}개 → 관리 {len(filtered)}개")
    return filtered


def format_state_record(state: Dict[str, Any], ts: datetime) -> Dict[str, Any]:
    """상태 데이터를 표준 형식으로 변환"""
    entity_id = state.get('entity_id', '')
    current_state = state.get('state', '')
    attributes = state.get('attributes', {})
    
    # metrics 추출
    metrics = {}
    
    # 1) attributes에서 직접 메트릭 추출
    for key in ['temperature', 'humidity', 'battery', 'battery_level', 
                'brightness', 'voltage', 'power', 'current', 'current_position']:
        if key in attributes:
            try:
                value = attributes[key]
                if isinstance(value, (int, float)):
                    metrics[key] = value
                elif isinstance(value, str):
                    try:
                        metrics[key] = float(value) if '.' in value else int(value)
                    except ValueError:
                        pass
            except Exception:
                pass
    
    # 2) state 값을 device_class 기준으로 metrics에 추가
    device_class = attributes.get('device_class', '')
    if current_state and current_state not in ['unavailable', 'unknown', 'on', 'off', 'open', 'closed']:
        try:
            # state를 숫자로 변환 시도
            state_value = float(current_state) if '.' in current_state else int(current_state)
            
            # device_class 기반으로 metrics 키 결정
            if device_class == 'temperature':
                metrics['temperature'] = state_value
            elif device_class == 'humidity':
                metrics['humidity'] = state_value
            elif device_class == 'illuminance':
                metrics['brightness'] = state_value
            elif device_class in ['current', 'voltage', 'power', 'energy']:
                metrics[device_class] = state_value
            elif entity_id.startswith('sensor.'):
                # 센서이지만 device_class가 없는 경우, entity_id 기반으로 추정
                if 'temp' in entity_id.lower() or 'temperature' in entity_id.lower() or 'ondo' in entity_id.lower():
                    metrics['temperature'] = state_value
                elif 'humid' in entity_id.lower() or 'seubdo' in entity_id.lower():
                    metrics['humidity'] = state_value
                elif 'bright' in entity_id.lower() or 'jodo' in entity_id.lower():
                    metrics['brightness'] = state_value
        except (ValueError, TypeError):
            # state가 숫자가 아닌 경우 무시
            pass
    
    # 3) attributes에서 추가 메트릭 추출 (current_position 등)
    if 'current_position' in attributes:
        try:
            metrics['current_position'] = int(attributes['current_position'])
        except (ValueError, TypeError):
            pass
    
    return {
        "ts": ts.isoformat().replace('+00:00', 'Z'),
        "device_id": entity_id,
        "status": current_state,
        "metrics": metrics,
        "attributes": attributes,  # 전체 attributes 포함 (선택적)
        "source": "edge-api",
        "version": "1.0"
    }


# =========================
# History 모드: 호출/평탄화/포맷
# =========================

def compute_history_window(now_utc: datetime) -> Tuple[datetime, datetime]:
    end_dt = hour_floor(now_utc)
    start_dt = end_dt - timedelta(minutes=HISTORY_WINDOW_MINUTES)
    return start_dt, end_dt


def build_history_query_params(start_iso: str, end_iso: str, entities: Set[str]) -> List[Tuple[str, str]]:
    params: List[Tuple[str, str]] = []
    params.append(("end_time", end_iso))
    if HISTORY_MINIMAL_RESPONSE:
        params.append(("minimal_response", "true"))
    if HISTORY_NO_ATTRIBUTES:
        params.append(("no_attributes", "true"))
    if HISTORY_SIGNIFICANT_ONLY:
        params.append(("significant_changes_only", "true"))
    # 반복 파라미터로 entity 추가
    for eid in sorted(list(entities)):
        params.append(("filter_entity_id", eid))
    return params


def fetch_history(start_dt: datetime, end_dt: datetime, entities: Set[str]) -> Optional[List[List[Dict[str, Any]]]]:
    if not HA_host or not hass_token:
        logger.error("HA_host 또는 hass_token이 설정되지 않았습니다")
        return None
    headers = {"Authorization": f"Bearer {hass_token}"}
    start_iso = to_utc_iso(start_dt)
    end_iso = to_utc_iso(end_dt)
    path = f"{HA_host}/api/history/period/{start_iso}"
    params_pairs = build_history_query_params(start_iso, end_iso, entities)
    query = urlencode(params_pairs)

    url = f"{path}?{query}"

    for attempt in range(MAX_RETRIES):
        try:
            resp = requests.get(url, headers=headers, timeout=60)
            if resp.status_code == 200:
                return resp.json()
            logger.warning(f"History API 응답 오류: {resp.status_code}")
            if attempt < MAX_RETRIES - 1:
                time.sleep(RETRY_DELAY_BASE ** attempt)
        except requests.exceptions.RequestException as e:
            logger.error(f"History API 호출 실패 (시도 {attempt + 1}/{MAX_RETRIES}): {e}")
            if attempt < MAX_RETRIES - 1:
                time.sleep(RETRY_DELAY_BASE ** attempt)
    return None


def flatten_history(raw: List[List[Dict[str, Any]]]) -> Iterator[Dict[str, Any]]:
    for entity_events in raw:
        if not isinstance(entity_events, list):
            continue
        for ev in entity_events:
            if not isinstance(ev, dict):
                continue
            eid = ev.get('entity_id')
            st = ev.get('state')
            ts = ev.get('last_changed') or ev.get('last_updated')
            attrs = None if HISTORY_NO_ATTRIBUTES else (ev.get('attributes') or None)
            if not (eid and st and ts):
                continue
            try:
                # ts를 datetime으로 검증 변환 후 ISO UTC로 재정규화
                _dt = datetime.fromisoformat(str(ts).replace('Z', '+00:00')).astimezone(timezone.utc)
                yield {
                    "ts": _dt.isoformat().replace('+00:00', 'Z'),
                    "device_id": eid,
                    "status": st,
                    "attributes": attrs,
                    "source": "ha-history-api",
                    "version": "2.0"
                }
            except Exception:
                continue


def save_to_file(states: List[Dict[str, Any]], dt: datetime) -> bool:
    """상태를 NDJSON 파일로 저장 (원자적 쓰기 보장)"""
    if not states:
        logger.warning("저장할 상태 데이터가 없습니다")
        return False
    
    temp_path = get_temp_path(dt)
    final_path = get_hour_path(dt)
    
    # 디렉토리 생성
    ensure_directory(os.path.dirname(temp_path))
    
    # UTC 타임스탬프 생성
    ts = dt.astimezone(timezone.utc)
    
    try:
        # 임시 파일에 쓰기 (새로운 시간 단위 파일이므로 'w' 모드 사용)
        with open(temp_path, 'w', encoding='utf-8') as f:
            for state in states:
                record = format_state_record(state, ts)
                f.write(json.dumps(record, ensure_ascii=False) + '\n')
        
        # 원자적 rename
        os.rename(temp_path, final_path)
        
        # 파일 정보 로깅
        file_size = os.path.getsize(final_path)
        logger.info(f"상태 저장 완료: {final_path} ({file_size} bytes, {len(states)}개 레코드)")
        return True
        
    except Exception as e:
        logger.error(f"파일 저장 실패: {e}")
        # 임시 파일 정리
        try:
            if os.path.exists(temp_path):
                os.remove(temp_path)
        except:
            pass
        return False


def dedup_and_atomic_append(start_dt: datetime, new_records: List[Dict[str, Any]]) -> bool:
    """기존 파일과 신규 이벤트를 병합해 원자적으로 저장 (중복 제거)"""
    final_path = get_hour_path(start_dt)
    temp_path = f"{final_path}.part"
    ensure_directory(os.path.dirname(final_path))

    # 기존 키 로드
    existing_keys: Set[Tuple[str, str]] = set()
    try:
        if os.path.exists(final_path):
            with open(final_path, 'r', encoding='utf-8') as rf:
                for line in rf:
                    try:
                        obj = json.loads(line)
                        k = (str(obj.get('device_id')), str(obj.get('ts')))
                        existing_keys.add(k)
                    except Exception:
                        continue
    except Exception as e:
        logger.warning(f"기존 파일 키 로딩 실패: {e}")

    # 임시 파일 작성: 기존 라인 복사
    try:
        with open(temp_path, 'w', encoding='utf-8') as wf:
            if os.path.exists(final_path):
                with open(final_path, 'r', encoding='utf-8') as rf:
                    for line in rf:
                        wf.write(line)
            # 신규 추가 (중복 제외)
            added = 0
            for rec in new_records:
                k = (str(rec.get('device_id')), str(rec.get('ts')))
                if k in existing_keys:
                    continue
                wf.write(json.dumps(rec, ensure_ascii=False) + '\n')
                existing_keys.add(k)
                added += 1
        os.replace(temp_path, final_path)
        logger.info(f"히스토리 저장 완료: {final_path} (신규 {added}개)\n")
        return True
    except Exception as e:
        logger.error(f"히스토리 파일 병합 실패: {e}")
        try:
            if os.path.exists(temp_path):
                os.remove(temp_path)
        except Exception:
            pass
        return False


def read_checkpoint() -> Optional[datetime]:
    try:
        if not HISTORY_CHECKPOINT_PATH:
            return None
        if not os.path.exists(HISTORY_CHECKPOINT_PATH):
            return None
        with open(HISTORY_CHECKPOINT_PATH, 'r', encoding='utf-8') as f:
            s = f.read().strip()
            if not s:
                return None
            return datetime.fromisoformat(s.replace('Z', '+00:00')).astimezone(timezone.utc)
    except Exception as e:
        logger.warning(f"체크포인트 읽기 실패: {e}")
        return None


def write_checkpoint(dt: datetime) -> None:
    try:
        ensure_directory(os.path.dirname(HISTORY_CHECKPOINT_PATH))
        with open(HISTORY_CHECKPOINT_PATH, 'w', encoding='utf-8') as f:
            f.write(to_utc_iso(dt))
    except Exception as e:
        logger.warning(f"체크포인트 기록 실패: {e}")


def collect_history_window(start_dt: datetime, end_dt: datetime, entities: Set[str]) -> None:
    if not entities:
        print("경고: 수집할 엔티티가 없습니다")
        logger.warning("수집할 엔티티가 없습니다")
        return
    raw = fetch_history(start_dt, end_dt, entities)
    if raw is None:
        print("경고: 히스토리 수집 실패 (API 호출 실패)")
        logger.warning("히스토리 수집 실패")
        return
    records = list(flatten_history(raw))
    print(f"히스토리 이벤트: {len(records)}개")
    logger.info(f"히스토리 이벤트: {len(records)}개")
    if not records:
        print("히스토리 이벤트 없음 (해당 시간대에 변경사항 없음)")
        logger.info("히스토리 이벤트 없음")
        write_checkpoint(end_dt)
        return
    ok = dedup_and_atomic_append(start_dt, records)
    if ok:
        print(f"히스토리 저장 성공: {len(records)}개 이벤트")
        logger.info(f"히스토리 저장 성공: {len(records)}개 이벤트")
        write_checkpoint(end_dt)
    else:
        print("경고: 히스토리 저장 실패")
        logger.warning("히스토리 저장 실패")


def backfill_from_checkpoint(now_utc: datetime, entities: Set[str]) -> None:
    end_now = hour_floor(now_utc)
    last = read_checkpoint()
    # 하한: BACKFILL_MAX_DAYS
    lower_bound = end_now - timedelta(days=HISTORY_BACKFILL_MAX_DAYS)
    if last is None or last < lower_bound:
        last = lower_bound
    # last부터 end_now까지 윈도우 반복
    cur_start = last
    while cur_start < end_now:
        cur_end = cur_start + timedelta(minutes=HISTORY_WINDOW_MINUTES)
        logger.info(f"백필 실행: {to_utc_iso(cur_start)} ~ {to_utc_iso(cur_end)}")
        collect_history_window(cur_start, cur_end, entities)
        cur_start = cur_end


def collect_hourly() -> None:
    """현재 시각의 상태 수집 및 저장"""
    now = datetime.now(timezone.utc)
    # 현재 시각의 정시로 내림
    hour_floor = now.replace(minute=0, second=0, microsecond=0)
    
    print(f"상태 수집 시작: {hour_floor.isoformat()}")
    logger.info(f"상태 수집 시작: {hour_floor.isoformat()}")
    
    # 상태 수집
    states = collect_device_states()
    if states is None:
        print("경고: 상태 수집 실패 (None 반환)")
        logger.warning("상태 수집 실패 (None 반환)")
        return
    
    print(f"수집된 상태: {len(states)}개")
    logger.info(f"수집된 상태: {len(states)}개")
    
    # 필터링 (선택사항)
    filtered_states = filter_states(states)
    print(f"필터링 후: {len(filtered_states)}개")
    logger.info(f"필터링 후: {len(filtered_states)}개")
    
    # 파일 저장
    result = save_to_file(filtered_states, hour_floor)
    if result:
        print(f"저장 완료: {len(filtered_states)}개 레코드")
        logger.info(f"저장 완료: {len(filtered_states)}개 레코드")
    else:
        print("경고: 저장 실패")
        logger.warning("저장 실패")


def collector_thread():
    """수집기 스레드 메인 루프 (History 모드/States 모드)"""
    print("상태 히스토리 수집기 시작")
    print(f"저장 경로: {EDGE_LOG_ROOT}")
    logger.info("상태 히스토리 수집기 시작")
    logger.info(f"저장 경로: {EDGE_LOG_ROOT}")

    if USE_HISTORY_MODE:
        print("모드: HISTORY (Home Assistant /api/history/period)")
        logger.info("모드: HISTORY (Home Assistant /api/history/period)")
        try:
            entities = build_entity_list()
            print(f"수집 대상 엔티티: {len(entities)}개")
            logger.info(f"수집 대상 엔티티: {len(entities)}개")
            if not entities:
                print("경고: 수집할 엔티티가 없습니다. devices.json 또는 HISTORY_ENTITIES 확인 필요")
                logger.warning("수집할 엔티티가 없습니다")
            now = datetime.now(timezone.utc)
            # 백필 먼저 수행
            print("백필 시작...")
            logger.info("백필 시작")
            backfill_from_checkpoint(now, entities)
            print("백필 완료")
            logger.info("백필 완료")
        except Exception as e:
            print(f"백필 초기화 실패: {e}")
            logger.error(f"백필 초기화 실패: {e}", exc_info=True)

        # 주기 실행: 매 정시 윈도우 수집
        while True:
            try:
                now = datetime.now(timezone.utc)
                start_dt, end_dt = compute_history_window(now)
                # 정시에 맞추어 대기
                wait_seconds = (end_dt - now).total_seconds()
                if wait_seconds > 0:
                    logger.info(f"다음 히스토리 수집까지 {wait_seconds:.0f}초 대기")
                    time.sleep(wait_seconds)
                # 수집 실행
                entities = build_entity_list()
                print(f"히스토리 수집 실행: {to_utc_iso(start_dt)} ~ {to_utc_iso(end_dt)}, 엔티티 {len(entities)}개")
                logger.info(f"히스토리 수집 실행: {to_utc_iso(start_dt)} ~ {to_utc_iso(end_dt)}, 엔티티 {len(entities)}개")
                collect_history_window(start_dt, end_dt, entities)
            except Exception as e:
                logger.error(f"히스토리 수집 루프 오류: {e}", exc_info=True)
                time.sleep(COLLECTION_INTERVAL)
    else:
        print("모드: STATES (Home Assistant /api/states)")
        logger.info("모드: STATES (Home Assistant /api/states)")
        # 시작 시 즉시 한 번 수집
        try:
            print("초기 수집 시작...")
            logger.info("초기 수집 시작")
            collect_hourly()
            print("초기 수집 완료")
            logger.info("초기 수집 완료")
        except Exception as e:
            print(f"초기 수집 실패: {e}")
            logger.error(f"초기 수집 실패: {e}", exc_info=True)

        # 주기적 수집
        while True:
            try:
                # 다음 정시까지 대기
                now = datetime.now(timezone.utc)
                next_hour = (now.replace(minute=0, second=0, microsecond=0) + 
                           timedelta(hours=1))
                wait_seconds = (next_hour - now).total_seconds()
                
                print(f"다음 수집까지 {wait_seconds:.0f}초 대기")
                logger.info(f"다음 수집까지 {wait_seconds:.0f}초 대기")
                time.sleep(wait_seconds)
                
                # 수집 실행
                print("정시 수집 실행")
                logger.info("정시 수집 실행")
                collect_hourly()
                
            except Exception as e:
                logger.error(f"수집기 스레드 오류: {e}", exc_info=True)
                # 오류 발생 시 기본 대기 시간
                time.sleep(COLLECTION_INTERVAL)


def start_collector():
    """수집기 스레드 시작"""
    thread = threading.Thread(target=collector_thread, daemon=True, name="StateCollector")
    thread.start()
    logger.info("상태 수집기 스레드 시작됨")
    return thread


if __name__ == "__main__":
    # 독립 실행 모드 (테스트용)
    collect_hourly()
