# Anycubic Photon P1 LAN Protocol

This document describes the local network protocol used by the Anycubic Photon P1 resin printer. The protocol was reverse-engineered from the Anycubic Photon Workshop desktop app (v4.1.4, macOS x86_64) by analyzing HTTP traffic, disassembling Mach-O binaries (`libmach_mqtt.dylib`, `libQMqttSDK.dylib`, `libQCloudSDK.dylib`, and the main `AnycubicPhotonWorkshop` executable), and inspecting config files.

## Overview

The printer exposes three services on the local network:

| Service | Port | Protocol | Purpose |
|---|---|---|---|
| HTTP API | 18910 | HTTP | Discovery, authentication handshake, file uploads |
| MQTT broker | 8883 | MQTT over TLS | Real-time status updates and printer control |
| Video server | 18088 | HTTP-FLV | Camera feed (only active after MQTT `startCapture`) |
| Unknown | 11311 | TCP | Accepts connections but does not send data unprompted |

Connecting to the MQTT broker requires a multi-step HTTP handshake to obtain ephemeral credentials. The token from the HTTP handshake is used to derive AES encryption keys, making the credentials short-lived.

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
  |  5. Query state for each       |                                |
  |     subtopic                   |                                |
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
- `cn` - device identifier (used as unique ID in the integration).
- `modelId` - numeric model identifier (used in MQTT topics).
- `deviceType` - `"lcd"` for resin printers, `"fdm"` for FDM printers. The protocol handler in the app has separate code paths (`router_lcd.cpp`, `router_fdm.cpp`).

## Step 2: POST /ctrl (Signed Request)

Requests encrypted MQTT credentials. Requires a valid `sign` parameter.

```
POST http://<printer_ip>:18910/ctrl?ts=<ms_timestamp>&nonce=<random_hex>&did=<any_string>&sign=<md5_hash>
Content-Length: 0
```

### Computing the `sign` parameter

Found in `SystemInfomation::get_LAN_signature(key, nonce, ts)` in the main binary (address `0x10001b330`). The function requires `key.size() > 15`.

```
sign = md5( md5(token[0:16]).hexdigest() + nonce + ts ).hexdigest()
```

Where:
- `token` - the 32-char token from Step 1 (passed as `key`)
- `ts` - current timestamp in milliseconds, as a string
- `nonce` - random hex string (15 characters, e.g. from `uuid4().hex[:15]`)

### The `did` parameter

The `did` (device ID) parameter can be **any non-empty string** — the printer does not validate it. The integration uses `"homeassistant"`.

For reference, the desktop app computes `did` as an MD5 hash of the macOS hardware UUID:

```python
# SystemInfomation::get_unique_id() at address 0x100018e60
# 1. IORegistryEntryFromPath(kIOMasterPortDefault, "IOService:/")
# 2. IORegistryEntryCreateCFProperty(entry, "IOPlatformUUID", ...)
# 3. QCryptographicHash(Algorithm=Md5)  (enum value 1 = MD5 in Qt5)
# 4. hash.addData(uuid_string)
# 5. hash.result().toHex()
did = md5(IOPlatformUUID).hexdigest()
```

