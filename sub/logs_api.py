"""
로그 조회 API 유틸리티
NDJSON 파일 기반 로그 조회 및 필터링 (단일 파일 버전)
"""
import os
import json
import base64
from datetime import datetime, timezone, timedelta
from typing import Iterator, List, Dict, Any, Optional, Tuple, Set
import pathlib


def to_utc(dt_str: str) -> datetime:
    """ISO8601 문자열을 UTC datetime으로 변환"""
    s = dt_str.strip().replace("Z", "+00:00")
    return datetime.fromisoformat(s).astimezone(timezone.utc)


def hour_floor(dt: datetime) -> datetime:
    """시간을 정시로 내림"""
    return dt.astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0)


def hour_range(from_dt: datetime, to_dt: datetime) -> Iterator[datetime]:
    """시간 범위에서 각 시간 생성"""
    cur = hour_floor(from_dt)
    end = hour_floor(to_dt)
    while cur <= end:
        yield cur
        cur += timedelta(hours=1)


def hour_path(dt: datetime, root: str) -> str:
    """시간에 해당하는 파일 경로 반환"""
    return os.path.join(root, dt.strftime("%Y/%m/%d/%H.ndjson"))


def iter_files(from_dt: datetime, to_dt: datetime, root: str) -> Iterator[str]:
    """시간 범위에 해당하는 파일 목록 생성"""
    for h in hour_range(from_dt, to_dt):
        p = hour_path(h, root)
        if os.path.exists(p):
            yield p


def decode_cursor(c: str) -> Dict[str, Any]:
    """cursor를 디코딩"""
    return json.loads(base64.b64decode(c).decode("utf-8"))


def encode_cursor(path: str, offset: int) -> str:
    """cursor 인코딩"""
    payload = {"path": path, "offset": int(offset)}
    return base64.b64encode(json.dumps(payload).encode("utf-8")).decode("utf-8")


def within_time_window(from_str: Optional[str], to_str: Optional[str],
                       default_hours: int = 24) -> Tuple[datetime, datetime]:
    """시간 범위 파싱 및 기본값 적용"""
    now = datetime.now(timezone.utc)
    f = to_utc(from_str) if from_str else (hour_floor(now) - timedelta(hours=default_hours))
    t = to_utc(to_str) if to_str else now
    if t < f:
        f, t = t, f
    return f, t


def filter_record(obj: Dict[str, Any], raw_line: str,
                  device_filter: Optional[set] = None,
                  status_filter: Optional[str] = None,
                  query_filter: Optional[str] = None) -> bool:
    """레코드 필터링"""
    if device_filter and str(obj.get("device_id")) not in device_filter:
        return False
    if status_filter is not None and str(obj.get("status")) != str(status_filter):
        return False
    if query_filter and query_filter not in raw_line:
        return False
    return True


def read_logs(from_str: Optional[str], to_str: Optional[str],
              device_ids: List[str], status: Optional[str],
              q: Optional[str], cursor: Optional[str],
              limit: int, root: str, default_window_hours: int = 24) -> Dict[str, Any]:
    """로그 읽기 메인 함수"""
    f, t = within_time_window(from_str, to_str, default_window_hours)
    start_path: Optional[str] = None
    start_off = 0
    if cursor:
        try:
            cur = decode_cursor(cursor)
            start_path = str(cur.get("path"))
            start_off = int(cur.get("offset", 0))
        except Exception:
            raise ValueError("invalid_cursor")
    device_filter = set(device_ids) if device_ids else None
    items: List[Dict[str, Any]] = []
    next_cursor: Optional[str] = None
    for path in iter_files(f, t, root):
        offset = 0
        if start_path:
            if path < start_path:
                continue
            if path == start_path:
                offset = start_off
        try:
            with open(path, "r", encoding="utf-8") as fp:
                if offset:
                    fp.seek(offset)
                while True:
                    pos = fp.tell()
                    line = fp.readline()
                    if not line:
                        break
                    try:
                        obj = json.loads(line)
                    except Exception:
                        continue
                    if filter_record(obj, line, device_filter, status, q):
                        items.append(obj)
                        if len(items) >= limit:
                            next_cursor = encode_cursor(path, fp.tell())
                            return {"items": items, "next_cursor": next_cursor}
        except FileNotFoundError:
            continue
    return {"items": items, "next_cursor": next_cursor}


