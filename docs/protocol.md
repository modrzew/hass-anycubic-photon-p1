# Anycubic Photon P1 LAN Protocol

This document describes the local network protocol used by the Anycubic Photon P1 resin printer. The protocol was reverse-engineered from the Anycubic Photon Workshop desktop app (v4.1.4, macOS) by analyzing HTTP traffic, binary libraries, and config files.

## Overview

The printer exposes two services on the local network:

- **HTTP server** on port **18910** - used for discovery, authentication handshake, and file uploads
- **MQTT broker** on port **8883** (TLS) - used for real-time status updates and printer control

Connecting to the MQTT broker requires a multi-step handshake to obtain ephemeral credentials. The token from the HTTP handshake is used to derive AES encryption keys, making the credentials short-lived.

## Connection Flow

```
Client                          Printer (:18910)              Printer MQTT (:8883)
  |                                |                                |
  |  1. GET /info                  |                                |
  |------------------------------->|                                |
  |  token, modelId, deviceName    |                                |
  |<-------------------------------|                                |
  |                                |                                |
  |  2. POST /ctrl?sign=...        |                                |
  |------------------------------->|                                |
  |  AES-encrypted MQTT creds      |                                |
  |<-------------------------------|                                |
  |                                |                                |
  |  3. Decrypt creds with AES     |                                |
  |                                |                                |
  |  4. Connect MQTT over TLS      |                                |
  |---------------------------------------------------------------->|
  |  Subscribe to .../+/report     |                                |
  |---------------------------------------------------------------->|
  |  Status updates (push)         |                                |
  |<----------------------------------------------------------------|
```

## Step 1: GET /info

Fetches printer identity and an ephemeral token.

```
GET http://<printer_ip>:18910/info
```

Response:
```json
{
  "cn": "78DE-A7B5-68F0-203C",
  "code": 0,
  "ctrlInfoUrl": "http://<printer_ip>:18910/ctrl",
  "ctrlType": "lan",
  "deviceName": "Anycubic Photon P1",
  "deviceType": "lcd",
  "env": "prod",
  "ip": "<printer_ip>",
  "message": "OK",
  "modelId": "131",
  "modelName": "Anycubic Photon P1",
  "token": "<32-char ephemeral token>",
  "zone": "cn"
}
```

Key fields:
- `token` - 32-character string, changes on each request. Used to compute the `sign` parameter and as the AES key material.
- `cn` - device identifier (used as unique ID in the integration)
- `modelId` - numeric model identifier (used in MQTT topics)

## Step 2: POST /ctrl (Signed Request)

Requests encrypted MQTT credentials. Requires a valid `sign` parameter.

```
POST http://<printer_ip>:18910/ctrl?ts=<ms_timestamp>&nonce=<random_hex>&did=<any_string>&sign=<md5_hash>
Content-Length: 0
```

### Computing the `sign` parameter

Found in `SystemInfomation::get_LAN_signature()` in the app binary:

```
sign = md5( md5(token[0:16]).hexdigest() + ts + nonce ).hexdigest()
```

Where:
- `token` - the 32-char token from Step 1
- `ts` - current timestamp in milliseconds, as a string
- `nonce` - random hex string (15 characters, e.g. from `uuid4().hex[:15]`)

