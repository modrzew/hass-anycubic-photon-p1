"""HTTP API client for Anycubic Photon P1 printer."""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import time
import uuid
from dataclasses import dataclass

import aiohttp
from Crypto.Cipher import AES

from .const import HTTP_PORT

_LOGGER = logging.getLogger(__name__)


@dataclass
class PrinterInfo:
    """Printer info from GET /info."""

    name: str
    model: str
    model_id: str
    device_id: str
    mac: str
    token: str
    firmware: str
    ip: str


@dataclass
class MqttCredentials:
    """MQTT credentials from POST /ctrl + AES decryption."""

    username: str
    password: str
    client_id: str
    broker: str
    device_id: str
    mac: str


class AnycubicApiError(Exception):
    """Base exception for API errors."""


class AnycubicApi:
    """HTTP client for the Anycubic Photon P1 printer."""

    def __init__(self, host: str, session: aiohttp.ClientSession) -> None:
        """Initialize the API client."""
        self.host = host
        self._session = session
        self._base_url = f"http://{host}:{HTTP_PORT}"

    async def get_info(self) -> PrinterInfo:
        """Fetch printer info from GET /info."""
        _LOGGER.debug("GET %s/info", self._base_url)
        async with self._session.get(f"{self._base_url}/info") as resp:
            if resp.status != 200:
                raise AnycubicApiError(f"GET /info returned {resp.status}")
            data = await resp.json()

        _LOGGER.debug("GET /info response: %s", data)

        if data.get("code") != 0:
            raise AnycubicApiError(f"GET /info error: {data.get('message')}")

        return PrinterInfo(
            name=data.get("deviceName", "Anycubic Photon P1"),
            model=data.get("modelName", "Anycubic Photon P1"),
            model_id=data["modelId"],
            device_id=data.get("cn", ""),
            mac=data.get("mac", ""),
            token=data["token"],
            firmware=data.get("firmwareVersion", ""),
            ip=data.get("ip", self.host),
        )

    async def get_mqtt_credentials(self, info: PrinterInfo) -> MqttCredentials:
        """Get MQTT credentials via POST /ctrl + AES decryption."""
        ts = str(int(time.time() * 1000))
        nonce = uuid.uuid4().hex[:15]
        sign = _compute_sign(info.token, ts, nonce)
        did = "homeassistant"

        url = f"{self._base_url}/ctrl?ts={ts}&nonce={nonce}&did={did}&sign={sign}"
        _LOGGER.debug("POST /ctrl ts=%s nonce=%s sign=%s", ts, nonce, sign)
        async with self._session.post(url) as resp:
            if resp.status != 200:
                raise AnycubicApiError(f"POST /ctrl returned {resp.status}")
            data = await resp.json()

        _LOGGER.debug("POST /ctrl response code=%s", data.get("code"))

        if data.get("code") != 200:
            raise AnycubicApiError(f"POST /ctrl error: {data.get('message')}")

        encoded_info = data["data"]["info"]
        ctrl_token = data["data"]["token"]

        return _decrypt_mqtt_info(encoded_info, info.token, ctrl_token)


def _compute_sign(token: str, ts: str, nonce: str) -> str:
    """Compute the sign parameter for /ctrl request.

    sign = md5(md5(token[:16]).hex() + ts + nonce).hex()
    """
    hash1 = hashlib.md5(token[:16].encode()).hexdigest()
    return hashlib.md5((hash1 + ts + nonce).encode()).hexdigest()


def _decrypt_mqtt_info(
    encoded_info: str, info_token: str, ctrl_token: str
) -> MqttCredentials:
    """Decrypt the AES-128-CBC encrypted MQTT credentials."""
    key = info_token[16:32].encode()
    iv = ctrl_token.encode()

    padding = (4 - len(encoded_info) % 4) % 4
    ciphertext = base64.b64decode(encoded_info + "=" * padding)

    cipher = AES.new(key, AES.MODE_CBC, iv)
    plaintext = cipher.decrypt(ciphertext)
    # Strip null bytes and any trailing AES padding/garbage after the JSON
    text = plaintext.rstrip(b"\x00").decode("utf-8", errors="ignore")
    # Find the end of the JSON object
    brace_count = 0
    json_end = 0
    for i, ch in enumerate(text):
        if ch == "{":
            brace_count += 1
        elif ch == "}":
            brace_count -= 1
            if brace_count == 0:
                json_end = i + 1
                break
    mqtt_config = json.loads(text[:json_end])
    _LOGGER.debug(
        "Decrypted MQTT config: broker=%s clientId=%s username=%s deviceId=%s",
        mqtt_config.get("broker"),
        mqtt_config.get("clientId"),
        mqtt_config.get("username"),
        mqtt_config.get("deviceId"),
    )

    return MqttCredentials(
        username=mqtt_config["username"],
        password=mqtt_config["password"],
        client_id=mqtt_config["clientId"],
        broker=mqtt_config.get("broker", ""),
        device_id=mqtt_config.get("deviceId", ""),
        mac=mqtt_config.get("mac", ""),
    )
