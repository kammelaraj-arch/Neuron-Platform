"""I2C bus + DS18B20-via-1-wire helper. The 1-wire driver lives here so the
brain only imports one module per bus.
"""
try:
    import onewire  # type: ignore
    import ds18x20  # type: ignore
    from machine import I2C, Pin  # type: ignore
except ImportError:
    onewire = ds18x20 = None  # type: ignore
    I2C = Pin = None  # type: ignore


def open_i2c(scl, sda, freq=400000):
    if I2C is None:
        return None
    return I2C(0, scl=Pin(scl), sda=Pin(sda), freq=freq)


def open_ds18b20(pin):
    if ds18x20 is None:
        return None
    bus = onewire.OneWire(Pin(pin))
    sensor = ds18x20.DS18X20(bus)
    roms = sensor.scan()
    return {"sensor": sensor, "roms": roms}


def read_ds18b20(handle):
    if handle is None or not handle["roms"]:
        return None
    handle["sensor"].convert_temp()
    import time
    time.sleep_ms(750)
    return handle["sensor"].read_temp(handle["roms"][0])