The `did` parameter can be any non-empty string (the printer doesn't validate it). We use `"homeassistant"`.

### Response

```json
{
  "code": 200,
  "data": {
    "info": "<base64-encoded AES ciphertext>",
    "token": "<16-char string used as AES IV>"
  },
  "message": "success"
}
```

Without a valid `sign`, returns `{"code": 19002, "message": "failed"}`.

## Step 3: AES Decryption

Found in `CtrlInfo::FromData()` in `libaccloud.1.0.0.dylib`:

```
key        = info_token[16:32]    # last 16 chars of the /info token
iv         = ctrl_token           # the 16-char "token" from /ctrl response
ciphertext = base64_decode(info)  # the "info" field from /ctrl response
plaintext  = AES-128-CBC-decrypt(ciphertext, key, iv)
```

The plaintext (after stripping null-byte padding) is JSON:

```json
{
  "broker": "<printer_ip>:8883",
  "clientId": "clientId",
  "username": "clientUser1",
  "password": "admin",
  "cacrt": "-----BEGIN CERTIFICATE-----\n...",
  "deviceId": "0208201620172018a7128a08f3b5",
  "deviceType": "lcd",
  "mac": "C0:09:25:F4:36:86",
  "ip": "<printer_ip>",
  "fileUploadUrl": "http://<printer_ip>:18910/upload/"
}
```

## Step 4: MQTT Connection

Connect to the printer's built-in MQTT broker using the decrypted credentials.

### TLS Configuration

The printer uses TLS 1.2 with a self-signed certificate:
- Issuer: `O=TLS Project Dodgy Certificate Authority`
- Key: RSA 1024-bit
- Signature: sha1WithRSAEncryption

Because of the weak crypto, clients must:
- Disable hostname verification
- Disable certificate verification
- Set `SECLEVEL=0` in the cipher string (required for RSA 1024-bit)

### Client ID Limitation

The MQTT broker only accepts the exact `clientId` from the decrypted credentials. Custom client IDs are rejected with "Not authorized". This means **only one client can be connected at a time** - if the Anycubic Photon Workshop app connects, it will disconnect the HA integration, and vice versa.

### Subscribe Topics

```
anycubic/anycubicCloud/v1/printer/public/{modelId}/{deviceId}/{subtopic}/report
```

Using the wildcard form:
```
anycubic/anycubicCloud/v1/printer/+/{modelId}/{deviceId}/+/report
```

### Publish Topics (client to printer)

```
anycubic/anycubicCloud/v1/pc/printer/{modelId}/{deviceId}/{subtopic}
```

Publishing `{}` to a subtopic requests the printer to send its current state for that topic.

## MQTT Subtopics

| Subtopic | Direction | Description |
|---|---|---|
| `status` | printer -> client | Printer state (idle, busy, etc.) |
| `print` | both | Print job status and control |
| `properties` | printer -> client | Printer properties (resin temperature) |
| `light` | both | UV light control |
| `video` | both | Camera feed |
| `peripherie` | printer -> client | Peripheral status |
| `releaseFilm` | printer -> client | Release film status |

## Message Format

All MQTT messages are JSON with a common envelope:

```json
{
  "type": "<subtopic>",
  "action": "<action_name>",
  "msgid": "<uuid>",
  "state": "<message_state>",
  "timestamp": 1773139043232,
  "code": 200,
  "msg": "",
  "data": { ... }
}
```

The `state` field at the top level has different meanings depending on context:
- For `status` subtopic: printer state (`"idle"`, `"busy"`)
- For `print` subtopic: print state (`"printing"`, `"paused"`, `"monitoring"`)
- For other subtopics: message acknowledgment (`"done"`)

The `data` field contains the actual payload, or `null` for some messages.

## Print Job Data

When a print starts, the `print` subtopic reports (action: `start`):

```json
{
  "data": {
    "taskid": "0",
    "filename": "model.pp1",
    "remain_time": 68,
    "model_hight": 14.6,
    "curr_layer": 0,
    "total_layers": 292,
    "supplies_usage": 19.25,
    "progress": 0,
    "z_thick": 0.05,
    "print_time": 0,
    "slicer": "ANYCUBIC-PC",
    "settings": {
      "on_time": 1.8,
      "off_time": 2,
      "bottom_time": 20,
      "bottom_layers": 1,
      "z_up_height": 10,
      "z_up_speed": 6,
      "z_down_speed": 6
    }
  }
}
```

Key fields:
- `progress` - 0 to 100 (percentage)
- `curr_layer` / `total_layers` - layer progress
- `remain_time` - minutes remaining
- `print_time` - minutes elapsed
- `supplies_usage` - resin used in mL
- `model_hight` - model height in mm (note the typo in the field name)
- `z_thick` - layer thickness in mm

## Properties Data

The `properties` subtopic reports sensor readings:

```json
{
  "data": {
    "resin_temp": 27.04
  }
}
```

- `resin_temp` - resin temperature in degrees Celsius

## Auto-Operation / Monitoring Messages

During print startup, the printer sends monitoring messages on the `print` subtopic with `action: "autoOperation"` or `action: "monitor"`. These contain `checkStatus` arrays listing pre-print checks:

```json
{
  "data": {
    "checkStatus": [
      {"name": "FileVerification", "status": 0},
      {"name": "levelling", "status": 0},
      {"name": "resin", "status": 0}
    ]
  }
}
```

Status values: `0` = passed, `-1` = skipped, `-2` = pending/not applicable.

## Token Lifetime

The `/info` token is ephemeral - it changes on every request. The MQTT credentials derived from it are also short-lived. On reconnection, the full handshake (GET /info -> POST /ctrl -> AES decrypt) must be repeated.

## Discovery (SSDP)

The printer responds to SSDP M-SEARCH requests:

```
M-SEARCH * HTTP/1.1
HOST: 239.255.255.250:1900
MAN: "ssdp:discover"
MX: 3
ST: ac:3dprinter:lcd
```

This is not currently used by the integration (IP is configured manually).

## HTTP Endpoints Summary

| Endpoint | Method | Auth | Description |
|---|---|---|---|
| `/info` | GET | None | Printer info + ephemeral token |
| `/ctrl` | POST | Signed query params | Encrypted MQTT credentials |
| `/upload/` | POST | Unknown | File upload for print jobs |
