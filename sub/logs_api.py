"""
로그 조회 API 유틸리티
NDJSON 파일 기반 로그 조회 및 필터링
"""
import os
import json
import base64
from datetime import datetime, timezone, timedelta
from typing import Iterator, List, Dict, Any, Optional, Tuple
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
    # 시간 범위 계산
    f, t = within_time_window(from_str, to_str, default_window_hours)
    
    # 커서 처리
    start_path: Optional[str] = None
    start_off = 0
    if cursor:
        try:
            cur = decode_cursor(cursor)
            start_path = str(cur.get("path"))
            start_off = int(cur.get("offset", 0))
        except Exception:
            raise ValueError("invalid_cursor")
    
    # 필터 준비
    device_filter = set(device_ids) if device_ids else None
    
    items: List[Dict[str, Any]] = []
    next_cursor: Optional[str] = None
    
    # 파일 순회
    for path in iter_files(f, t, root):
        # 커서 위치 존중
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
    
    # 수집
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
    
    # 최근 N개 추출 및 역순
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
    
    # 파일 스캔
    for path in iter_files(f, t, root):
        # 파일명에서 시간 추출
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
    
    # 리스트로 변환
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
    """
    최근 N일 동안 매일 특정 시간(sample_hour)의 로그만 조회
    예: 최근 일주일 동안 매일 12:00시 로그만 조회
    """
    now = datetime.now(timezone.utc)
    # 오늘의 sample_hour로 시작
    start_date = now.replace(hour=sample_hour, minute=0, second=0, microsecond=0)
    
    device_filter = set(device_ids) if device_ids else None
    
    items: List[Dict[str, Any]] = []
    seen_dates = set()  # 중복 날짜 체크용
    
    # 역순으로 날짜 순회 (오늘부터 과거로)
    for day_offset in range(days):
        target_date = start_date - timedelta(days=day_offset)
        date_key = target_date.strftime("%Y-%m-%d")
        
        # 이미 처리한 날짜는 스킵
        if date_key in seen_dates:
            continue
        
        # 해당 날짜의 sample_hour 파일 경로
        target_path = hour_path(target_date, root)
        
        if not os.path.exists(target_path):
            # 파일이 없으면 스킵 (해당 날짜 데이터 없음)
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


def read_period_history_json(timestamp: Optional[str], root: str) -> List[List[Dict[str, Any]]]:
    """
    Period History 모드로 저장된 JSON 파일을 읽어서 반환
    HA History API와 동일한 응답 형식 (중첩 배열)
    
    Args:
        timestamp: ISO8601 형식의 타임스탬프 (예: "2025-11-03T05:00:00Z")
                   None이면 가장 최근 파일 반환
        root: 로그 디렉토리 경로
    
    Returns:
        JSON 데이터 (중첩 배열) - 파일이 없으면 빈 배열 []
    """
    import glob
    
    if timestamp:
        # 특정 타임스탬프의 파일 경로
        file_path = os.path.join(root, f"{timestamp}.json")
        if not os.path.exists(file_path):
            return []  # HA History API와 동일하게 빈 배열 반환
        
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
                if isinstance(data, list):
                    return data
                return []  # 잘못된 형식이면 빈 배열
        except Exception as e:
            # 에러 발생 시 빈 배열 반환
            return []
    else:
        # 가장 최근 파일 찾기
        pattern = os.path.join(root, "*.json")
        files = glob.glob(pattern)
        
        if not files:
            return []  # 파일이 없으면 빈 배열 반환
        
        # 파일명에서 타임스탬프 추출하여 정렬 (최신순)
        def get_timestamp_from_path(path: str) -> str:
            filename = os.path.basename(path)
            return filename.replace('.json', '')
        
        files.sort(key=get_timestamp_from_path, reverse=True)
        latest_file = files[0]
        
        try:
            with open(latest_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
                if isinstance(data, list):
                    return data
                return []  # 잘못된 형식이면 빈 배열
        except Exception as e:
            # 에러 발생 시 빈 배열 반환
            return []


def list_period_history_files(root: str, limit: int = 10) -> Dict[str, Any]:
    """
    Period History 모드로 저장된 JSON 파일 목록 조회
    
    Args:
        root: 로그 디렉토리 경로
        limit: 반환할 최대 파일 개수
    
    Returns:
        {"files": [...]}
    """
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
                "mtime": datetime.fromtimestamp(st.st_mtime, tz=timezone.utc)
                           .isoformat().replace("+00:00", "Z"),
            })
        except Exception:
            continue
    
    # 타임스탬프 기준으로 정렬 (최신순)
    file_list.sort(key=lambda x: x["timestamp"], reverse=True)
    
    return {"files": file_list[:limit]}


def read_period_history_daily_sample(root: str, days: int, sample_hour: int = 12) -> List[List[Dict[str, Any]]]:
    """
    Period History 파일에서 최근 N일 동안 매일 특정 시간(sample_hour)의 데이터만 조회
    예: 최근 일주일 동안 매일 12:00시 데이터만 조회
    
    Args:
        root: Period History 파일 저장 경로
        days: 조회할 일수
        sample_hour: 대표 시간 (0-23, 기본값: 12)
    
    Returns:
        HA History API 형식의 중첩 배열 (날짜별로 정렬)
    """
    import glob
    
    now = datetime.now(timezone.utc)
    results: List[Tuple[str, List[List[Dict[str, Any]]]]] = []  # (date_key, data)
    seen_dates = set()
    
    # 모든 파일 목록 가져오기
    pattern = os.path.join(root, "*.json")
    all_files = glob.glob(pattern)
    
    # 파일명에서 타임스탬프 추출하여 정렬
    file_timestamps = []
    for file_path in all_files:
        try:
            filename = os.path.basename(file_path)
            timestamp_str = filename.replace('.json', '')
            # ISO8601 형식: "2025-11-06T02:00:00Z"
            dt = datetime.fromisoformat(timestamp_str.replace('Z', '+00:00')).astimezone(timezone.utc)
            file_timestamps.append((dt, file_path))
        except Exception:
            continue
    
    # 타임스탬프 기준으로 정렬 (최신순)
    file_timestamps.sort(key=lambda x: x[0], reverse=True)
    
    # 역순으로 날짜 순회 (오늘부터 과거로)
    for day_offset in range(days):
        target_date = (now.replace(hour=sample_hour, minute=0, second=0, microsecond=0) 
                      - timedelta(days=day_offset))
        date_key = target_date.strftime("%Y-%m-%d")
        
        # 이미 처리한 날짜는 스킵
        if date_key in seen_dates:
            continue
        
        # 해당 날짜의 sample_hour 파일 찾기
        target_timestamp = target_date.strftime("%Y-%m-%dT%H:%M:%SZ")
        target_file = None
        
        for dt, file_path in file_timestamps:
            file_timestamp = dt.strftime("%Y-%m-%dT%H:%M:%SZ")
            if file_timestamp == target_timestamp:
                target_file = file_path
                break
        
        if not target_file or not os.path.exists(target_file):
            # 파일이 없으면 스킵 (해당 날짜 데이터 없음)
            continue
        
        seen_dates.add(date_key)
        
        try:
            with open(target_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
                if isinstance(data, list):
                    results.append((date_key, data))
        except Exception:
            continue
    
    # 날짜순으로 정렬 (오래된 것부터)
    results.sort(key=lambda x: x[0])
    
    # 중첩 배열로 합치기 (날짜별로 그룹화)
    combined_data: List[List[Dict[str, Any]]] = []
    for date_key, data in results:
        combined_data.extend(data)
    
    return combined_data