This has no bearing on the one-client limitation (see [Client ID Limitation](#client-id-limitation) below).

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

Found in `SystemInfomation::decodeMqttConnData()` in the main binary:

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

The desktop app also bundles its own TLS client certificate (`AnycubicSlicer.crt` / `AnycubicSlicer.key`, issued by `AC Root CA`), but this appears to be used for cloud MQTT only, not LAN connections.

### Client ID Limitation

The MQTT broker only accepts the exact `clientId` from the decrypted credentials (e.g. `"clientId"`). Custom client IDs are rejected with "Not authorized". This means **only one MQTT client can be connected at a time** — connecting a second client with the same `clientId` disconnects the first (standard MQTT broker behavior).

This limitation is baked into the printer's firmware. The `did` parameter does not affect this — it's the fixed `clientId` in the MQTT credentials that causes the conflict. There is no client-side workaround; the only options are:

- **MQTT proxy/multiplexer** — a single process connects to the printer and fans out messages to multiple downstream consumers.
- **Coordinate access** — only run one client at a time (either the app or the integration, not both).

### MQTT Password Calculation (Cloud Mode)

For cloud MQTT connections (not LAN), the app uses `MqttRequestImpl::calculatePassword(MQTTConfig*)` which derives credentials using an embedded CA key. This is not needed for LAN connections where credentials come from the AES-decrypted response.

## MQTT Topics

### Subscribe (printer reports to client)

```
anycubic/anycubicCloud/v1/printer/public/{modelId}/{deviceId}/{subtopic}/report
```

Using the wildcard form:
```
anycubic/anycubicCloud/v1/printer/+/{modelId}/{deviceId}/+/report
```

The desktop app subscribes to an even broader wildcard:
```
anycubic/anycubicCloud/v1/+/public/{modelId}/{deviceId}/#
```

### Publish (client commands to printer)

```
anycubic/anycubicCloud/v1/pc/printer/{modelId}/{deviceId}/{subtopic}
```

The desktop app uses `slicer` instead of `pc` in its publish path:
```
anycubic/anycubicCloud/v1/slicer/printer/{modelId}/{deviceId}/{subtopic}
```

Both work — the printer matches on the topic structure, not the specific path component before `printer`.

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

- `type` - the subtopic name (e.g. `"print"`, `"status"`, `"video"`)
- `action` - the specific action (see [Action Reference](#action-reference) below)
- `msgid` - a UUID v4 string identifying the message
- `state` - context-dependent state string
- `timestamp` - milliseconds since epoch
- `code` - response code (200 = success)
- `data` - payload object, or `null`

The `state` field has different meanings depending on context:
- For `status` subtopic: printer state (`"idle"`, `"busy"`)
- For `print` subtopic: print state (`"printing"`, `"paused"`, `"monitoring"`)
- For other subtopics: message acknowledgment (`"done"`)

## Requesting Current State

Publishing `{}` (empty JSON object) to a subtopic triggers the printer to respond with its current state for that topic. This is a convenient shortcut.

For more targeted queries, publish a properly structured message with a specific query action:

```json
{
  "type": "print",
  "action": "query",
  "timestamp": 1773139043232,
  "msgid": "550e8400-e29b-41d4-a716-446655440000",
  "data": null
}
```

Key query actions per subtopic:

| Subtopic | Query Action | Notes |
|---|---|---|
| `print` | `query` | Request current print job state |
| `status` | (automatic) | Printer pushes `free`/`BUSY`/`workReport` on its own |
| `network` | `queryInfo` | Request network information |
| `peripherie` | `query` | Request peripheral status |
| `info` | `report` | Request printer info |
| `releaseFilm` | `get` | Request release film count |
| `axis` | `query` | Request axis position |
| `autoOperation` | `get` | Request auto-operation settings |

## MQTT Subtopics

The full list of subtopics, extracted from `gs_typeNodes` in `libmach_mqtt.dylib`:

| Enum | Subtopic | Direction | Description |
|------|----------|-----------|-------------|
| 1 | `file` | both | File management (list/delete on USB and local storage) |
| 2 | `print` | both | Print job status and control (start/pause/resume/stop) |
| 3 | `axis` | both | Z-axis movement and homing |
| 4 | `tempature` | printer→client | Temperature readings (note: typo is in the firmware) |
| 5 | `filament` | both | Filament management (replace, set type) |
| 6 | `screenSaver` | both | Screen saver settings |
| 7 | `levelling` | both | Auto-leveling |
| 8 | `zoff` | both | Z offset calibration |
| 9 | `preheating` | both | Preheating control |
| 10 | `fan` | both | Fan control |
| 11 | `status` | printer→client | Printer status (idle/busy) |
| 12 | `video` | both | Camera stream control and timelapse |
| 13 | `network` | both | Network information |
| 14 | `info` | printer→client | Printer info report |
| 15 | `lastWill` | printer→client | MQTT last will (online/offline) |
| 16 | `wifi` | both | WiFi signal strength |
| 17 | `exposure` | both | UV exposure test |
| 18 | `residual` | both | Residual resin cleaning |
| 19 | `ota` | both | Firmware OTA updates |
| 21 | `airpure` | both | Air purifier on/off |
| 23 | `releaseFilm` | both | Release film usage counter |
| 24 | `smartResinVat` | both | Smart resin vat (cyclic cleaning) |
| 25 | `resin` | both | Resin feed management |
| 26 | `autoOperation` | both | Auto-operation settings |
| 27 | `multiColorBox` | both | Multi-color box / AMS control |
| 28 | `light` | both | UV light / chamber light control |
| 29 | `extfilbox` | printer→client | External filament box status |
| 30 | `peripherie` | both | Peripheral device status |

Note: enum 20 (`MQTT`) is used internally for connection events (CONNECT, DISCONNECT, SUBSCRIBE, etc.) and is not a real subtopic.

## Action Reference

All actions were extracted from `gs_nodeContainer` in `libmach_mqtt.dylib` (74 entries). Each action belongs to a subtopic (event type) and has a unique enum value within that subtopic.

### `print` (type 2) - Print Job Control

| Action | Enum | Direction | Description |
|--------|------|-----------|-------------|
| `start` | 103 | client→printer | Start a print job |
| `pause` | 104 | client→printer | Pause current print |
| `resume` | 105 | client→printer | Resume paused print |
| `stop` | 106 | client→printer | Stop/cancel current print |
| `query` | 107 | client→printer | Query current print state |
| `update` | 108 | printer→client | Print progress update |
| `ignore` | 109 | client→printer | Ignore a warning or error |
| `monitor` | 110 | printer→client | Pre-print monitoring status |
| `autoOperation` | 111 | printer→client | Auto-operation check results |
| `reDetect` | 112 | client→printer | Retry a failed pre-print check |

### `status` (type 11) - Printer Status

| Action | Enum | Direction | Description |
|--------|------|-----------|-------------|
| `free` | 200 | printer→client | Printer is idle |
| `BUSY` | 201 | printer→client | Printer is busy |
| `workReport` | 202 | printer→client | Work status report |

### `file` (type 1) - File Management

| Action | Enum | Direction | Description |
|--------|------|-----------|-------------|
| `listUdisk` | 1 | client→printer | List files on USB drive |
| `listLocal` | 2 | client→printer | List files on internal storage |
| `deleteUdisk` | 3 | client→printer | Delete file from USB drive |
| `deleteLocal` | 4 | client→printer | Delete file from internal storage |

### `axis` (type 3) - Z-Axis Control

| Action | Enum | Direction | Description |
|--------|------|-----------|-------------|
| `move` | 800 | client→printer | Move Z axis |
| `turnOff` | 801 | client→printer | Turn off axis motors |
| `query` | 802 | client→printer | Query axis position |
| `moveToCoordinates` | 803 | client→printer | Move to specific coordinates |

### `tempature` (type 4) - Temperature

| Action | Enum | Direction | Description |
|--------|------|-----------|-------------|
| `auto` | 300 | printer→client | Automatic temperature report |

### `filament` (type 5) - Filament Management

| Action | Enum | Direction | Description |
|--------|------|-----------|-------------|
| `replace` | 1300 | client→printer | Replace filament |
| `set` | 1301 | client→printer | Set filament type |

### `video` (type 12) - Camera / Video

| Action | Enum | Direction | Description |
|--------|------|-----------|-------------|
| `startCapture` | 400 | client→printer | Start camera stream |
| `stopCapture` | 401 | client→printer | Stop camera stream |

### `network` (type 13) - Network Info

| Action | Enum | Direction | Description |
|--------|------|-----------|-------------|
| `queryInfo` | 1900 | client→printer | Query network information |

### `info` (type 14) - Printer Info

| Action | Enum | Direction | Description |
|--------|------|-----------|-------------|
| `report` | 2400 | printer→client | Printer info report |

### `lastWill` (type 15) - Online Status

| Action | Enum | Direction | Description |
|--------|------|-----------|-------------|
| `onlineReport` | 1000 | printer→client | Online/offline status |

### `wifi` (type 16) - WiFi

| Action | Enum | Direction | Description |
|--------|------|-----------|-------------|
| `getSignalStrength` | 600 | client→printer | Query WiFi signal strength |

### `exposure` (type 17) - UV Exposure Test

| Action | Enum | Direction | Description |
|--------|------|-----------|-------------|
| `start` | 700 | client→printer | Start UV exposure test |
| `cancel` | 701 | client→printer | Cancel UV exposure test |

### `residual` (type 18) - Resin Cleaning

| Action | Enum | Direction | Description |
|--------|------|-----------|-------------|
| `clean` | 900 | client→printer | Start residual resin cleaning |
| `cancel` | 901 | client→printer | Cancel cleaning |

### `ota` (type 19) - Firmware Updates

| Action | Enum | Direction | Description |
|--------|------|-----------|-------------|
| `reportVersion` | 1100 | printer→client | Report current firmware version |
| `update` | 1101 | client→printer | Initiate firmware update |
| `updateSuccessProcessed` | 1102 | client→printer | Acknowledge successful update |
| `updateFailedProcessed` | 1103 | client→printer | Acknowledge failed update |
| `start` | 1104 | printer→client | Update started |
| `downloading` | 1105 | printer→client | Downloading firmware |
| `download-failed` | 1106 | printer→client | Download failed |
| `updating` | 1107 | printer→client | Applying update |
| `canceled` | 1108 | printer→client | Update canceled |
| `update-success` | 1102 | printer→client | Update succeeded |
| `update-failed` | 1103 | printer→client | Update failed |

### `airpure` (type 21) - Air Purifier

| Action | Enum | Direction | Description |
|--------|------|-----------|-------------|
| `off` | 1200 | client→printer | Turn off air purifier |
| `on` | 1201 | client→printer | Turn on air purifier |

### `releaseFilm` (type 23) - Release Film Counter

| Action | Enum | Direction | Description |
|--------|------|-----------|-------------|
| `get` | 1500 | client→printer | Get release film usage count |
| `reset` | 1501 | client→printer | Reset release film counter |

### `smartResinVat` (type 24) - Smart Resin Vat

| Action | Enum | Direction | Description |
|--------|------|-----------|-------------|
| `reportInfo` | 1600 | printer→client | Report vat info |
| `cyclicCleaning` | 1601 | client→printer | Start cyclic cleaning |

### `resin` (type 25) - Resin Management

| Action | Enum | Direction | Description |
|--------|------|-----------|-------------|
| `reportAutoFeedInfo` | 1700 | printer→client | Auto-feed status report |
| `feedResin` | 1701 | client→printer | Feed resin |

### `autoOperation` (type 26) - Auto-Operation Settings

| Action | Enum | Direction | Description |
|--------|------|-----------|-------------|
| `get` | 1800 | client→printer | Get auto-operation settings |
| `set` | 1801 | client→printer | Set auto-operation settings |
| `reportStatus` | 1802 | printer→client | Report auto-operation status |

### `multiColorBox` (type 27) - Multi-Color Box / AMS

| Action | Enum | Direction | Description |
|--------|------|-----------|-------------|
| `getInfo` | 2000 | client→printer | Get box info |
| `setDry` | 2001 | client→printer | Set drying mode |
| `feedFilament` | 2002 | client→printer | Feed filament |
| `finishFeedFilament` | 2003 | client→printer | Finish feeding filament |
| `setAutoFeed` | 2004 | client→printer | Set auto-feed |
| `refresh` | 2005 | client→printer | Refresh box status |
| `setInfo` | 2006 | client→printer | Set box info |
| `autoUpdateDryStatus` | 2007 | printer→client | Auto drying status update |
| `autoUpdateInfo` | 2008 | printer→client | Auto info update |

### `light` (type 28) - Light Control

| Action | Enum | Direction | Description |
|--------|------|-----------|-------------|
| `control` | 2100 | client→printer | Control light brightness |

### `extfilbox` (type 29) - External Filament Box

| Action | Enum | Direction | Description |
|--------|------|-----------|-------------|
| `reportInfo` | 2200 | printer→client | Report external filament box info |

### `peripherie` (type 30) - Peripherals

| Action | Enum | Direction | Description |
|--------|------|-----------|-------------|
| `query` | 2300 | client→printer | Query peripheral status |

## Print Control Commands

All commands are published to:
```
anycubic/anycubicCloud/v1/pc/printer/{modelId}/{deviceId}/print
```

### Pause Print

```json
{
  "type": "print",
  "action": "pause",
  "timestamp": 1773139043232,
  "msgid": "550e8400-e29b-41d4-a716-446655440000",
  "data": null
}
```

### Resume Print

```json
{
  "type": "print",
  "action": "resume",
  "timestamp": 1773139043232,
  "msgid": "550e8400-e29b-41d4-a716-446655440000",
  "data": null
}
```

### Stop / Cancel Print

```json
{
  "type": "print",
  "action": "stop",
  "timestamp": 1773139043232,
  "msgid": "550e8400-e29b-41d4-a716-446655440000",
  "data": null
}
```

### Query Print State

```json
{
  "type": "print",
  "action": "query",
  "timestamp": 1773139043232,
  "msgid": "550e8400-e29b-41d4-a716-446655440000",
  "data": null
}
```

### Start Print (from uploaded file)

The `start` action requires a `data` payload with the file information. The struct was reconstructed from `mach_mqtt::FDM::Control::PrintStart` reflection metadata in `libmach_mqtt.dylib`:

```json
{
  "type": "print",
  "action": "start",
  "timestamp": 1773139043232,
  "msgid": "550e8400-e29b-41d4-a716-446655440000",
  "data": {
    "taskid": "0",
    "url": "/local/model.pp1",
    "md5": "d41d8cd98f00b204e9800998ecf8427e",
    "project_type": "local",
    "use_ams": false,
    "auto_leveling": 0,
    "vibration_compensation": 0,
    "timelapse": 0,
    "ams_settings": {},
    "task_settings": {}
  }
}
```

### Ignore Warning

```json
{
  "type": "print",
  "action": "ignore",
  "timestamp": 1773139043232,
  "msgid": "550e8400-e29b-41d4-a716-446655440000",
  "data": null
}
```

### Re-Detect (Retry Failed Check)

```json
{
  "type": "print",
  "action": "reDetect",
  "timestamp": 1773139043232,
  "msgid": "550e8400-e29b-41d4-a716-446655440000",
  "data": null
}
```

## Other Control Commands

### Light Control

Published to `.../light`:

```json
{
  "type": "light",
  "action": "control",
  "timestamp": 1773139043232,
  "msgid": "550e8400-e29b-41d4-a716-446655440000",
  "data": {
    "brightness": 50
  }
}
```

### Air Purifier

Published to `.../airpure`:

```json
{
  "type": "airpure",
  "action": "on",
  "timestamp": 1773139043232,
  "msgid": "550e8400-e29b-41d4-a716-446655440000",
  "data": null
}
```

Use `"off"` to turn off.

### UV Exposure Test

Published to `.../exposure`:

```json
{
  "type": "exposure",
  "action": "start",
  "timestamp": 1773139043232,
  "msgid": "550e8400-e29b-41d4-a716-446655440000",
  "data": null
}
```

Use `"cancel"` to stop.

### Z-Axis Movement

Published to `.../axis`:

```json
{
  "type": "axis",
  "action": "move",
  "timestamp": 1773139043232,
  "msgid": "550e8400-e29b-41d4-a716-446655440000",
  "data": null
}
```

Other axis actions: `turnOff` (disable motors), `moveToCoordinates`, `query`.

### File Management

Published to `.../file`:

```json
{
  "type": "file",
  "action": "listLocal",
  "timestamp": 1773139043232,
  "msgid": "550e8400-e29b-41d4-a716-446655440000",
  "data": null
}
```

Actions: `listUdisk`, `listLocal`, `deleteUdisk`, `deleteLocal`.

### Release Film Counter

Published to `.../releaseFilm`:

```json
{
  "type": "releaseFilm",
  "action": "get",
  "timestamp": 1773139043232,
  "msgid": "550e8400-e29b-41d4-a716-446655440000",
  "data": null
}
```

Use `"reset"` to reset the counter.

## Print Job Data

When a print starts or progresses, the `print` subtopic reports updates:

```json
{
  "type": "print",
  "action": "start",
  "state": "printing",
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
- `model_hight` - model height in mm (note: typo is in the firmware)
- `z_thick` - layer thickness in mm

Additional fields found in the binary but not yet observed in traffic: `print_speed_mode`, `camera_timelapse_support`, `cameraReady`, `trans_layers`.

## Properties Data

The `properties` subtopic reports sensor readings:

```json
{
  "data": {
    "resin_temp": 27.04
  }
}
```

Other property fields found in the binary: `curr_hotbed_temp`, `curr_nozzle_temp`, `target_hotbed_temp`, `target_nozzle_temp`, `target_temp` (these are primarily for FDM printers).

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

The client can respond with `ignore` to skip a failed check, or `reDetect` to retry it.

## Camera Stream (HTTP-FLV)

The printer has a built-in camera that streams video as FLV over HTTP. The stream is not always available — it must be activated via an MQTT command and remains active only while the MQTT client stays connected.

### Stream Properties

| Property | Value |
|---|---|
| URL | `http://<printer_ip>:18088/flv` |
| Container | FLV |
| Codec | H.264 Constrained Baseline |
| Resolution | 640x480 |
| Frame rate | 10 fps |
| Color space | YUV420P progressive |

The server sets `Content-Length: 99999999999` (effectively infinite) and `Access-Control-Allow-Origin: *`.

### Activation

Publish `startCapture` to the `video` subtopic:

```
Topic: anycubic/anycubicCloud/v1/pc/printer/{modelId}/{deviceId}/video
```

```json
{
  "type": "video",
  "action": "startCapture",
  "timestamp": 1773202214897,
  "msgid": "eab01877-e3e2-494b-af65-1a21be1ab8b8",
  "data": null
}
```

The printer responds on `video/report`:

```json
{
  "type": "video",
  "action": "startCapture",
  "msgid": "9cb8d24f-00d2-4d75-a10a-d9b89be79895",
  "state": "initSuccess",
  "timestamp": 1773202215403,
  "code": 200,
  "msg": "LanStream start success",
  "data": null
}
```

After this response, the FLV stream is available at `http://<printer_ip>:18088/flv`.

### Lifecycle

- The stream stays active as long as the MQTT client that sent `startCapture` remains connected.
- When the MQTT client disconnects, the printer stops the stream and publishes `{"msg": "LanStream push ended"}` on the `video/report` topic.
- `GET http://<printer_ip>:18088/` returns `ok` (can be used as a health check).
- `GET http://<printer_ip>:18088/flv` returns `ok` (2 bytes) when the stream is not active, and the FLV byte stream when it is.
- To stop the stream without disconnecting MQTT, publish `stopCapture`.

### Other Video Actions

The `video` subtopic also supports timelapse management:

| Action | Description |
|---|---|
| `listVideo` | List timelapse recordings on the printer |
| `deleteVideo` | Delete a timelapse recording |

### Cloud Mode (Agora RTC)

When the printer is accessed through the Anycubic cloud (not LAN), it uses the Agora RTC SDK instead of HTTP-FLV. In that mode, the `startCapture` response includes Agora credentials in the `data` field (`appId`, `token`, `channel`, `userId`, `remoteId`, `encryptionKey`, `encryptionSalt`, `encryptionMode`). This is not relevant for LAN-only integrations.

### Implementation Notes

- The desktop app's `RtspDecoder` (despite the name) consumes the FLV stream using FFmpeg with flags: `flv_no_metadata`, `flv_ignore_chunks`, `nobuffer+igndts+discardcorrupt`.
- The `startCapture` must be sent after every MQTT reconnect. The printer does not remember previous stream state.
- The printer status data includes `camera_timelapse_support` (bool) and `cameraReady` (bool) fields that indicate camera capability.

## Token Lifetime

The `/info` token is ephemeral — it changes on every request. The MQTT credentials derived from it are also short-lived. On reconnection, the full handshake (GET /info -> POST /ctrl -> AES decrypt) must be repeated.

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

### Port 18910 (API)

| Endpoint | Method | Auth | Description |
|---|---|---|---|
| `/info` | GET | None | Printer info + ephemeral token |
| `/ctrl` | POST | Signed query params | Encrypted MQTT credentials |
| `/upload/` | POST | Unknown | File upload for print jobs |

### Port 18088 (Video)

| Endpoint | Method | Auth | Description |
|---|---|---|---|
| `/` | GET | None | Health check, returns `ok` |
| `/flv` | GET | None | FLV camera stream (only active after MQTT `startCapture`) |

## Cloud Infrastructure

The desktop app connects to Anycubic's cloud services for remote access. The URLs are stored in `environment.ini` encoded as base64 + byte shift of 5. Decoded values:

| Service | China | International |
|---|---|---|
| Cloud API | `https://cloud-platform.anycubicloud.com/p/p/workbench/api` | `https://cloud-universe.anycubic.com/p/p/workbench/api` |
| Cloud MQTT | `mqtt.anycubic.com:8883` | `mqtt-universe-testnew.anycubic.com:8883` |

The cloud API uses HTTP headers for authentication:
- `XX-Timestamp` - current time in milliseconds
- `XX-Nonce` - UUID v4 string
- `XX-Signature` - `MD5_hex(key1 + nonce + secret + key2 + timestamp + key1)` where `key1 = f9b3528877c94d5c9c5af32245db46ef`, `key2 = 0cf75926606049a3937f56b0373b99fb`, `secret = "V3.0.0"`
- `XX-Token` - login token (when authenticated)
- `XX-Device-Type`, `XX-IS-CN`, `XX-LANGUAGE`, `XX-Version` - device metadata

These are not used by the LAN integration.

## Binary Analysis Notes

The protocol implementation spans several libraries in the app bundle:

| Library | Source Path | Purpose |
|---|---|---|
| Main binary | — | `SystemInfomation` class (get_unique_id, get_LAN_signature, decodeMqttConnData, get_nonce) |
| `libmach_mqtt.dylib` | `cloud_connect/mach_mqtt/` | Machine MQTT protocol: action/event enum tables (`gs_nodeContainer`, `gs_typeNodes`), message encoding, protocol handlers (`common_handler.cpp`, `fdm_handler.cpp`) |
| `libQMqttSDK.dylib` | `cloud_connect/cloud_mqtt/` | Qt MQTT wrapper: `MqttRequestHandler`, `MqttRequestImpl`, topic creation (`CreateTopic`), message routing for LCD and FDM printers |
| `libQCloudSDK.dylib` | `cloud_connect/cloud_sdk_cpp/` | Cloud SDK: HTTP API client (`BaseClient`), auth headers (getSignature, getNonce), `Ctrl::CtrlImpl` for file upload/download |
| `libmqtt_client.dylib` | `cloud_connect/mqtt_client/` | Low-level C MQTT client (wraps paho-mqtt3as) |
| `libconnect_cloud.1.0.0.dylib` | — | Cloud connection: TencentCOS uploads, printer management API, `NetworkRequest` class |

The `gs_nodeContainer` table in `libmach_mqtt.dylib` (at address `0x48490`, 74 entries of 16 bytes each) maps `(event_type, action_string) → action_enum`. The `gs_typeNodes` table (at `0x482b0`, 30 entries of 16 bytes each) maps `event_type_string → event_type_enum`. These tables are the authoritative source for the subtopic and action names documented above.

The C++ structs use [iguana](https://github.com/qicosmos/iguana) for JSON serialization, with compile-time reflection. Struct field names visible in the binary correspond directly to JSON field names in the protocol messages.
