"""Edge integrations — vendor-specific drivers that bridge external devices
into the Neuron twin model.

Each integration:

  * Reads its config (typically from EDGE_<VENDOR>_DEVICES env var or a
    JSON file in /app/edge_runtime/data/<vendor>.json).
  * Polls the device for its current state, calls
    ``twin_cache.merge(dna, "reported", {...})`` so the value flows out
    via REST + SSE + MQTT automatically.
  * Subscribes to ``cache.merge`` for ``desired`` updates on its DNAs and
    pushes them down to the device.

Failures are logged at warn-level only — the Edge keeps running even if
a vendor's cloud / device is unreachable.
"""
