"""Physical pin-header layouts per compute module.

This is the visual-twin metadata for compute_*.json manifests — pin
positions on the PCB, function labels, alt-functions, color groups.
The wizard's pin-map step renders this as an interactive SVG header
so operators can see exactly which physical pin a logical GPIO maps
to (e.g. GPIO 4 = physical pin 7 on the Pi 40-pin header).

Convention:
  pins: list of (pin_number, primary_label, kind, alt_functions[])
  layout: "2x20_dual_inline" — Pi-style 40-pin header
  voltage: typical logic voltage (used for the legend)

Kinds drive the colour scheme in the UI:
  power_5v        | power_3v3 | ground
  gpio            | adc       | special
  reserved        | not_connected
"""
from __future__ import annotations


PI_40PIN_HEADER = [
    # Raspberry Pi 3 / 4 / 5 / Zero 2 W / Compute Module IO board — all
    # share the same 40-pin GPIO header layout (BCM numbering).
    (1,  "3V3",     "power_3v3", []),
    (2,  "5V",      "power_5v",  []),
    (3,  "GPIO 2",  "gpio",      ["SDA1", "I2C1"]),
    (4,  "5V",      "power_5v",  []),
    (5,  "GPIO 3",  "gpio",      ["SCL1", "I2C1"]),
    (6,  "GND",     "ground",    []),
    (7,  "GPIO 4",  "gpio",      ["GPCLK0", "1-Wire"]),
    (8,  "GPIO 14", "gpio",      ["TXD0", "UART0"]),
    (9,  "GND",     "ground",    []),
    (10, "GPIO 15", "gpio",      ["RXD0", "UART0"]),
    (11, "GPIO 17", "gpio",      []),
    (12, "GPIO 18", "gpio",      ["PCM_CLK", "PWM0"]),
    (13, "GPIO 27", "gpio",      []),
    (14, "GND",     "ground",    []),
    (15, "GPIO 22", "gpio",      []),
    (16, "GPIO 23", "gpio",      []),
    (17, "3V3",     "power_3v3", []),
    (18, "GPIO 24", "gpio",      []),
    (19, "GPIO 10", "gpio",      ["MOSI", "SPI0"]),
    (20, "GND",     "ground",    []),
    (21, "GPIO 9",  "gpio",      ["MISO", "SPI0"]),
    (22, "GPIO 25", "gpio",      []),
    (23, "GPIO 11", "gpio",      ["SCLK", "SPI0"]),
    (24, "GPIO 8",  "gpio",      ["CE0",  "SPI0"]),
    (25, "GND",     "ground",    []),
    (26, "GPIO 7",  "gpio",      ["CE1",  "SPI0"]),
    (27, "ID_SD",   "reserved",  ["HAT EEPROM"]),
    (28, "ID_SC",   "reserved",  ["HAT EEPROM"]),
    (29, "GPIO 5",  "gpio",      []),
    (30, "GND",     "ground",    []),
    (31, "GPIO 6",  "gpio",      []),
    (32, "GPIO 12", "gpio",      ["PWM0"]),
    (33, "GPIO 13", "gpio",      ["PWM1"]),
    (34, "GND",     "ground",    []),
    (35, "GPIO 19", "gpio",      ["PCM_FS", "PWM1"]),
    (36, "GPIO 16", "gpio",      []),
    (37, "GPIO 26", "gpio",      []),
    (38, "GPIO 20", "gpio",      ["PCM_DIN"]),
    (39, "GND",     "ground",    []),
    (40, "GPIO 21", "gpio",      ["PCM_DOUT"]),
]


# Raspberry Pi Pico / Pico W / Pico 2 / Pico 2 W — 40-pin two-row layout.
# Numbering matches the silkscreen: pins 1-20 down the left side,
# pins 21-40 down the right side (NOT alternating like Pi).
PICO_40PIN_LAYOUT = [
    # Left side (pins 1-20, top to bottom)
    (1,  "GP0",  "gpio", ["UART0_TX", "I2C0_SDA", "SPI0_RX"]),
    (2,  "GP1",  "gpio", ["UART0_RX", "I2C0_SCL", "SPI0_CSn"]),
    (3,  "GND",  "ground", []),
    (4,  "GP2",  "gpio", ["I2C1_SDA", "SPI0_SCK"]),
    (5,  "GP3",  "gpio", ["I2C1_SCL", "SPI0_TX"]),
    (6,  "GP4",  "gpio", ["UART1_TX", "I2C0_SDA"]),
    (7,  "GP5",  "gpio", ["UART1_RX", "I2C0_SCL"]),
    (8,  "GND",  "ground", []),
    (9,  "GP6",  "gpio", ["I2C1_SDA", "SPI0_SCK"]),
    (10, "GP7",  "gpio", ["I2C1_SCL", "SPI0_TX"]),
    (11, "GP8",  "gpio", ["UART1_TX", "I2C0_SDA", "SPI1_RX"]),
    (12, "GP9",  "gpio", ["UART1_RX", "I2C0_SCL", "SPI1_CSn"]),
    (13, "GND",  "ground", []),
    (14, "GP10", "gpio", ["I2C1_SDA", "SPI1_SCK"]),
    (15, "GP11", "gpio", ["I2C1_SCL", "SPI1_TX"]),
    (16, "GP12", "gpio", ["UART0_TX", "I2C0_SDA", "SPI1_RX"]),
    (17, "GP13", "gpio", ["UART0_RX", "I2C0_SCL", "SPI1_CSn"]),
    (18, "GND",  "ground", []),
    (19, "GP14", "gpio", ["I2C1_SDA", "SPI1_SCK"]),
    (20, "GP15", "gpio", ["I2C1_SCL", "SPI1_TX"]),
    # Right side (pins 21-40, top to bottom — matches silkscreen order)
    (40, "VBUS",     "power_5v",  ["USB +5V"]),
    (39, "VSYS",     "power_5v",  ["1.8-5.5V battery in"]),
    (38, "GND",      "ground",    []),
    (37, "3V3_EN",   "special",   ["regulator enable"]),
    (36, "3V3",      "power_3v3", ["3.3V out"]),
    (35, "ADC_VREF", "special",   ["ADC reference"]),
    (34, "GP28",     "adc",       ["ADC2"]),
    (33, "AGND",     "ground",    ["analog ground"]),
    (32, "GP27",     "adc",       ["ADC1", "I2C1_SCL"]),
    (31, "GP26",     "adc",       ["ADC0", "I2C1_SDA"]),
    (30, "RUN",      "special",   ["reset pin"]),
    (29, "GP22",     "gpio",      []),
    (28, "GND",      "ground",    []),
    (27, "GP21",     "gpio",      ["I2C0_SCL"]),
    (26, "GP20",     "gpio",      ["I2C0_SDA"]),
    (25, "GP19",     "gpio",      ["SPI0_TX", "I2C1_SCL"]),
    (24, "GP18",     "gpio",      ["SPI0_SCK", "I2C1_SDA"]),
    (23, "GND",      "ground",    []),
    (22, "GP17",     "gpio",      ["UART0_RX", "I2C0_SCL", "SPI0_CSn"]),
    (21, "GP16",     "gpio",      ["UART0_TX", "I2C0_SDA", "SPI0_RX"]),
]


