# Pico 2 W — Flashing

The Master Platform issues a firmware bundle (`<DNA>.zip`) containing
`dna.json`, `brain.json`, `pinmap.json` and the bundle manifest. The
provisioning flow is:

## One-time per device (factory bench)

1. Hold BOOTSEL on the Pico 2 W and connect USB. The device mounts as
   `RPI-RP2` mass storage.
2. Drop the latest [MicroPython firmware for Pico 2 W](https://micropython.org/download/RPI_PICO2_W/)
   onto the drive. The device reboots with a REPL on `/dev/ttyACM0`.
3. With `mpremote` (recommended) or Thonny, copy the firmware tree:

   ```bash
   pip install mpremote
   cd neuron-platform/level0-pico2w
   mpremote connect /dev/ttyACM0 cp -r . :
   ```

4. Pull the device-specific bundle from the Master and unpack it onto
   the device:

   ```bash
   curl -H "X-API-Key: $NEURON_KEY" \
     https://neuron.shital.org.uk/api/devices/$DNA/firmware-bundle \
     -o /tmp/bundle.zip
   unzip /tmp/bundle.zip -d /tmp/bundle
   mpremote cp /tmp/bundle/dna.json :dna.json
   mpremote cp /tmp/bundle/brain.json :brain.json
   mpremote cp /tmp/bundle/pinmap.json :pinmap.json
   ```

5. Set Wi-Fi + Edge URL in `config.json`:

   ```bash
   mpremote exec "import json; cfg=json.load(open('config.json')); cfg['wifi']['ssid']='FACTORY_LAN'; cfg['wifi']['password']='secret'; cfg['edge']['url']='http://192.168.10.10:8090'; json.dump(cfg, open('config.json','w'))"
   ```

6. Power-cycle. The device should appear online in
   `https://neuron.shital.org.uk/ui/devices` within 30 s.

## Verification

```bash
mpremote exec "import json; print(json.load(open('dna.json'))['fingerprint']['hash'][:16])"
```

The first 16 chars of the SHA-256 fingerprint should match what the
Master shows for this DNA on `/ui/devices/{dna}`.
