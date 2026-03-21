from __future__ import annotations

import json
import os
import queue
import subprocess
import threading
import time
from typing import Any, Dict, Optional

from awscrt import mqtt

from . import runtime, settings


update_queue: "queue.Queue[Dict[str, Any]]" = queue.Queue()
update_queue_lock = threading.Lock()
is_processing_update = False


def _publish_response(payload: Dict[str, Any]) -> None:
    connection = runtime.get_connection()
    matterhub_id = settings.MATTERHUB_ID
    if connection is None or not matterhub_id:
        print("❌ 업데이트 응답 전송 실패: MQTT 연결 또는 matterhub_id 없음")
        return

    response_topic = f"matterhub/{matterhub_id}/update/response"
    try:
        pub_future, _ = connection.publish(
            topic=response_topic,
            payload=json.dumps(payload),
            qos=mqtt.QoS.AT_LEAST_ONCE,
        )
        try:
            pub_future.result(timeout=10)
        except Exception as exc:
            print(f"⚠️ PUBACK 대기 실패: {exc}")
    except Exception as exc:
        print(f"❌ 업데이트 응답 전송 중 오류: {exc}")


def send_immediate_response(message: Dict[str, Any], status: str = "processing") -> None:
    matterhub_id = settings.MATTERHUB_ID
    payload = {
        "update_id": message.get("update_id"),
        "hub_id": matterhub_id,
        "timestamp": int(time.time()),
        "command": "git_update",
        "status": status,
        "message": f"Update command received and {status}",
    }
    _publish_response(payload)
    print(f"📤 즉시 응답 전송: {status} - {message.get('update_id')}")


def send_final_response(message: Dict[str, Any], result: Dict[str, Any]) -> None:
    matterhub_id = settings.MATTERHUB_ID
    payload = {
        "update_id": message.get("update_id"),
        "hub_id": matterhub_id,
        "timestamp": int(time.time()),
        "command": "git_update",
        "status": "success" if result.get("success") else "failed",
        "result": result,
    }
    _publish_response(payload)
    print(
        f"✅ 최종 응답 전송 완료: {message.get('update_id')}"
    )
    print(f"결과: {'성공' if result.get('success') else '실패'}")


def send_error_response(message: Dict[str, Any], error_msg: str) -> None:
    matterhub_id = settings.MATTERHUB_ID
    payload = {
        "update_id": message.get("update_id"),
        "hub_id": matterhub_id,
        "timestamp": int(time.time()),
        "command": "git_update",
        "status": "failed",
        "error": error_msg,
    }
    _publish_response(payload)
    print(f"❌ 에러 응답 전송: {message.get('update_id')} - {error_msg}")


def _find_update_script() -> Optional[str]:
    """update_server.sh 경로 탐색"""
    for path in [
        os.path.join(os.path.dirname(__file__), "../device_config/update_server.sh"),
        os.path.join(os.path.dirname(__file__), "../update_server.sh"),  # 구형 레이아웃 (루트에 위치)
        "./device_config/update_server.sh",
        "./update_server.sh",  # 구형 레이아웃 (루트에 위치)
        "/opt/matterhub/device_config/update_server.sh",  # .deb 설치 경로
        "/srv/matterhub/device_config/update_server.sh",
    ]:
        resolved = os.path.abspath(path)
        if os.path.exists(resolved):
            return resolved
    return None


def _wait_for_pid(pid: int, timeout: int = 300) -> None:
    """PID가 종료될 때까지 대기"""
    waited = 0
    while waited < timeout:
        try:
            result = subprocess.run(
                ["ps", "-p", str(pid)],
                capture_output=True,
                text=True,
            )
            if result.returncode != 0:
                return  # 프로세스 종료됨
        except Exception:
            return
        time.sleep(10)
        waited += 10


def _read_status_file(path: str) -> Optional[Dict[str, Any]]:
    """상태 파일 읽기"""
    try:
        if os.path.exists(path):
            with open(path, "r") as f:
                return json.load(f)
    except Exception as exc:
        print(f"⚠️ 상태 파일 읽기 실패: {exc}")
    return None


def execute_external_update_script(
    branch: str = "master",
    force_update: bool = False,
    update_id: str = "unknown",
    skip_restart: bool = False,
) -> Dict[str, Any]:
    try:
        script_path = _find_update_script()

        if not script_path:
            return {
                "success": False,
                "error": "Update script not found.",
                "timestamp": int(time.time()),
            }

        try:
            os.chmod(script_path, 0o755)
            print(f"✅ 스크립트 권한 설정 완료: {script_path}")
        except Exception as exc:
            print(f"스크립트 권한 설정 실패: {exc}")

        matterhub_id = settings.MATTERHUB_ID
        force_flag = "true" if force_update else "false"
        skip_flag = " --skip-restart" if skip_restart else ""
        log_file = f"/tmp/update_{update_id}.log"
        cmd = (
            f"nohup bash {script_path} {branch} {force_flag} {update_id} {matterhub_id}"
            f"{skip_flag} > {log_file} 2>&1 & echo $!"
        )
        print(
            f"🚀 외부 업데이트 스크립트 실행: {script_path} "
            f"(branch={branch}, force_update={force_update}, "
            f"update_id={update_id}, hub_id={matterhub_id}, skip_restart={skip_restart})"
        )

        result = subprocess.run(cmd, shell=True, capture_output=True, text=True)

        if result.returncode != 0:
            print(f"❌ 스크립트 실행 실패: {result.stderr}")
            return {
                "success": False,
                "error": f"Script execution failed: {result.stderr}",
                "timestamp": int(time.time()),
            }

        try:
            pid = int(result.stdout.strip())
        except ValueError:
            print("⚠️ PID 추출 실패 - stdout:", result.stdout.strip())
            return {
                "success": True,
                "message": "Update script started but PID extraction failed",
                "script_path": script_path,
                "branch": branch,
                "force_update": force_update,
                "update_id": update_id,
                "hub_id": matterhub_id,
                "timestamp": int(time.time()),
            }

        print(f"✅ 업데이트 스크립트 시작됨 (PID: {pid})")
        time.sleep(2)
        if os.path.exists(log_file):
            try:
                with open(log_file, "r", encoding="utf-8") as log_f:
                    log_content = log_f.read()
                    print(f"스크립트 로그: {log_content}")
            except Exception as exc:
                print(f"로그 파일 읽기 실패: {exc}")

        return {
            "success": True,
            "message": "Update script started successfully",
            "script_path": script_path,
            "branch": branch,
            "force_update": force_update,
            "update_id": update_id,
            "hub_id": matterhub_id,
            "pid": pid,
            "timestamp": int(time.time()),
        }

    except Exception as exc:
        print(f"❌ 업데이트 스크립트 실행 중 예외 발생: {exc}")
        return {
            "success": False,
            "error": str(exc),
            "timestamp": int(time.time()),
        }