def read_tail_logs(since_sec: int, device_ids: List[str],
                   status: Optional[str], q: Optional[str],
                   limit: int, root: str) -> Dict[str, Any]:
    """최근 로그 읽기 (tail)"""
    now = datetime.now(timezone.utc)
    f = now - timedelta(seconds=since_sec)
    device_filter = set(device_ids) if device_ids else None
    buf: List[Dict[str, Any]] = []
    for path in iter_files(f, now, root):
        try:
            with open(path, "r", encoding="utf-8") as fp:
                for line in fp:
                    try:
                        obj = json.loads(line)
                    except Exception:
                        continue
                    if filter_record(obj, line, device_filter, status, q):
                        buf.append(obj)
        except FileNotFoundError:
            continue
    items = buf[-limit:] if len(buf) > limit else buf
    items.reverse()
    return {"items": items}


def get_log_stats(from_str: Optional[str], to_str: Optional[str],
                  root: str, default_window_hours: int = 24) -> Dict[str, Any]:
    """로그 통계"""
    f, t = within_time_window(from_str, to_str, default_window_hours)
    buckets: Dict[str, int] = {}
    for h in hour_range(f, t):
        buckets[h.isoformat().replace("+00:00", "Z")] = 0
    for path in iter_files(f, t, root):
        path_obj = pathlib.Path(path)
        parts = path_obj.parts
        if len(parts) >= 4:
            try:
                year = int(parts[-4])
                month = int(parts[-3])
                day = int(parts[-2])
                hour = int(path_obj.stem)
                h = datetime(year, month, day, hour, tzinfo=timezone.utc)
                key = h.isoformat().replace("+00:00", "Z")
                try:
                    with open(path, "r", encoding="utf-8") as fp:
                        for _ in fp:
                            buckets[key] = buckets.get(key, 0) + 1
                except FileNotFoundError:
                    continue
            except (ValueError, IndexError):
                continue
    items = [{"hour": k, "count": v} for k, v in buckets.items()]
    items.sort(key=lambda x: x["hour"])
    return {"items": items}


def list_log_files(from_str: Optional[str], to_str: Optional[str],
                   root: str, default_window_hours: int = 24) -> Dict[str, Any]:
    """로그 파일 목록"""
    f, t = within_time_window(from_str, to_str, default_window_hours)
    files = []
    for path in iter_files(f, t, root):
        try:
            st = os.stat(path)
            files.append({
                "path": path,
                "size": st.st_size,
                "mtime": datetime.fromtimestamp(st.st_mtime, tz=timezone.utc)
                       .isoformat().replace("+00:00", "Z"),
            })
        except FileNotFoundError:
            continue
    return {"files": files}


