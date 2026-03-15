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
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.dispatcher import async_dispatcher_send

from .api import AnycubicApi, AnycubicApiError, PrinterInfo
from .const import (
    DOMAIN,
    MQTT_PORT,
    MQTT_TOPIC_PUBLISH,
    MQTT_TOPIC_SUBSCRIBE,
    SIGNAL_UPDATE,
    STARTUP_QUERIES,
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
        self._model_id: str = ""
        self._device_id: str = ""

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
            client.on_subscribe = self._on_subscribe
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

    def _build_command(self, msg_type: str, action: str, data: Any = None) -> str:
        """Build a JSON command message.

        The app always includes "data" (null when no payload is needed).
        ``msg_type`` is the JSON "type" field (may differ from topic suffix).
        """
        return json.dumps(
            {
                "type": msg_type,
                "action": action,
                "timestamp": int(time.time() * 1000),
                "msgid": str(uuid.uuid4()),
                "data": data,
            }
        )

    def publish_command(
        self,
        subtopic: str,
        action: str,
        data: Any = None,
        *,
        msg_type: str | None = None,
    ) -> None:
        """Publish a command to the printer.

        ``subtopic`` determines the MQTT topic suffix.
        ``msg_type`` is the JSON "type" field; defaults to ``subtopic``.
        Safe to call from any thread (paho publish is thread-safe).
        """
        if self._client is None or not self._model_id:
            _LOGGER.warning("Cannot publish: MQTT not connected")
            return
        topic = MQTT_TOPIC_PUBLISH.format(
            model_id=self._model_id,
            device_id=self._device_id,
            subtopic=subtopic,
        )
        msg = self._build_command(msg_type or subtopic, action, data)
        self._client.publish(topic, msg)
        _LOGGER.debug("Published %s/%s to %s", msg_type or subtopic, action, topic)

    def request_refresh(self) -> None:
        """Request a full state refresh from the printer."""
        if self._client is None or not self._model_id:
            _LOGGER.warning("Cannot refresh: MQTT not connected")
            return

        for topic_suffix, msg_type, action, data in STARTUP_QUERIES:
            self.publish_command(
                topic_suffix,
                action,
                data,
                msg_type=msg_type,
            )
        _LOGGER.debug("Published %d refresh queries", len(STARTUP_QUERIES))

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
            self._model_id = userdata["model_id"]
            self._device_id = userdata["device_id"]
            # Subscribe and wait for SUBACK before publishing queries.
            # The on_subscribe callback will fire the startup queries.
            client.subscribe(userdata["topic"])

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

    def _on_subscribe(
        self,
        client: mqtt.Client,
        userdata: dict,
        mid: int,
        reason_code_list: list[mqtt.ReasonCode],
        properties: mqtt.Properties | None = None,
    ) -> None:
        """Handle MQTT subscription confirmed — now safe to query state."""
        for rc in reason_code_list:
            if rc.is_failure:
                _LOGGER.error("MQTT subscription rejected: %s", rc)
                return
        _LOGGER.debug(
            "MQTT subscription confirmed (rc=%s), querying printer state",
            reason_code_list,
        )

        # Request current state using the same query burst as the app's refresh path.
        self.request_refresh()

        # Start camera stream
        video_topic = MQTT_TOPIC_PUBLISH.format(
            model_id=self._model_id,
            device_id=self._device_id,
            subtopic="video",
        )
        client.publish(
            video_topic,
            self._build_command("video", "startCapture"),
        )
        _LOGGER.debug("Published startCapture")

    def _on_message(
        self,
        client: mqtt.Client,
        userdata: dict,
        msg: mqtt.MQTTMessage,
    ) -> None:
        """Handle incoming MQTT message."""
        try:
            payload = json.loads(msg.payload)
            # Prefer the "type" field in the payload for subtopic name;
            # fall back to extracting from the topic path.
            subtopic = payload.get("type")
            if not subtopic:
                parts = msg.topic.split("/")
                subtopic = parts[7] if len(parts) > 7 else "unknown"
            _LOGGER.debug("MQTT %s: %s", subtopic, payload)

            # Merge into existing data for this subtopic so that
            # monitoring messages don't overwrite print progress, etc.
            if subtopic not in self._data:
                self._data[subtopic] = {}
            stored = self._data[subtopic]

            # Capture top-level state (e.g. "busy", "printing")
            if "state" in payload:
                stored["state"] = payload["state"]
                # Map firmware state names to our enum values
                _STATE_MAP = {
                    "free": "idle",
                    "idle": "idle",
                    "busy": "busy",
                    "printing": "printing",
                    "paused": "paused",
                    "stopping": "stopping",
                    "complete": "complete",
                    "monitoring": "monitoring",
                    "error": "error",
                }
                mapped = _STATE_MAP.get(payload["state"])
                if mapped:
                    if "__combined__" not in self._data:
                        self._data["__combined__"] = {}
                    self._data["__combined__"]["state"] = mapped
            if "action" in payload:
                stored["action"] = payload["action"]

            # Merge data dict fields (skip None and list-only payloads
            # like checkStatus from monitoring messages)
            data = payload.get("data")
            if isinstance(data, dict):
                stored.update(data)

                # Update device sw_version from lanInfo response
                if subtopic == "lanInfo" and "version" in data:
                    self.hass.loop.call_soon_threadsafe(
                        self._update_sw_version, data["version"]
                    )
        except (json.JSONDecodeError, IndexError) as err:
            _LOGGER.warning("Failed to parse MQTT message: %s", err)
            return

        self.hass.loop.call_soon_threadsafe(
            async_dispatcher_send,
            self.hass,
            SIGNAL_UPDATE.format(entry_id=self.entry.entry_id),
        )

    def _update_sw_version(self, version: str) -> None:
        """Update device registry with firmware version from lanInfo."""
        registry = dr.async_get(self.hass)
        device = registry.async_get_device(
            identifiers={(DOMAIN, self.entry.entry_id)}
        )
        if device and device.sw_version != version:
            registry.async_update_device(device.id, sw_version=version)

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
