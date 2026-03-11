"""MQTT coordinator for Anycubic Photon P1."""

from __future__ import annotations

import asyncio
import json
import logging
import ssl
import time
import uuid
from typing import Any

import paho.mqtt.client as mqtt
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.dispatcher import async_dispatcher_send

from .api import AnycubicApi, AnycubicApiError, PrinterInfo
from .const import (
    MQTT_PORT,
    MQTT_TOPIC_PUBLISH,
    MQTT_TOPIC_SUBSCRIBE,
    SIGNAL_UPDATE,
    SUBTOPICS,
    VIDEO_PORT,
)

_LOGGER = logging.getLogger(__name__)

MIN_RECONNECT_DELAY = 30
MAX_RECONNECT_DELAY = 300


class AnycubicMqttCoordinator:
    """Manages MQTT connection to the Anycubic Photon P1 printer."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        api: AnycubicApi,
        printer_info: PrinterInfo,
    ) -> None:
        """Initialize the coordinator."""
        self.hass = hass
        self.entry = entry
        self.api = api
        self.printer_info = printer_info
        self.available = False
        self._data: dict[str, dict[str, Any]] = {}
        self._client: mqtt.Client | None = None
        self._reconnect_delay = MIN_RECONNECT_DELAY
        self._reconnect_task: asyncio.Task | None = None
        self._stopping = False

    @property
    def stream_url(self) -> str:
        """Return the HTTP-FLV stream URL."""
        return f"http://{self.printer_info.ip}:{VIDEO_PORT}/flv"

    def get_data(self, subtopic: str) -> dict[str, Any] | None:
        """Get the latest data for a subtopic."""
        return self._data.get(subtopic)

    async def async_start(self) -> None:
        """Start the MQTT connection."""
        self._stopping = False
        await self._async_connect()

    async def _async_connect(self) -> None:
        """Perform full handshake and connect to MQTT."""
        try:
            info = await self.api.get_info()
            self.printer_info = info
            creds = await self.api.get_mqtt_credentials(info)
        except (AnycubicApiError, Exception) as err:
            _LOGGER.error("Failed to get MQTT credentials: %s", err)
            self._schedule_reconnect()
            return

        topic = MQTT_TOPIC_SUBSCRIBE.format(
            model_id=info.model_id,
            device_id=creds.device_id,
        )
        _LOGGER.debug(
            "Connecting MQTT to %s:%s as %s, subscribing to %s",
            self.printer_info.ip,
            MQTT_PORT,
            creds.client_id,
            topic,
        )

        def _create_and_connect() -> mqtt.Client:
            client = mqtt.Client(
                callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
                client_id=creds.client_id,
            )
            client.username_pw_set(creds.username, creds.password)

            ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            ctx.set_ciphers("DEFAULT:@SECLEVEL=0")
            client.tls_set_context(ctx)

            client.on_connect = self._on_connect
            client.on_message = self._on_message
            client.on_disconnect = self._on_disconnect

            client.user_data_set(
                {
                    "topic": topic,
                    "model_id": info.model_id,
                    "device_id": creds.device_id,
                }
            )
            client.connect(self.printer_info.ip, MQTT_PORT)
            client.loop_start()
            return client

        try:
            self._client = await self.hass.async_add_executor_job(_create_and_connect)
        except Exception as err:
            _LOGGER.error("Failed to connect MQTT: %s", err)
            self._schedule_reconnect()

    def _on_connect(
        self,
        client: mqtt.Client,
        userdata: dict,
        flags: mqtt.ConnectFlags,
        rc: mqtt.ReasonCode,
        properties: mqtt.Properties | None = None,
    ) -> None:
        """Handle MQTT connection."""
        if rc.is_failure is False:
            _LOGGER.debug("MQTT connected to %s", self.printer_info.ip)
            client.subscribe(userdata["topic"])
            # Request current state from all subtopics
            for subtopic in SUBTOPICS:
                pub_topic = MQTT_TOPIC_PUBLISH.format(
                    model_id=userdata["model_id"],
                    device_id=userdata["device_id"],
                    subtopic=subtopic,
                )
                client.publish(pub_topic, "{}")
                _LOGGER.debug("Published status request to %s", pub_topic)

            # Start camera stream
            video_topic = MQTT_TOPIC_PUBLISH.format(
                model_id=userdata["model_id"],
                device_id=userdata["device_id"],
                subtopic="video",
            )
            start_capture_msg = json.dumps(
                {
                    "type": "video",
                    "action": "startCapture",
                    "timestamp": int(time.time() * 1000),
                    "msgid": str(uuid.uuid4()),
                    "data": None,
                }
            )
            client.publish(video_topic, start_capture_msg)
            _LOGGER.debug("Published startCapture to %s", video_topic)
            self._data["__combined__"] = {"state": "online"}
            self.available = True
            self._reconnect_delay = MIN_RECONNECT_DELAY
            self.hass.loop.call_soon_threadsafe(
                async_dispatcher_send,
                self.hass,
                SIGNAL_UPDATE.format(entry_id=self.entry.entry_id),
            )
        else:
            _LOGGER.error("MQTT connect failed: %s", rc)

    def _on_message(
        self,
        client: mqtt.Client,
        userdata: dict,
        msg: mqtt.MQTTMessage,
    ) -> None:
        """Handle incoming MQTT message."""
        try:
            parts = msg.topic.split("/")
            # .../printer/public/{modelId}/{deviceId}/{subtopic}/report
            subtopic = parts[7] if len(parts) > 7 else "unknown"
            payload = json.loads(msg.payload)
            _LOGGER.debug("MQTT %s: %s", subtopic, payload)

            # Merge into existing data for this subtopic so that
            # monitoring messages don't overwrite print progress, etc.
            if subtopic not in self._data:
                self._data[subtopic] = {}
            stored = self._data[subtopic]

            # Capture top-level state (e.g. "busy", "printing")
            if "state" in payload:
                stored["state"] = payload["state"]
                # Only update combined state with known printer states
                _PRINTER_STATES = {
                    "idle",
                    "busy",
                    "printing",
                    "paused",
                    "stopping",
                    "complete",
                    "monitoring",
                    "error",
                }
                if payload["state"] in _PRINTER_STATES:
                    if "__combined__" not in self._data:
                        self._data["__combined__"] = {}
                    self._data["__combined__"]["state"] = payload["state"]
            if "action" in payload:
                stored["action"] = payload["action"]

            # Merge data dict fields (skip None and list-only payloads
            # like checkStatus from monitoring messages)
            data = payload.get("data")
            if isinstance(data, dict):
                stored.update(data)
        except (json.JSONDecodeError, IndexError) as err:
            _LOGGER.warning("Failed to parse MQTT message: %s", err)
            return

        self.hass.loop.call_soon_threadsafe(
            async_dispatcher_send,
            self.hass,
            SIGNAL_UPDATE.format(entry_id=self.entry.entry_id),
        )

    def _on_disconnect(
        self,
        client: mqtt.Client,
        userdata: dict,
        flags: mqtt.DisconnectFlags,
        rc: mqtt.ReasonCode,
        properties: mqtt.Properties | None = None,
    ) -> None:
        """Handle MQTT disconnection."""
        if self._stopping:
            return
        _LOGGER.warning("MQTT disconnected (rc=%s), will reconnect", rc)
        self._data["__combined__"] = {"state": "offline"}
        self.available = False
        self.hass.loop.call_soon_threadsafe(
            async_dispatcher_send,
            self.hass,
            SIGNAL_UPDATE.format(entry_id=self.entry.entry_id),
        )
        self.hass.loop.call_soon_threadsafe(self._schedule_reconnect)

    def _schedule_reconnect(self) -> None:
        """Schedule a reconnection attempt."""
        if self._stopping:
            return
        if self._reconnect_task and not self._reconnect_task.done():
            return

        async def _reconnect() -> None:
            _LOGGER.debug("Reconnecting in %s seconds", self._reconnect_delay)
            await asyncio.sleep(self._reconnect_delay)
            self._reconnect_delay = min(self._reconnect_delay * 2, MAX_RECONNECT_DELAY)
            await self._async_stop_client()
            await self._async_connect()

        self._reconnect_task = self.hass.async_create_task(_reconnect())

    async def _async_stop_client(self) -> None:
        """Stop the current MQTT client."""
        if self._client is not None:
            client = self._client
            self._client = None
            await self.hass.async_add_executor_job(client.loop_stop)
            await self.hass.async_add_executor_job(client.disconnect)

    async def async_stop(self) -> None:
        """Stop the coordinator and disconnect MQTT."""
        self._stopping = True
        if self._reconnect_task and not self._reconnect_task.done():
            self._reconnect_task.cancel()
        self.available = False
        await self._async_stop_client()
