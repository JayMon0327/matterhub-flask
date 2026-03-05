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
        connection.publish(
            topic=response_topic,
            payload=json.dumps(payload),
            qos=mqtt.QoS.AT_MOST_ONCE,
        )
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


def execute_external_update_script(
    branch: str = "master",
    force_update: bool = False,
    update_id: str = "unknown",
) -> Dict[str, Any]:
    try:
        possible_paths = [
            "/home/hyodol/whatsmatter-hub-flask-server/update_server.sh",
            "./update_server.sh",
            "../update_server.sh",
            os.path.join(os.path.dirname(__file__), "../update_server.sh"),
        ]

        script_path = None
        for path in possible_paths:
            resolved = os.path.abspath(path)
            if os.path.exists(resolved):
                script_path = resolved
                break

        if not script_path:
            return {
                "success": False,
                "error": f"Update script not found. Checked paths: {possible_paths}",
                "timestamp": int(time.time()),
            }

        try:
            os.chmod(script_path, 0o755)
            print(f"✅ 스크립트 권한 설정 완료: {script_path}")
        except Exception as exc:
            print(f"스크립트 권한 설정 실패: {exc}")

        matterhub_id = settings.MATTERHUB_ID
        print(
            f"🚀 외부 업데이트 스크립트 실행: {script_path} "
            f"(branch={branch}, force_update={force_update}, update_id={update_id}, hub_id={matterhub_id})"
        )

        force_flag = "true" if force_update else "false"
        log_file = f"/tmp/update_{update_id}.log"
        cmd = (
            f"nohup bash {script_path} {branch} {force_flag} {update_id} {matterhub_id} "
            f"> {log_file} 2>&1 & echo $!"
        )
        print(f"실행 명령어: {cmd}")

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


def execute_update_async(message: Dict[str, Any]) -> None:
    try:
        branch = message.get("branch", "master")
        force_update = bool(message.get("force_update", False))
        update_id = message.get("update_id", "unknown")

        print(
            f"백그라운드 업데이트 시작: {update_id} "
            f"(branch={branch}, force={force_update}, hub_id={settings.MATTERHUB_ID})"
        )

        result = execute_external_update_script(branch, force_update, update_id)
        print(f"스크립트 실행 결과: {result}")

        if result.get("success") and result.get("pid"):
            pid = result["pid"]
            max_wait_time = 300
            wait_interval = 10
            waited_time = 0

            while waited_time < max_wait_time:
                try:
                    check_result = subprocess.run(
                        ["ps", "-p", str(pid)],
                        capture_output=True,
                        text=True,
                    )
                    if check_result.returncode != 0:
                        print(f"✅ 업데이트 스크립트 완료 감지 (PID: {pid})")
                        break
                except Exception as exc:
                    print(f"프로세스 체크 실패: {exc}")

                time.sleep(wait_interval)
                waited_time += wait_interval
                print(f"업데이트 대기 ({waited_time}/{max_wait_time}초)")

            if waited_time >= max_wait_time:
                print(f"업데이트 타임아웃 ({max_wait_time}초)")
                result["timeout"] = True

        send_final_response(message, result)

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
        update_id = message.get("update_id")
        print(
            "[UPDATE][DISABLED] command ignored "
            f"(command={command}, update_id={update_id}, reason=reverse_tunnel_only)"
        )
        send_error_response(
            message,
            "REMOTE_UPDATE_DISABLED: use reverse tunnel maintenance workflow",
        )
    except Exception as exc:
        print(f"❌ Git 업데이트 실패: {exc}")
        send_error_response(message, str(exc))


def start_queue_worker() -> threading.Thread:
    worker = threading.Thread(target=process_update_queue, name="update-queue-worker")
    worker.daemon = True
    worker.start()
    print("✅ 업데이트 큐 처리 스레드 시작됨")
    return worker
