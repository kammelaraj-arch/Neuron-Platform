"""ADC read helper. The Pico 2 W exposes 12-bit ADC on GP26..GP28 (reads as
16-bit on MicroPython). Returns volts on the 3.3 V rail.
"""
try:
    from machine import ADC  # type: ignore
except ImportError:
    ADC = None


def open_channel(pin):
    if ADC is None:
        return None
    return ADC(pin)


def read_voltage(adc, vref=3.3):
    if adc is None:
        return 0.0
    raw = adc.read_u16()
    return (raw / 65535.0) * vref