# Lookup table: compute.stable_id → header layout descriptor.
# Keys match the actual stable_ids in libraries/micro_compute_library/manifests/.
COMPUTE_HEADERS: dict[str, dict] = {
    # Raspberry Pi single-board computers (Linux-class, 40-pin BCM header)
    "compute.rpi5": {
        "name": "Raspberry Pi 5",
        "layout": "2x20_alternating",
        "logic_voltage": 3.3,
        "pins": PI_40PIN_HEADER,
        "form_factor": "credit_card",
    },
    "compute.rpi4": {
        "name": "Raspberry Pi 4 Model B",
        "layout": "2x20_alternating",
        "logic_voltage": 3.3,
        "pins": PI_40PIN_HEADER,
        "form_factor": "credit_card",
    },
    "compute.rpi3": {
        "name": "Raspberry Pi 3 Model B / B+",
        "layout": "2x20_alternating",
        "logic_voltage": 3.3,
        "pins": PI_40PIN_HEADER,
        "form_factor": "credit_card",
    },
    "compute.rpi_zero_2w": {
        "name": "Raspberry Pi Zero 2 W",
        "layout": "2x20_alternating",
        "logic_voltage": 3.3,
        "pins": PI_40PIN_HEADER,
        "form_factor": "zero",
    },
    "compute.rpi_zero": {
        "name": "Raspberry Pi Zero / Zero W",
        "layout": "2x20_alternating",
        "logic_voltage": 3.3,
        "pins": PI_40PIN_HEADER,
        "form_factor": "zero",
    },
    "compute.rpi400": {
        "name": "Raspberry Pi 400",
        "layout": "2x20_alternating",
        "logic_voltage": 3.3,
        "pins": PI_40PIN_HEADER,
        "form_factor": "keyboard",
    },
    "compute.cm4": {
        "name": "Raspberry Pi Compute Module 4 (via IO Board)",
        "layout": "2x20_alternating",
        "logic_voltage": 3.3,
        "pins": PI_40PIN_HEADER,
        "form_factor": "module",
    },
    "compute.cm5": {
        "name": "Raspberry Pi Compute Module 5 (via IO Board)",
        "layout": "2x20_alternating",
        "logic_voltage": 3.3,
        "pins": PI_40PIN_HEADER,
        "form_factor": "module",
    },
    # Raspberry Pi Pico family (RP2040 / RP2350, 40-pin two-row silkscreen)
    "compute.pico2w": {
        "name": "Raspberry Pi Pico 2 W",
        "layout": "2x20_silkscreen",
        "logic_voltage": 3.3,
        "pins": PICO_40PIN_LAYOUT,
        "form_factor": "pico",
    },
    "compute.pico_2": {
        "name": "Raspberry Pi Pico 2",
        "layout": "2x20_silkscreen",
        "logic_voltage": 3.3,
        "pins": PICO_40PIN_LAYOUT,
        "form_factor": "pico",
    },
    "compute.pico_w": {
        "name": "Raspberry Pi Pico W",
        "layout": "2x20_silkscreen",
        "logic_voltage": 3.3,
        "pins": PICO_40PIN_LAYOUT,
        "form_factor": "pico",
    },
    "compute.pico": {
        "name": "Raspberry Pi Pico (original RP2040)",
        "layout": "2x20_silkscreen",
        "logic_voltage": 3.3,
        "pins": PICO_40PIN_LAYOUT,
        "form_factor": "pico",
    },
}


def header_for(compute_stable_id: str | None) -> dict | None:
    """Look up the header layout for a compute module. Returns None if
    unknown — callers should fall back to a generic 'unknown header'
    placeholder rather than crash."""
    if not compute_stable_id:
        return None
    return COMPUTE_HEADERS.get(compute_stable_id)


PIN_KIND_COLORS = {
    "power_5v":       "#dc2626",   # red
    "power_3v3":      "#f97316",   # orange
    "ground":         "#000000",   # black
    "gpio":           "#10b981",   # emerald
    "adc":            "#06b6d4",   # cyan
    "special":        "#a855f7",   # purple
    "reserved":       "#64748b",   # slate
    "not_connected":  "#475569",   # darker slate
}