def _launch_restart(update_id: str) -> None:
    """서비스 재시작을 별도 프로세스로 실행 (자기 자신도 재시작됨)

    PM2 cgroup에서 탈출하기 위해 systemd-run --scope 사용.
    PM2 서비스가 stop되면 cgroup 내 모든 프로세스가 kill되므로,
    restart 스크립트는 반드시 별도 scope에서 실행해야 함.
    """
    script_path = _find_update_script()
    if not script_path:
        print("❌ 재시작 스크립트를 찾을 수 없습니다")
        return

    log_file = f"/tmp/restart_{update_id}.log"

    # systemd-run으로 PM2 cgroup에서 독립 실행 (sudo NOPASSWD 필요)
    cmd = (
        f"sudo systemd-run --scope --unit=matterhub-update-restart "
        f"bash {script_path} --restart-only > {log_file} 2>&1 &"
    )
    result = subprocess.run(cmd, shell=True, capture_output=True, text=True)

    if result.returncode != 0:
        # systemd-run 실패 시 (sudo 불가 등) nohup fallback
        print(f"⚠️ systemd-run 실패 ({result.stderr.strip()}), nohup fallback")
        cmd = f"nohup bash {script_path} --restart-only > {log_file} 2>&1 &"
        subprocess.run(cmd, shell=True)

    print(f"🔄 서비스 재시작 프로세스 시작됨: {log_file}")


def execute_update_async(message: Dict[str, Any]) -> None:
    """2단계 업데이트 실행: git pull → 응답 전송 → 서비스 재시작"""
    try:
        branch = message.get("branch", "master")
        force_update = bool(message.get("force_update", False))
        update_id = message.get("update_id", "unknown")

        print(
            f"백그라운드 업데이트 시작: {update_id} "
            f"(branch={branch}, force={force_update}, hub_id={settings.MATTERHUB_ID})"
        )

        # Phase A: git pull only (--skip-restart)
        result = execute_external_update_script(
            branch, force_update, update_id, skip_restart=True
        )
        print(f"스크립트 실행 결과: {result}")

        # Phase B: PID 모니터링 + 상태 파일 읽기
        if result.get("success") and result.get("pid"):
            _wait_for_pid(result["pid"], timeout=300)
            status_file = f"/tmp/update_{update_id}.status"
            status = _read_status_file(status_file)
            if status:
                result.update(status)
                # exit_code가 0이 아니면 실패로 처리
                if status.get("exit_code", 0) != 0:
                    result["success"] = False
                    print(f"❌ 스크립트 실패 (exit_code={status.get('exit_code')})")
                else:
                    print(f"상태 파일 읽기 완료: {status}")
            else:
                result["success"] = False
                print("❌ 상태 파일을 읽을 수 없음 — 스크립트 실패로 처리")

        # Phase C: 최종 응답 전송 (QoS 1, PUBACK 확인)
        send_final_response(message, result)

        # Phase D: 서비스 재시작 (이 프로세스도 재시작됨)
        if result.get("success"):
            _launch_restart(update_id)

    except Exception as exc:
        print(f"❌ 비동기 업데이트 실행 실패: {exc}")
        send_error_response(message, str(exc))


def process_update_queue() -> None:
    global is_processing_update
    while True:
        try:
            message = update_queue.get()
            with update_queue_lock:
                is_processing_update = True

            print(f"업데이트 큐 처리: {message.get('update_id')}")
            execute_update_async(message)

            with update_queue_lock:
                is_processing_update = False

            update_queue.task_done()
            print(f"✅ 큐 업데이트 완료: {message.get('update_id')}")

        except Exception as exc:
            print(f"❌ 큐 처리 중 오류: {exc}")
            with update_queue_lock:
                is_processing_update = False
            update_queue.task_done()


def handle_update_command(message: Dict[str, Any]) -> None:
    try:
        command = message.get("command")
        update_id = message.get("update_id", "unknown")
        print(f"📥 업데이트 명령 수신: command={command}, update_id={update_id}")

        send_immediate_response(message, status="processing")

        update_queue.put(message)
        print(f"📋 업데이트 큐에 추가됨: {update_id}")

    except Exception as exc:
        print(f"❌ Git 업데이트 실패: {exc}")
        send_error_response(message, str(exc))


def start_queue_worker() -> threading.Thread:
    worker = threading.Thread(target=process_update_queue, name="update-queue-worker")
    worker.daemon = True
    worker.start()
    print("✅ 업데이트 큐 처리 스레드 시작됨")
    return worker
