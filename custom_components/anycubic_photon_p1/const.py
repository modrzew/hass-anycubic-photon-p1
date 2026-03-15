"""Constants for the Anycubic Photon P1 integration."""

from __future__ import annotations

from typing import Any

DOMAIN = "anycubic_photon_p1"

HTTP_PORT = 18910
MQTT_PORT = 8883
VIDEO_PORT = 18088

MQTT_TOPIC_SUBSCRIBE = (
    "anycubic/anycubicCloud/v1/printer/+/{model_id}/{device_id}/+/report"
)
MQTT_TOPIC_PUBLISH = (
    "anycubic/anycubicCloud/v1/pc/printer/{model_id}/{device_id}/{subtopic}"
)

# Startup queries sent after MQTT subscription is confirmed.
# Each tuple: (topic_suffix, json_type, action, data)
# topic_suffix is the MQTT topic path segment.
# json_type is the "type" field in the JSON (may differ from topic).
STARTUP_QUERIES: list[tuple[str, str, str, Any]] = [
    ("status", "status", "query", None),
    ("status", "lanInfo", "query", None),
    ("print", "print", "query", None),
    ("light", "light", "query", None),
    ("peripherie", "peripherie", "query", None),
    ("releaseFilm", "releaseFilm", "get", None),
    ("properties", "properties", "read", ["connect"]),
    ("properties", "properties", "read", ["signal_strength"]),
    ("status", "autoOperation", "getStatus", None),
]

SIGNAL_UPDATE = f"{DOMAIN}_update_{{entry_id}}"
