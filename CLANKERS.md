# CLANKERS.md

This file provides guidance to AI agents when working with code in this repository.

## What this is

A Home Assistant custom integration for the Anycubic Photon P1 resin printer. It communicates over the local network using the printer's HTTP API (port 18910) and MQTT (port 8883, TLS) — no cloud account needed. The printer only allows one MQTT client at a time.

## Development commands

```bash
# Lint and format (pre-commit runs both automatically)
ruff check --fix .
ruff format .

# Run pre-commit hooks manually
pre-commit run --all-files
```

Uses `uv` for dependency management. Dev dependencies: `ruff`, `pre-commit`.

No test suite exists currently.

## Architecture

This repo is a flat HA custom component (no `custom_components/` nesting — files are deployed directly into `custom_components/anycubic_photon_p1/`).

### Connection flow

1. **`config_flow.py`** — User provides printer IP. Validates by calling HTTP API and getting MQTT credentials.
2. **`__init__.py`** — On setup, fetches `PrinterInfo` via HTTP, creates `AnycubicMqttCoordinator`, forwards to sensor platform.
3. **`api.py`** — HTTP client. `GET /info` returns printer metadata + token. `POST /ctrl` with signed request returns AES-128-CBC encrypted MQTT credentials (username, password, client_id). The signing and decryption use MD5 hashing and the token from `/info`.
4. **`coordinator.py`** — Manages the paho-mqtt client. Connects with TLS (no cert verification), subscribes to `anycubic/anycubicCloud/v1/printer/+/{modelId}/{deviceId}/+/report`, publishes `{}` to each subtopic to request current state. Handles reconnection with exponential backoff (30s–300s). Dispatches updates via HA's dispatcher system (`SIGNAL_UPDATE`).
5. **`entity.py`** — Base entity class. Sets `should_poll = False`, listens for dispatcher signals to trigger state writes.
6. **`sensor.py`** — Defines sensor entities via `AnycubicSensorEntityDescription` dataclass with `subtopic` and `value_fn` fields. Sensors read from coordinator's merged data dict keyed by MQTT subtopic.

### MQTT data model

The coordinator stores data as `dict[subtopic, dict[str, Any]]`. Incoming messages are merged (not replaced) per subtopic. A special `__combined__` key tracks the overall printer state (online/offline/idle/printing/etc.). The subtopics are: `status`, `properties`, `print`, `light`, `video`, `peripherie`, `releaseFilm`.

### Key constants (`const.py`)

- `DOMAIN = "anycubic_photon_p1"`
- `HTTP_PORT = 18910`, `MQTT_PORT = 8883`
- MQTT topic patterns use `{model_id}`, `{device_id}`, `{subtopic}` placeholders

### Protocol documentation

`docs/protocol.md` contains reverse-engineered details of the LAN protocol.

## Ruff configuration

Target: Python 3.12, line length 88. Enabled rule sets: E, W, F, I, UP, B, SIM, RUF. The `old-stuff/` directory is excluded.
