# Pico 2 W — Factory acceptance checklist

| # | Step                                                              | Pass |
| - | ----------------------------------------------------------------- | ---- |
| 1 | Hardware revision matches DNA `hardware_revision`                 | ☐    |
| 2 | All component manifests' `self_tests` pass (run via `test_mode`)  | ☐    |
| 3 | Pinmap loaded; no `conflicts[]` entries                           | ☐    |
| 4 | DNA fingerprint on device matches Master                          | ☐    |
| 5 | Brain config schema validates (`config_schema_version` matches)   | ☐    |
| 6 | Heater Control: trips to SAFE on injected over-temp event         | ☐    |
| 7 | Wi-Fi connect within 20 s of cold boot                            | ☐    |
| 8 | Heartbeat reaches Edge within 30 s of cold boot                   | ☐    |
| 9 | Disconnect Edge for 60 s — device keeps running, queue ≤ buffer   | ☐    |
| 10 | Reconnect Edge — queued telemetry drained within 30 s             | ☐    |

Sign-off:

| Name | Role | Date | Signature |
| ---- | ---- | ---- | --------- |
|      |      |      |           |
