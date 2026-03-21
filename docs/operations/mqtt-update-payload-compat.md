# MQTT 업데이트 페이로드 호환성 가이드

## 업데이트 토픽

```
matterhub/update/specific/{mqtt_id}
```

> `matterhub/{hub_id}/git/update` 레거시 토픽은 제거됨 (커밋 8d6653e 이후).

## 필수 페이로드 필드

```json
{
  "command": "git_update",
  "update_id": "deploy-XXXXX",
  "branch": "master",
  "force_update": false,
  "endpoint": "/update",
  "method": "post"
}
```

### `endpoint` / `method` 필드가 필요한 이유

커밋 `4b4d5b8` 이전 구버전 `mqtt_callback`은 수신된 모든 MQTT 메시지에서 `endpoint` 필드를 읽어 `endpoint.startswith(...)` 로 라우팅한다. `endpoint` 필드가 없으면 `NoneType.startswith()` → **크래시** 발생.

`endpoint`/`method` 필드를 포함하면:
1. 구버전 크래시 회피 → update 핸들러 도달
2. 구버전 `update_server.sh`가 `git pull` + PM2 restart 실행
3. PM2 restart 후 새 코드의 `mqtt.py` 시작 → cert 심링크 자동 생성 → 정상 동작

### Lambda 코드 수정 필요

클라우드 Lambda에서 `matterhub/update/specific/{mqtt_id}` 토픽으로 발행할 때, 반드시 `endpoint`와 `method` 필드를 포함해야 구버전 디바이스에서도 정상 동작한다.

## 응답 토픽

업데이트 완료 후 디바이스가 발행:

```
matterhub/{hub_id}/update/response
```

페이로드:
```json
{
  "update_id": "deploy-XXXXX",
  "hub_id": "whatsmatter-nipa_SN-XXXXXXXXXX",
  "timestamp": 1711100000,
  "command": "git_update",
  "status": "success",
  "result": { ... }
}
```

`status` 값: `"processing"` (수신 즉시) → `"success"` 또는 `"failed"` (완료 후)
