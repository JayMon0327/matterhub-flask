from __future__ import annotations

import importlib
import json
import os
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock, patch


# ---------------------------------------------------------------------------
# Helpers — mirror the mock-injection pattern from test_runtime.py
# ---------------------------------------------------------------------------

def _load_runtime():
    """Import mqtt_pkg.runtime with awscrt/awsiot stubbed out."""
    project_root = str(Path(__file__).resolve().parent.parent.parent)
    if project_root not in sys.path:
        sys.path.insert(0, project_root)

    awscrt_module = types.ModuleType("awscrt")
    awscrt_module.io = types.SimpleNamespace(
        EventLoopGroup=MagicMock(return_value=MagicMock()),
        DefaultHostResolver=MagicMock(return_value=MagicMock()),
        ClientBootstrap=MagicMock(return_value=MagicMock()),
    )
    # mqtt.Will must be a real class so the code can instantiate it
    class _Will:
        def __init__(self, *, topic, qos, payload, retain):
            self.topic = topic
            self.qos = qos
            self.payload = payload
            self.retain = retain

    awscrt_module.mqtt = types.SimpleNamespace(
        QoS=types.SimpleNamespace(AT_LEAST_ONCE=1),
        Connection=object,
        Will=_Will,
    )

    awsiot_module = types.ModuleType("awsiot")
    awsiot_module.mqtt_connection_builder = MagicMock()

    dotenv_module = types.ModuleType("dotenv")
    dotenv_module.load_dotenv = lambda *args, **kwargs: None

    with patch.dict(
        sys.modules,
        {
            "awscrt": awscrt_module,
            "awsiot": awsiot_module,
            "dotenv": dotenv_module,
        },
    ):
        for mod_name in list(sys.modules):
            if mod_name == "mqtt_pkg" or mod_name.startswith("mqtt_pkg."):
                del sys.modules[mod_name]
        for mod_name in list(sys.modules):
            if mod_name.startswith("providers"):
                del sys.modules[mod_name]
        return importlib.import_module("mqtt_pkg.runtime"), awscrt_module, awsiot_module


def _fake_mqtt_conn():
    conn = MagicMock()
    future = MagicMock()
    future.result.return_value = None
    conn.connect.return_value = future
    return conn


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_lwt_will_attached_with_hub_offline_payload(monkeypatch):
    runtime, awscrt_mod, awsiot_mod = _load_runtime()

    monkeypatch.setattr(runtime.settings, "MATTERHUB_ID", "hub_test_lwt", raising=False)

    fake_conn = _fake_mqtt_conn()
    awsiot_mod.mqtt_connection_builder.mtls_from_path.return_value = fake_conn

    with patch.object(runtime, "mqtt_connection_builder", awsiot_mod.mqtt_connection_builder), \
         patch.object(runtime, "threading") as mock_threading, \
         patch.object(runtime, "_certificate_paths", return_value=("/tmp/c.pem", "/tmp/k.pem", "/tmp/ca.pem")), \
         patch.object(runtime.os.path, "exists", return_value=False):

        mock_threading.Timer.return_value = MagicMock()

        client = runtime.AWSIoTClient.__new__(runtime.AWSIoTClient)
        client.cert_path = "/tmp/nope"
        client.endpoint = "fake.endpoint"
        client.client_id = "hub_test_lwt"

        # _check_certificate is called first; patch it to return True + paths
        with patch.object(client, "_check_certificate", return_value=(True, "/tmp/c.pem", "/tmp/k.pem")):
            client.connect_mqtt()

    kwargs = awsiot_mod.mqtt_connection_builder.mtls_from_path.call_args.kwargs
    assert "will" in kwargs, "will kwarg must be passed to mtls_from_path"
    will = kwargs["will"]
    assert will.topic == "matterhub/hub_test_lwt/event/hub_status"
    assert will.retain is False
    body = json.loads(will.payload.decode("utf-8"))
    assert body["alert_type"] == "HUB_OFFLINE"
    assert body["source"] == "lwt"


def test_hub_online_scheduled_after_grace(monkeypatch):
    runtime, awscrt_mod, awsiot_mod = _load_runtime()

    monkeypatch.setattr(runtime.settings, "MATTERHUB_ID", "hub_test_online", raising=False)

    fake_conn = _fake_mqtt_conn()
    awsiot_mod.mqtt_connection_builder.mtls_from_path.return_value = fake_conn

    with patch.object(runtime, "mqtt_connection_builder", awsiot_mod.mqtt_connection_builder), \
         patch.object(runtime, "threading") as mock_threading, \
         patch.object(runtime, "_certificate_paths", return_value=("/tmp/c.pem", "/tmp/k.pem", "/tmp/ca.pem")), \
         patch.object(runtime.os.path, "exists", return_value=False):

        timer_instance = MagicMock()
        mock_threading.Timer.return_value = timer_instance

        client = runtime.AWSIoTClient.__new__(runtime.AWSIoTClient)
        client.cert_path = "/tmp/nope"
        client.endpoint = "fake.endpoint"
        client.client_id = "hub_test_online"

        with patch.object(client, "_check_certificate", return_value=(True, "/tmp/c.pem", "/tmp/k.pem")):
            client.connect_mqtt()

    mock_threading.Timer.assert_called_once()
    delay = mock_threading.Timer.call_args.args[0]
    assert delay == runtime.HUB_STATUS_GRACE_SECONDS
    timer_instance.start.assert_called_once()
