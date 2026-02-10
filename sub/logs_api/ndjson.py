"""
NDJSON 로그 파일 조회: read_logs, read_tail_logs, get_log_stats, list_log_files, read_daily_sample_logs
"""
import os
import json
import pathlib
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Any, Optional

from .utils import (
    within_time_window,
    iter_files,
    hour_path,
    decode_cursor,
    encode_cursor,
    filter_record,
)


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
    from .utils import hour_range
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