def read_daily_sample_logs(days: int, device_ids: List[str],
                           status: Optional[str], q: Optional[str],
                           limit: int, root: str, sample_hour: int = 12) -> Dict[str, Any]:
    """최근 N일 동안 매일 특정 시간(sample_hour)의 로그만 조회"""
    now = datetime.now(timezone.utc)
    start_date = now.replace(hour=sample_hour, minute=0, second=0, microsecond=0)
    device_filter = set(device_ids) if device_ids else None
    items: List[Dict[str, Any]] = []
    seen_dates = set()
    for day_offset in range(days):
        target_date = start_date - timedelta(days=day_offset)
        date_key = target_date.strftime("%Y-%m-%d")
        if date_key in seen_dates:
            continue
        target_path = hour_path(target_date, root)
        if not os.path.exists(target_path):
            continue
        seen_dates.add(date_key)
        try:
            with open(target_path, "r", encoding="utf-8") as fp:
                for line in fp:
                    try:
                        obj = json.loads(line)
                    except Exception:
                        continue
                    if filter_record(obj, line, device_filter, status, q):
                        items.append(obj)
                        if len(items) >= limit:
                            return {"items": items}
        except FileNotFoundError:
            continue
    return {"items": items}


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
    """Period History JSON 파일 조회. 파일 없으면 빈 배열."""
    import glob
    import logging
    if not os.path.exists(root):
        logging.warning(f"Period History 디렉토리가 없습니다: {root}")
        return []
    entity_filter = _load_devices_entity_ids(devices_file_path)
    if timestamp:
        file_path = os.path.join(root, f"{timestamp}.json")
        if not os.path.exists(file_path):
            return []
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
                if isinstance(data, list):
                    if entity_filter:
                        filtered_data = [events for events in data
                                         if isinstance(events, list) and events
                                         and isinstance(events[0], dict)
                                         and events[0].get('entity_id') in entity_filter]
                        data = filtered_data
                    return data
                return []
        except Exception:
            return []
    else:
        pattern = os.path.join(root, "*.json")
        files = glob.glob(pattern)
        if not files:
            return []
        files.sort(key=lambda p: os.path.basename(p).replace('.json', ''), reverse=True)
        latest_file = files[0]
        try:
            with open(latest_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
                if isinstance(data, list):
                    if entity_filter:
                        filtered_data = [events for events in data
                                         if isinstance(events, list) and events
                                         and isinstance(events[0], dict)
                                         and events[0].get('entity_id') in entity_filter]
                        data = filtered_data
                    return data
                return []
        except Exception:
            return []


def list_period_history_files(root: str, limit: int = 10) -> Dict[str, Any]:
    """Period History JSON 파일 목록"""
    import glob
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
                "mtime": datetime.fromtimestamp(st.st_mtime, tz=timezone.utc).isoformat().replace("+00:00", "Z"),
            })
        except Exception:
            continue
    file_list.sort(key=lambda x: x["timestamp"], reverse=True)
    return {"files": file_list[:limit]}


def read_period_history_daily_sample(root: str, days: int, sample_hour: int = 12) -> List[List[Dict[str, Any]]]:
    """최근 N일 매일 sample_hour 시각 데이터 조회"""
    import glob
    import logging
    if not os.path.exists(root):
        return []
    now = datetime.now(timezone.utc)
    results: List[Tuple[str, List[List[Dict[str, Any]]]]] = []
    seen_dates = set()
    pattern = os.path.join(root, "*.json")
    all_files = glob.glob(pattern)
    if not all_files:
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
        target_date = (now.replace(hour=sample_hour, minute=0, second=0, microsecond=0) - timedelta(days=day_offset))
        date_key = target_date.strftime("%Y-%m-%d")
        if date_key in seen_dates:
            continue
        target_file = None
        if date_key in files_by_date:
            date_files = files_by_date[date_key]
            target_timestamp = target_date.strftime("%Y-%m-%dT%H:%M:%SZ")
            for dt, file_path in date_files:
                if dt.strftime("%Y-%m-%dT%H:%M:%SZ") == target_timestamp:
                    target_file = file_path
                    break
            if not target_file:
                best_file = min(date_files, key=lambda x: abs(x[0].hour - sample_hour))
                target_file = best_file[1]
        if not target_file or not os.path.exists(target_file):
            continue
        seen_dates.add(date_key)
        try:
            with open(target_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
                if isinstance(data, list):
                    results.append((date_key, data))
        except Exception:
            continue
    results.sort(key=lambda x: x[0])
    combined_data: List[List[Dict[str, Any]]] = []
    for _, data in results:
        combined_data.extend(data)
    return combined_data


def read_period_history_daily_hourly(root: str, date_str: str, devices_file_path: Optional[str] = None) -> Dict[str, Any]:
    """특정 날짜의 0~23시 Period History 조회"""
    import glob
    import logging
    if not os.path.exists(root):
        return {"date": date_str, "hours": {}}
    try:
        target_date = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError:
        return {"date": date_str, "hours": {}}
    entity_filter = _load_devices_entity_ids(devices_file_path)
    pattern = os.path.join(root, "*.json")
    all_files = glob.glob(pattern)
    if not all_files:
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
                                    if not (first_event and isinstance(first_event, dict) and first_event.get('entity_id') in entity_filter):
                                        continue
                                filtered_data.append(events)
                            if filtered_data:
                                hours_data[hour_key] = filtered_data
                except Exception:
                    continue
        except Exception:
            continue
    return {"date": date_str, "hours": hours_data}
