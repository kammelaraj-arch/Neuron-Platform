# MQTT Topic Reference (Edge ↔ Pico)

The Neuron Edge Runtime ships an embedded **Eclipse Mosquitto** broker
(see `edge_runtime/docker-compose.yml`). The Edge bridges its in-memory
twin cache to MQTT in both directions, so a Pico 2 W can publish/subscribe
to canonical topics directly without going through HTTP.

This co-exists with the existing REST + SSE shim — clients that don't
speak MQTT keep working unchanged.

## Topic shape

```
twin/<twin_kind>/<device_dna>/<channel>/<field>
device/<device_dna>/heartbeat
alarm/<device_dna>/<alarm_id>          (planned)
```

Where:

| Token | Allowed values |
| --- | --- |
| `twin_kind` | `heater` / `motor` / `scale` / `smart_plug` / `camera` / `recorder` / `safety` |
| `device_dna` | `DNA-XXXX-XXXX-XXXX-XXXX` |
| `channel` | `desired` / `reported` / `current` |
| `field` | The leaf field name from the twin's metadata, e.g. `setpoint_c`, `process_temp_c`, `state`, `power_w` |

### Examples

```
twin/heater/DNA-AAAA-BBBB-CCCC-DDDD/desired/setpoint_c   ← Master sets, Pico subscribes
twin/heater/DNA-AAAA-BBBB-CCCC-DDDD/reported/state       ← Pico publishes, Edge mirrors
twin/heater/DNA-AAAA-BBBB-CCCC-DDDD/current/ssr_duty     ← Pico publishes
twin/scale/DNA-AAAA-BBBB-CCCC-DDDD/reported/stable_weight_g
device/DNA-AAAA-BBBB-CCCC-DDDD/heartbeat                  ← Pico every 15 s
```

## Payload format

Every payload is a JSON-encoded scalar or object. Examples:

```
80                       # number
"PREHEAT"                # string
true                     # bool
{ "iso": "2026-05-09T12:00:00Z" }  # complex
```

The bridge JSON-decodes on receive; if decode fails it falls back to
treating the payload as a raw UTF-8 string.

## Retention

Every published twin field is **retained**. New subscribers (e.g. a
freshly-booted dashboard widget) get the latest value immediately on
subscription, no replay loop needed.

## Security

The default broker is **anonymous, local-LAN only** (port 1883 on the
docker0 / edge LAN bridge). The Edge access policy already denies all
non-LAN inbound HTTP, and the broker is not exposed externally:

  * Master ↔ Edge twin synchronisation goes over HTTPS via the
    `/api/v1/internal/twin-push` push and the SSE stream.
  * Pico ↔ Edge MQTT stays inside the factory LAN.

For sites that want mutual-TLS MQTT, add an `mqtt+tls 8883` listener
to `edge_runtime/mosquitto/mosquitto.conf` and pin the CA in the Edge
access policy.

## How the Edge bridges in / out

`edge_runtime/edge/mqtt_bridge.py` runs a single async task that:

1. **Subscribes** to `twin/+/+/+/+` and `device/+/heartbeat`.
2. On every received message → `twin_cache.merge()`. That single
   call propagates everywhere:
   * REST clients see it on `GET /api/v1/twin/{dna}`.
   * The Master receives a push at `/api/v1/internal/twin-push`,
     which fans out via SSE to any open browser.
3. On every successful `twin_cache.merge()` (from any source —
   REST, SSE, MQTT itself), the bridge **publishes** the new field
   value back under its canonical topic, retained.

So a Pico publishing `twin/heater/<dna>/reported/process_temp_c = 42.3`
shows up on every dashboard within the network round-trip + browser
SSE delivery (≪ 1 s).

## Pico client

The shipped firmware uses an HTTP shim (`mqtt_client.py` despite the
name — it speaks REST against the Edge's `/api/v1/twin/...` endpoints)
because some early MicroPython builds had memory issues with
`umqtt.simple` under TLS. To switch a Pico to native MQTT once your
build can spare the RAM:

```python
from umqtt.simple import MQTTClient
mq = MQTTClient(b"pico-1", b"<edge-ip>", port=1883, keepalive=30)
mq.connect()
mq.publish(b"twin/heater/<dna>/reported/process_temp_c", b"42.3", retain=True)
```

The Edge accepts both transports interchangeably — same topic shape,
same canonical state.
