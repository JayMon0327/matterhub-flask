# 플랫폼 설치 완료 보고서 (2026-04-01)

## 개요

MatterHub 플랫폼(OTBR + Home Assistant + Matter Server) 설치 스킬을 개발하고,
3대의 라즈베리파이에 실전 적용하여 검증 완료.

## 작업 내역

### 1. 스킬 개발

기존 `/platform-install` (전체 원스톱)을 SD카드 대량 복제 워크플로우에 맞게 2개 스킬로 분리.

| 스킬 | 용도 | 소요 시간 |
|------|------|-----------|
| `/platform-base-image` | 공통 설치 (Docker + OTBR 빌드 + Docker 이미지 pull + mDNS 수정) | ~25분 |
| `/platform-activate` | 장비별 활성화 (Thread 초기화 + HA/Matter 통합 등록) | ~3분 (HA 초기설정 제외) |
| `/platform-install` | 전체 원스톱 (기존, 유지) | ~30분 |

### 2. 스킬 개선 사항 (실전 적용 중 발견)

- **Thread 초기화 전 stop/ifconfig down 필수**: OTBR 자동 시작 시 기본 dataset 잔존 방지
- **ip6tables 규칙 activate 시 확인/복구**: 베이스 이미지에서 영구화 누락 대응
- **SSH known_hosts 갱신**: 새 SD카드 장착 시 호스트 키 충돌 방지
- **expect + sudo 주의사항**: `&&` 연결 금지, `| tail` 금지, 스크립트 파일 사용 권장
- **호스트 정보 환경변수 처리**: 하드코딩된 SSH user/pw 제거 → 스킬 시작 시 사용자에게 요청

### 3. 장비 설치 결과

| 장비 | IP | 설치 방식 | 결과 |
|------|-----|-----------|------|
| test 1호 | 192.168.1.94 | base-image + activate | 정상 |
| test 2호 | 192.168.1.101 | base-image | 정상 (베이스 이미지 완료) |
| test 3호 | 192.168.1.96 | (기존 SD카드) activate | 정상 |
| test 4호 | 192.168.1.97 | (복제 SD카드) activate | 정상 |

### 4. 각 장비 최종 검증 항목

| 항목 | 94 | 96 | 97 |
|------|:---:|:---:|:---:|
| OTBR leader, ch15 | OK | OK | OK |
| otbr-agent active | OK | OK | OK |
| REST API "leader" | OK | OK | OK |
| HA HTTP 200 | OK | OK | OK |
| Docker 컨테이너 2개 Up | OK | OK | OK |
| thread/otbr/matter loaded | OK | OK | OK |
| avahi deny wpan0 | OK | OK | OK |
| ip6tables 5353 DROP | OK | OK | OK |

### 5. 트러블슈팅 문서

`docs/operations/platform-install-troubleshooting-20260401.md`에 7건 기록:

1. OTBR Thread 초기화 시 detached 상태
2. ip6tables 규칙 재부팅 후 소실
3. SSH known_hosts 호스트 키 충돌
4. expect에서 sudo 비밀번호 캡처 실패
5. expect + sudo + tail 조합 실패
6. Matter 통합 등록 시 abort 응답
7. Matter Server 시작 직후 mDNS advertise 실패

## 생성/수정된 파일

| 파일 | 작업 |
|------|------|
| `.claude/skills/platform-base-image/SKILL.md` | 신규 생성 |
| `.claude/skills/platform-activate/SKILL.md` | 신규 생성 |
| `.claude/skills/platform-install/SKILL.md` | 수정 (환경변수 처리, 분리 스킬 안내) |
| `docs/operations/platform-install-troubleshooting-20260401.md` | 신규 생성 |
| `docs/operations/platform-install-report-20260401.md` | 신규 생성 (본 문서) |
| `docs/platform-install.md` | 수정 (최신화) |

## 워크플로우 요약

```
[새 Pi] → /platform-base-image → SD카드 복제 × N장
                                      ↓
              [각 Pi에 장착] → /platform-activate → 운영 준비 완료
```
