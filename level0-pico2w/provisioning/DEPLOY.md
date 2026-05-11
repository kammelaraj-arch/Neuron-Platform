# Pico 2 W — Field deploy

## On-site checklist

- [ ] Power supply: 5 V, ≥1 A. Pico 2 W draws ~70 mA typical, peripherals add to this.
- [ ] Wi-Fi credentials in `config.json` match the factory LAN.
- [ ] Edge runtime reachable from the device: `ping <edge-host>`.
- [ ] Master DNA already issued for this hardware (no firmware bundle = device
      cannot start).
- [ ] At least one `twin_control` listed in the DNA matches the wiring.

## Healthy boot signature

A correctly-flashed Pico prints, on the REPL:

```
[boot] dna= DNA-XXXX-XXXX-XXXX-XXXX  wifi= up
{"t":..., "lvl":"info","src":"main","msg":"boot","kv":{"dna":"DNA-...","fw":"1.0.0"}}
{"t":..., "lvl":"info","src":"main","msg":"heartbeat_ok"}
```

If the heartbeat never goes `ok`, check the Edge dashboard at
`http://<edge-host>:8090/` — every device that has reached the edge appears
there even on the very first heartbeat.

## OTA upgrade

The Master writes a signed bundle and exposes it at
`/api/devices/{dna}/firmware-bundle`. Once Stage 4 lands, the Pico will
poll that URL and apply the bundle subject to the
`min_supported_base_firmware_version` /
`supported_hardware_revisions` gates already encoded in
`shared_schemas/ota_manifest_schema.json`.

Until then, OTA is a manual `mpremote cp` of the new bundle followed by
a power-cycle. `ota_client.py` already implements the version-gate check
locally so the device will refuse an incompatible bundle.
