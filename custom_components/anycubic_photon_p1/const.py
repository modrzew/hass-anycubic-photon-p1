"""Constants for the Anycubic Photon P1 integration."""

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

SUBTOPICS = [
    "status",
    "properties",
    "print",
    "light",
    "video",
    "peripherie",
    "releaseFilm",
]

SIGNAL_UPDATE = f"{DOMAIN}_update_{{entry_id}}"
