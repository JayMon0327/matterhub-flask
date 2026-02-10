"""
로그 조회 API 유틸리티: 시간·경로·커서·필터
"""
import os
import json
import base64
from datetime import datetime, timezone, timedelta
from typing import Iterator, Dict, Any, Optional, Tuple


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
