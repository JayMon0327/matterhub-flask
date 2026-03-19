# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

MatterHub is a Python/Flask IoT gateway for smart home automation. It runs on Raspberry Pi (Ubuntu 24.04) and bridges Home Assistant with AWS IoT Core via MQTT. The system uses a multi-process architecture with 6 systemd services.

## Commands

### Run tests
```bash
python -m unittest discover -v -s tests -t .   # all tests (project root as top-level)
python -m unittest tests.test_mqtt             # single module
python -m unittest tests.libs.test_edit        # nested module
```

Tests use `unittest` (not pytest). Mocking via `unittest.mock`.

### Run services locally
```bash
python app.py              # Flask API on :8100
python mqtt.py             # MQTT worker
python support_tunnel.py   # SSH reverse tunnel
python update_agent.py     # Update agent
```

Requires `.env` file with HA host, tokens, MQTT topics, and certificate paths.

### Build .deb package
```bash
bash device_config/build_matterhub_deb.sh --version <ver> --mode source
```

### Deploy to device
```bash
bash device_config/deploy_matterhub_deb.sh   # or manual scp + ssh
bash device_config/provision_device_full.sh   # full provisioning
```

## Architecture

### Multi-Process Services

| Service | Entry Point | Role |
|---------|-------------|------|
| matterhub-api | `app.py` | Flask REST API (port 8100), proxies Home Assistant |
| matterhub-mqtt | `mqtt.py` | AWS IoT Core MQTT client (Konai protocol) |
| matterhub-rule-engine | `sub/ruleEngine.py` | State-change triggered automation |
| matterhub-notifier | `sub/notifier.py` | WebSocket event → webhook notifications |
| matterhub-support-tunnel | `support_tunnel.py` | Reverse SSH tunnel for remote maintenance |
| matterhub-update-agent | `update_agent.py` | Device update coordinator (runs as root) |

Service definitions: `device_config/service_definitions.py`
systemd unit templates: `device_config/systemd/`

### Key Packages

- **`mqtt_pkg/`** — Refactored MQTT layer: `runtime.py` (AWSIoTClient connection), `callbacks.py` (message handlers), `publisher.py` (state publishing), `settings.py` (topic config from .env), `provisioning.py` (claim-based Thing registration)
- **`sub/`** — Older subsystem modules: scheduler, notifier, rule engine, collector
- **`wifi_config/`** — Wi-Fi provisioning via nmcli: Flask blueprint at `/local/admin/network/*`, AP bootstrap, provision state tracking
- **`libs/`** — Utilities: `device_binding.py` (MAC binding enforcement), `edit.py` (JSON CRUD, .env editing)
- **`device_config/`** — Raspberry Pi provisioning, .deb packaging, systemd unit rendering, deployment scripts

### Data Flow

1. Flask API ↔ Home Assistant (HTTP proxy on :8100)
2. MQTT worker ↔ AWS IoT Core (awscrt/awsiot SDK, Konai protocol topics)
3. Notifier → WebSocket listener → webhook calls on state change
4. Scheduler reads `resources/schedule.json`, calls HA services on schedule

### Configuration

- `.env` — All runtime config (HA host/token, MQTT topics, matterhub_id, cert paths)
- `resources/*.json` — Runtime state files (devices, schedules, rules, rooms, notifications)
- `python-dotenv` loads `.env`; use `dotenv_path='.env'` explicitly (required for .pyc compatibility)

## Conventions

- Documentation and commit messages are in Korean
- Target platform: Raspberry Pi 4+ / Ubuntu 24.04 LTS
- Python 3.9+ (deployed via .deb with .pyc compilation; .py files deleted on device)
- No linter/formatter configured
- No CI/CD pipeline; deployment is manual via shell scripts
