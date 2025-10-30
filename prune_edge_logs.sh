#!/usr/bin/env bash
set -euo pipefail

# 로그 자동 삭제 스크립트
# 총량 기반으로 오래된 파일부터 삭제

TARGET_DIR="${EDGE_LOG_ROOT:-/var/log/edge-history}"
CAP_BYTES=${CAP_BYTES:-$((20 * 1024 * 1024 * 1024))}  # 기본 20 GiB

# 로깅 함수
log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"
}

# 현재 디렉토리 크기 계산 (바이트)
current_size() {
    if [ ! -d "$TARGET_DIR" ]; then
        echo 0
        return
    fi
    du -sb "$TARGET_DIR" 2>/dev/null | awk '{print $1}' || echo 0
}

main() {
    log "로그 정리 스크립트 시작"
    log "대상 디렉토리: $TARGET_DIR"
    log "크기 제한: $CAP_BYTES bytes ($(($CAP_BYTES / 1024 / 1024 / 1024)) GiB)"
    
    if [ ! -d "$TARGET_DIR" ]; then
        log "경고: 대상 디렉토리가 존재하지 않습니다: $TARGET_DIR"
        exit 0
    fi
    
    size=$(current_size)
    log "현재 디렉토리 크기: $size bytes ($(($size / 1024 / 1024 / 1024)) GiB)"
    
    if [ "$size" -le "$CAP_BYTES" ]; then
        log "크기 제한 미초과, 종료"
        exit 0
    fi
    
    log "크기 제한 초과, 오래된 파일부터 삭제 시작"
    
    # 오래된 파일부터 정렬 (파일명 기반: yyyy/mm/dd/HH.ndjson)
    # find로 파일 찾고, 경로를 파싱하여 시간순 정렬
    deleted_count=0
    deleted_size=0
    
    while [ "$size" -gt "$CAP_BYTES" ]; do
        # 가장 오래된 파일 찾기 (시간순)
        oldest_file=""
        oldest_time=""
        
        # 모든 ndjson 파일 찾기
        while IFS= read -r file; do
            if [ -z "$file" ]; then
                continue
            fi
            
            # 파일명에서 시간 추출: yyyy/mm/dd/HH.ndjson
            if [[ "$file" =~ ([0-9]{4})/([0-9]{1,2})/([0-9]{1,2})/([0-9]{1,2})\.ndjson$ ]]; then
                year="${BASH_REMATCH[1]}"
                month=$(printf "%02d" "${BASH_REMATCH[2]}")
                day=$(printf "%02d" "${BASH_REMATCH[3]}")
                hour=$(printf "%02d" "${BASH_REMATCH[4]}")
                
                # 시간 비교용 타임스탬프 생성 (YYYYMMDDHH)
                file_time="${year}${month}${day}${hour}"
                
                if [ -z "$oldest_file" ] || [ "$file_time" -lt "$oldest_time" ]; then
                    oldest_file="$file"
                    oldest_time="$file_time"
                fi
            fi
        done < <(find "$TARGET_DIR" -type f -name '*.ndjson' -print 2>/dev/null || true)
        
        if [ -z "$oldest_file" ]; then
            log "삭제할 파일이 더 이상 없습니다"
            break
        fi
        
        # 파일 삭제
        file_size=$(stat -f%z "$oldest_file" 2>/dev/null || stat -c%s "$oldest_file" 2>/dev/null || echo 0)
        if rm -f -- "$oldest_file"; then
            deleted_count=$((deleted_count + 1))
            deleted_size=$((deleted_size + file_size))
            log "삭제: $oldest_file ($(($file_size / 1024)) KB)"
        else
            log "경고: 파일 삭제 실패: $oldest_file"
            break
        fi
        
        # 현재 크기 재계산
        size=$(current_size)
    done
    
    log "정리 완료: $deleted_count개 파일 삭제, $(($deleted_size / 1024 / 1024)) MB 정리"
    log "최종 디렉토리 크기: $size bytes ($(($size / 1024 / 1024)) MB)"
}

main "$@"
