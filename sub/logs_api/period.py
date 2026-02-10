"""
Period History JSON 파일 조회: read_period_history_json, list_period_history_files,
read_period_history_daily_sample, read_period_history_daily_hourly
"""
import os
import json
import glob
import logging
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Any, Optional, Tuple, Set


def _load_devices_entity_ids(devices_file_path: Optional[str]) -> Optional[Set[str]]:
    """devices.json에서 entity_id 목록을 읽어옴"""
    if not devices_file_path or not os.path.exists(devices_file_path):
        return None
    try:
        with open(devices_file_path, 'r', encoding='utf-8') as f:
            content = f.read().strip()
            if not content:
                return None
            devices_data = json.loads(content)
            entities = set()
            for device in devices_data:
                eid = device.get('entity_id')
                if isinstance(eid, str) and eid:
                    entities.add(eid)
            return entities if entities else None
    except Exception:
        return None


def read_period_history_json(timestamp: Optional[str], root: str, devices_file_path: Optional[str] = None) -> List[List[Dict[str, Any]]]:
    """Period History 모드로 저장된 JSON 파일을 읽어서 반환 (HA History API와 동일한 응답 형식)"""
    if not os.path.exists(root):
        logging.warning(f"Period History 디렉토리가 없습니다: {root}")
        return []
    entity_filter = _load_devices_entity_ids(devices_file_path)
    if timestamp:
        file_path = os.path.join(root, f"{timestamp}.json")
        if not os.path.exists(file_path):
            logging.warning(f"Period History 파일이 없습니다: {file_path}")
            return []
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
                if isinstance(data, list):
                    if entity_filter:
                        filtered_data = []
                        for events in data:
                            if not isinstance(events, list) or len(events) == 0:
                                continue
                            first_event = events[0] if events else None
                            if first_event and isinstance(first_event, dict):
                                entity_id = first_event.get('entity_id')
                                if entity_id and entity_id in entity_filter:
                                    filtered_data.append(events)
                        data = filtered_data
                    logging.info(f"Period History 파일 읽기 성공: {file_path}, 엔티티 배열 {len(data)}개")
                    return data
                logging.warning(f"Period History 파일 형식 오류: {file_path} (list가 아님)")
                return []
        except Exception as e:
            logging.error(f"Period History 파일 읽기 실패: {file_path}, 에러: {e}")
            return []
    else:
        pattern = os.path.join(root, "*.json")
        files = glob.glob(pattern)
        logging.info(f"Period History 파일 조회: {root}에서 {len(files)}개 파일 발견")
        if not files:
            logging.warning(f"Period History 파일이 없습니다: {root}")
            return []
        def get_timestamp_from_path(path: str) -> str:
            return os.path.basename(path).replace('.json', '')
        files.sort(key=get_timestamp_from_path, reverse=True)
        latest_file = files[0]
        logging.info(f"Period History 최신 파일 선택: {latest_file}")
        try:
            with open(latest_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
                if isinstance(data, list):
                    if entity_filter:
                        filtered_data = []
                        for events in data:
                            if not isinstance(events, list) or len(events) == 0:
                                continue
                            first_event = events[0] if events else None
                            if first_event and isinstance(first_event, dict):
                                entity_id = first_event.get('entity_id')
                                if entity_id and entity_id in entity_filter:
                                    filtered_data.append(events)
                        data = filtered_data
                    logging.info(f"Period History 파일 읽기 성공: {latest_file}, 엔티티 배열 {len(data)}개")
                    return data
                logging.warning(f"Period History 파일 형식 오류: {latest_file} (list가 아님)")
                return []
        except Exception as e:
            logging.error(f"Period History 파일 읽기 실패: {latest_file}, 에러: {e}")
            return []


def list_period_history_files(root: str, limit: int = 10) -> Dict[str, Any]:
    """Period History 모드로 저장된 JSON 파일 목록 조회"""
    pattern = os.path.join(root, "*.json")
    files = glob.glob(pattern)
    file_list = []
    for file_path in files:
        try:
            st = os.stat(file_path)
            filename = os.path.basename(file_path)
            timestamp = filename.replace('.json', '')
            file_list.append({
                "timestamp": timestamp,
                "file": file_path,
                "size": st.st_size,
                "mtime": datetime.fromtimestamp(st.st_mtime, tz=timezone.utc)
                           .isoformat().replace("+00:00", "Z"),
            })
        except Exception:
            continue
    file_list.sort(key=lambda x: x["timestamp"], reverse=True)
    return {"files": file_list[:limit]}


def read_period_history_daily_sample(root: str, days: int, sample_hour: int = 12) -> List[List[Dict[str, Any]]]:
    """Period History 파일에서 최근 N일 동안 매일 특정 시간(sample_hour)의 데이터만 조회"""
    if not os.path.exists(root):
        logging.warning(f"Period History 디렉토리가 없습니다: {root}")
        return []
    now = datetime.now(timezone.utc)
    results: List[Tuple[str, List[List[Dict[str, Any]]]]] = []
    seen_dates = set()
    pattern = os.path.join(root, "*.json")
    all_files = glob.glob(pattern)
    logging.info(f"Period History 파일 조회: {root}에서 {len(all_files)}개 파일 발견")
    if not all_files:
        logging.warning(f"Period History 파일이 없습니다: {root}")
        return []
    file_timestamps = []
    for file_path in all_files:
        try:
            filename = os.path.basename(file_path)
            timestamp_str = filename.replace('.json', '')
            dt = datetime.fromisoformat(timestamp_str.replace('Z', '+00:00')).astimezone(timezone.utc)
            file_timestamps.append((dt, file_path))
        except Exception:
            continue
    if not file_timestamps:
        return []
    files_by_date: Dict[str, List[Tuple[datetime, str]]] = {}
    for dt, file_path in file_timestamps:
        date_key = dt.strftime("%Y-%m-%d")
        if date_key not in files_by_date:
            files_by_date[date_key] = []
        files_by_date[date_key].append((dt, file_path))
    for day_offset in range(days):
        target_date = (now.replace(hour=sample_hour, minute=0, second=0, microsecond=0)
                      - timedelta(days=day_offset))
        date_key = target_date.strftime("%Y-%m-%d")
        if date_key in seen_dates:
            continue
        target_file = None
        if date_key in files_by_date:
            date_files = files_by_date[date_key]
            target_timestamp = target_date.strftime("%Y-%m-%dT%H:%M:%SZ")
            for dt, file_path in date_files:
                file_timestamp = dt.strftime("%Y-%m-%dT%H:%M:%SZ")
                if file_timestamp == target_timestamp:
                    target_file = file_path
                    break
            if not target_file:
                best_file = None
                min_diff = float('inf')
                target_hour = sample_hour
                for dt, file_path in date_files:
                    hour_diff = abs(dt.hour - target_hour)
                    if hour_diff < min_diff:
                        min_diff = hour_diff
                        best_file = file_path
                if best_file:
                    target_file = best_file
        if not target_file or not os.path.exists(target_file):
            continue
        seen_dates.add(date_key)
        try:
            with open(target_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
                if isinstance(data, list):
                    results.append((date_key, data))
                    logging.info(f"Period History 파일 읽기 성공: {target_file}, 엔티티 배열 {len(data)}개")
        except Exception as e:
            logging.warning(f"Period History 파일 읽기 실패: {target_file}, 에러: {e}")
            continue
    results.sort(key=lambda x: x[0])
    logging.info(f"Period History 일일 샘플 조회: {len(results)}개 날짜의 데이터 수집")
    combined_data: List[List[Dict[str, Any]]] = []
    for date_key, data in results:
        combined_data.extend(data)
    logging.info(f"Period History 일일 샘플 조회 완료: 총 {len(combined_data)}개 엔티티 배열 반환")
    return combined_data


def read_period_history_daily_hourly(root: str, date_str: str, devices_file_path: Optional[str] = None) -> Dict[str, Any]:
    """특정 날짜의 모든 시간대(0시~23시) Period History 파일을 조회"""
    if not os.path.exists(root):
        logging.warning(f"Period History 디렉토리가 없습니다: {root}")
        return {"date": date_str, "hours": {}}
    try:
        target_date = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError:
        logging.warning(f"잘못된 날짜 형식: {date_str}")
        return {"date": date_str, "hours": {}}
    entity_filter = _load_devices_entity_ids(devices_file_path)
    pattern = os.path.join(root, "*.json")
    all_files = glob.glob(pattern)
    if not all_files:
        logging.warning(f"Period History 파일이 없습니다: {root}")
        return {"date": date_str, "hours": {}}
    hours_data: Dict[str, List[List[Dict[str, Any]]]] = {}
    for file_path in all_files:
        try:
            filename = os.path.basename(file_path)
            timestamp_str = filename.replace('.json', '')
            dt = datetime.fromisoformat(timestamp_str.replace('Z', '+00:00')).astimezone(timezone.utc)
            if dt.date() == target_date.date():
                hour_key = dt.strftime("%H")
                try:
                    with open(file_path, 'r', encoding='utf-8') as f:
                        data = json.load(f)
                        if isinstance(data, list):
                            filtered_data = []
                            for events in data:
                                if not isinstance(events, list) or len(events) == 0:
                                    continue
                                if entity_filter:
                                    first_event = events[0] if events else None
                                    if first_event and isinstance(first_event, dict):
                                        entity_id = first_event.get('entity_id')
                                        if entity_id and entity_id not in entity_filter:
                                            continue
                                filtered_data.append(events)
                            if filtered_data:
                                hours_data[hour_key] = filtered_data
                except Exception as e:
                    logging.warning(f"Period History 파일 읽기 실패: {file_path}, 에러: {e}")
                    continue
        except Exception:
            continue
    logging.info(f"Period History 일일 시간대별 조회: {date_str}, {len(hours_data)}개 시간대 파일 발견")
    return {"date": date_str, "hours": hours_data}
