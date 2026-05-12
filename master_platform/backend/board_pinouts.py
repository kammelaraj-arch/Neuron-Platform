"""Detailed pin labels for control boards.

Looked up by control board stable_id. Each board declares its physical
pinout so the wizard can render it visually next to the compute header
and the operator can see exactly which board pin connects to which
compute pin.

Pin entry: (pin_name, kind, description, suggested_compute_kind)
  kind: "power_5v" | "power_3v3" | "ground" | "input" | "output" |
         "bidir" | "pwm_in" | "i2c_sda" | "i2c_scl" | "spi_mosi" |
         "spi_miso" | "spi_sck" | "spi_cs" | "uart_tx" | "uart_rx" |
         "analog_in" | "motor_out" | "interrupt" | "address_select" |
         "reset" | "enable" | "vref" | "special"
  suggested_compute_kind: hint for the visual matcher — what kind of
    compute pin this should wire to. Keeps the connection inference
    accurate (an EN pin shouldn't be drawn to a 5V pin).
"""
from __future__ import annotations


BOARD_PINOUTS: dict[str, list[tuple[str, str, str, str | None]]] = {
    # ── L298N Dual H-Bridge Motor Driver ─────────────────────────────────
    "board.motor.l298n": [
        ("VCC",  "power_5v",   "+5V from on-board regulator (or external 5V)", "power_5v"),
        ("GND",  "ground",     "Common ground — tie to compute GND",            "ground"),
        ("+12V", "power_5v",   "Motor supply VIN, 6-35V (with 5V_EN jumper)",  None),
        ("ENA",  "pwm_in",     "Motor A enable / speed (PWM)",                  "pwm"),
        ("IN1",  "input",      "Motor A direction 1",                           "gpio"),
        ("IN2",  "input",      "Motor A direction 2",                           "gpio"),
        ("IN3",  "input",      "Motor B direction 1",                           "gpio"),
        ("IN4",  "input",      "Motor B direction 2",                           "gpio"),
        ("ENB",  "pwm_in",     "Motor B enable / speed (PWM)",                  "pwm"),
        ("OUT1", "motor_out",  "Motor A output 1 — connect to motor",           None),
        ("OUT2", "motor_out",  "Motor A output 2 — connect to motor",           None),
        ("OUT3", "motor_out",  "Motor B output 1 — connect to motor",           None),
        ("OUT4", "motor_out",  "Motor B output 2 — connect to motor",           None),
    ],
    # ── L293D Dual H-Bridge IC ───────────────────────────────────────────
    "board.motor.l293d": [
        ("VCC1", "power_5v",  "Logic supply (4.5-7V)",                          "power_5v"),
        ("VCC2", "power_5v",  "Motor supply (4.5-36V)",                         None),
        ("GND",  "ground",    "Common ground",                                  "ground"),
        ("EN1",  "pwm_in",    "Channel 1+2 enable (PWM for motor A speed)",     "pwm"),
        ("EN2",  "pwm_in",    "Channel 3+4 enable (PWM for motor B speed)",     "pwm"),
        ("1A",   "input",     "Motor A direction 1",                            "gpio"),
        ("2A",   "input",     "Motor A direction 2",                            "gpio"),
        ("3A",   "input",     "Motor B direction 1",                            "gpio"),
        ("4A",   "input",     "Motor B direction 2",                            "gpio"),
        ("1Y",   "motor_out", "Motor A output 1",                               None),
        ("2Y",   "motor_out", "Motor A output 2",                               None),
        ("3Y",   "motor_out", "Motor B output 1",                               None),
        ("4Y",   "motor_out", "Motor B output 2",                               None),
    ],
    # ── TB6612FNG ──────────────────────────────────────────────────────
    "board.motor.tb6612fng": [
        ("VM",     "power_5v",  "Motor supply (2.5-13.5V)",                      None),
        ("VCC",    "power_3v3", "Logic supply (2.7-5.5V)",                       "power_3v3"),
        ("GND",    "ground",    "Common ground",                                 "ground"),
        ("STBY",   "enable",    "Standby — must be HIGH to enable both channels","gpio"),
        ("AIN1",   "input",     "Motor A direction 1",                           "gpio"),
        ("AIN2",   "input",     "Motor A direction 2",                           "gpio"),
        ("BIN1",   "input",     "Motor B direction 1",                           "gpio"),
        ("BIN2",   "input",     "Motor B direction 2",                           "gpio"),
        ("PWMA",   "pwm_in",    "Motor A PWM speed input",                       "pwm"),
        ("PWMB",   "pwm_in",    "Motor B PWM speed input",                       "pwm"),
        ("AO1",    "motor_out", "Motor A output 1",                              None),
        ("AO2",    "motor_out", "Motor A output 2",                              None),
        ("BO1",    "motor_out", "Motor B output 1",                              None),
        ("BO2",    "motor_out", "Motor B output 2",                              None),
    ],
    # ── PCA9685 16-channel servo / PWM HAT ────────────────────────────
    "board.servo.pca9685": [
        ("VCC",  "power_3v3",  "Logic supply (3-5V) — separate from V+",        "power_3v3"),
        ("GND",  "ground",     "Common ground",                                 "ground"),
        ("SCL",  "i2c_scl",    "I2C clock",                                     "i2c_scl"),
        ("SDA",  "i2c_sda",    "I2C data",                                      "i2c_sda"),
        ("OE",   "enable",     "Output enable — pull LOW to enable outputs",    "gpio"),
        ("V+",   "power_5v",   "Servo / LED power (separate, typically 5-6V)",  None),
        ("CH0",  "pwm_in",     "PWM channel 0 — connect to servo signal",       None),
        ("CH1",  "pwm_in",     "PWM channel 1",                                 None),
        ("CH2",  "pwm_in",     "PWM channel 2",                                 None),
        ("CH3",  "pwm_in",     "PWM channel 3",                                 None),
        ("…",    "pwm_in",     "…channels 4-14…",                               None),
        ("CH15", "pwm_in",     "PWM channel 15",                                None),
    ],
    # ── A4988 Stepper Driver ──────────────────────────────────────────
    "board.stepper.a4988": [
        ("VMOT",   "power_5v", "Motor power (8-35V)",                            None),
        ("GND",    "ground",   "Motor ground",                                   "ground"),
        ("VDD",    "power_3v3","Logic power (3-5.5V)",                           "power_3v3"),
        ("GND",    "ground",   "Logic ground",                                   "ground"),
        ("STEP",   "pwm_in",   "Step pulse — each rising edge = one (micro)step","gpio"),
        ("DIR",    "input",    "Direction",                                      "gpio"),
        ("ENABLE", "enable",   "Active-low driver enable",                       "gpio"),
        ("MS1",    "address_select", "Microstep config bit 1",                   "gpio"),
        ("MS2",    "address_select", "Microstep config bit 2",                   "gpio"),
        ("MS3",    "address_select", "Microstep config bit 3",                   "gpio"),
        ("RESET",  "reset",    "Active-low reset (tie to SLEEP)",                "gpio"),
        ("SLEEP",  "enable",   "Active-low sleep (tie to RESET)",                "gpio"),
        ("1A",     "motor_out","Stepper coil A pin 1",                           None),
        ("1B",     "motor_out","Stepper coil A pin 2",                           None),
        ("2A",     "motor_out","Stepper coil B pin 1",                           None),
        ("2B",     "motor_out","Stepper coil B pin 2",                           None),
    ],
    # ── DRV8825 Stepper Driver ────────────────────────────────────────
    "board.stepper.drv8825": [
        ("VMOT",   "power_5v",  "Motor power (8.2-45V)",                          None),
        ("GND",    "ground",    "Motor ground",                                   "ground"),
        ("VCC",    "power_3v3", "Logic power (2.5-5.25V)",                        "power_3v3"),
        ("STEP",   "pwm_in",    "Step pulse",                                     "gpio"),
        ("DIR",    "input",     "Direction",                                      "gpio"),
        ("ENABLE", "enable",    "Active-low enable",                              "gpio"),
        ("M0",     "address_select", "Microstep bit 0",                           "gpio"),
        ("M1",     "address_select", "Microstep bit 1",                           "gpio"),
        ("M2",     "address_select", "Microstep bit 2",                           "gpio"),
        ("RESET",  "reset",     "Active-low reset",                               "gpio"),
        ("SLEEP",  "enable",    "Active-low sleep",                               "gpio"),
        ("FAULT",  "interrupt", "Fault output (active low)",                      "gpio"),
        ("A1",     "motor_out", "Coil A pin 1",                                   None),
        ("A2",     "motor_out", "Coil A pin 2",                                   None),
        ("B1",     "motor_out", "Coil B pin 1",                                   None),
        ("B2",     "motor_out", "Coil B pin 2",                                   None),
    ],
    # ── TMC2209 Silent Stepper ─────────────────────────────────────────
    "board.stepper.tmc2209": [
        ("VMOT",   "power_5v",  "Motor power (4.75-29V)",                         None),
        ("GND",    "ground",    "Motor ground",                                   "ground"),
        ("VIO",    "power_3v3", "Logic power (3-5V)",                             "power_3v3"),
        ("STEP",   "pwm_in",    "Step pulse",                                     "gpio"),
        ("DIR",    "input",     "Direction",                                      "gpio"),
        ("EN",     "enable",    "Active-low enable",                              "gpio"),
        ("MS1",    "address_select", "Microstep + UART address bit",              "gpio"),
        ("MS2",    "address_select", "Microstep + UART address bit",              "gpio"),
        ("PDN_UART","uart_tx",  "UART (single-wire) for advanced config",         "uart_tx"),
        ("DIAG",   "interrupt", "StallGuard fault output",                        "gpio"),
        ("A1",     "motor_out", "Coil A pin 1",                                   None),
        ("A2",     "motor_out", "Coil A pin 2",                                   None),
        ("B1",     "motor_out", "Coil B pin 1",                                   None),
        ("B2",     "motor_out", "Coil B pin 2",                                   None),
    ],
    # ── MCP3008 8-channel SPI ADC ──────────────────────────────────────
    "board.adc.mcp3008": [
        ("VDD",   "power_3v3", "Digital supply (2.7-5.5V)",                       "power_3v3"),
        ("VREF",  "vref",      "ADC voltage reference (tie to VDD for full-scale)","power_3v3"),
        ("AGND",  "ground",    "Analog ground",                                   "ground"),
        ("DGND",  "ground",    "Digital ground",                                  "ground"),
        ("CS",    "spi_cs",    "SPI chip-select",                                 "spi_cs"),
        ("DIN",   "spi_mosi",  "SPI MOSI",                                        "spi_mosi"),
        ("DOUT",  "spi_miso",  "SPI MISO",                                        "spi_miso"),
        ("CLK",   "spi_sck",   "SPI clock",                                       "spi_sck"),
        ("CH0",   "analog_in", "Analog input channel 0",                          None),
        ("CH1",   "analog_in", "Analog input channel 1",                          None),
        ("CH2",   "analog_in", "Analog input channel 2",                          None),
        ("CH3",   "analog_in", "Analog input channel 3",                          None),
        ("CH4",   "analog_in", "Analog input channel 4",                          None),
        ("CH5",   "analog_in", "Analog input channel 5",                          None),
        ("CH6",   "analog_in", "Analog input channel 6",                          None),
        ("CH7",   "analog_in", "Analog input channel 7",                          None),
    ],
    # ── ADS1115 16-bit I2C ADC ─────────────────────────────────────────
    "board.adc.ads1115": [
        ("VDD",   "power_3v3", "Supply (2-5.5V)",                                 "power_3v3"),
        ("GND",   "ground",    "Ground",                                          "ground"),
        ("SCL",   "i2c_scl",   "I2C clock",                                       "i2c_scl"),
        ("SDA",   "i2c_sda",   "I2C data",                                        "i2c_sda"),
        ("ADDR",  "address_select", "I2C address select (GND=0x48, VDD=0x49, SDA=0x4A, SCL=0x4B)", None),
        ("ALRT",  "interrupt", "Alert / conversion-ready output",                 "gpio"),
        ("A0",    "analog_in", "Single-ended analog input 0 (or diff A0-A1)",     None),
        ("A1",    "analog_in", "Single-ended analog input 1",                     None),
        ("A2",    "analog_in", "Single-ended analog input 2 (or diff A2-A3)",     None),
        ("A3",    "analog_in", "Single-ended analog input 3",                     None),
    ],
    # ── MCP23017 16-channel I2C GPIO expander ──────────────────────────
    "board.io.mcp23017": [
        ("VDD",   "power_3v3", "Supply (1.8-5.5V)",                               "power_3v3"),
        ("VSS",   "ground",    "Ground",                                          "ground"),
        ("SCL",   "i2c_scl",   "I2C clock",                                       "i2c_scl"),
        ("SDA",   "i2c_sda",   "I2C data",                                        "i2c_sda"),
        ("A0",    "address_select", "Address bit 0",                              "gpio"),
        ("A1",    "address_select", "Address bit 1",                              "gpio"),
        ("A2",    "address_select", "Address bit 2",                              "gpio"),
        ("RESET", "reset",     "Active-low reset (tie to VDD via pull-up)",       "gpio"),
        ("INTA",  "interrupt", "Port A interrupt output",                         "gpio"),
        ("INTB",  "interrupt", "Port B interrupt output",                         "gpio"),
        ("GPA0",  "bidir",     "Port A pin 0",                                    None),
        ("GPA1",  "bidir",     "Port A pin 1",                                    None),
        ("…",     "bidir",     "GPA2-GPA7, GPB0-GPB7",                            None),
        ("GPB7",  "bidir",     "Port B pin 7",                                    None),
    ],
    # ── PCF8574 8-channel I2C GPIO expander ────────────────────────────
    "board.io.pcf8574": [
        ("VDD",   "power_3v3", "Supply (2.5-6V)",                                 "power_3v3"),
        ("VSS",   "ground",    "Ground",                                          "ground"),
        ("SCL",   "i2c_scl",   "I2C clock",                                       "i2c_scl"),
        ("SDA",   "i2c_sda",   "I2C data",                                        "i2c_sda"),
        ("A0",    "address_select", "Address bit 0 (0x20-0x27 range)",            "gpio"),
        ("A1",    "address_select", "Address bit 1",                              "gpio"),
        ("A2",    "address_select", "Address bit 2",                              "gpio"),
        ("INT",   "interrupt", "Change-of-state interrupt output",                "gpio"),
        ("P0",    "bidir",     "Quasi-bidir port 0",                              None),
        ("P1",    "bidir",     "Quasi-bidir port 1",                              None),
        ("…",     "bidir",     "P2-P7",                                           None),
        ("P7",    "bidir",     "Quasi-bidir port 7",                              None),
    ],
    # ── TCA9548A I2C multiplexer ───────────────────────────────────────
    "board.io.tca9548a": [
        ("VIN",   "power_3v3", "Supply (1.65-5.5V)",                              "power_3v3"),
        ("GND",   "ground",    "Ground",                                          "ground"),
        ("SCL",   "i2c_scl",   "Master I2C clock (from compute)",                 "i2c_scl"),
        ("SDA",   "i2c_sda",   "Master I2C data (from compute)",                  "i2c_sda"),
        ("A0",    "address_select", "Address bit 0",                              "gpio"),
        ("A1",    "address_select", "Address bit 1",                              "gpio"),
        ("A2",    "address_select", "Address bit 2",                              "gpio"),
        ("RESET", "reset",     "Active-low reset",                                "gpio"),
        ("SC0", "i2c_scl",   "Downstream channel 0 SCL", None),
        ("SD0", "i2c_sda",   "Downstream channel 0 SDA", None),
        ("…",   "i2c_sda",   "channels 1-6…",                                     None),
        ("SC7", "i2c_scl",   "Downstream channel 7 SCL",                          None),
        ("SD7", "i2c_sda",   "Downstream channel 7 SDA",                          None),
    ],
    # ── Single-channel SSR-style relay banks ───────────────────────────
    "board.relay.8ch_5v": [
        ("VCC",  "power_5v",  "Coil supply (5V)",                                 "power_5v"),
        ("GND",  "ground",    "Ground",                                           "ground"),
        ("JD-VCC","power_5v", "Coil isolation supply (jumper for opto-isolation)",None),
        ("IN1",  "input",     "Channel 1 control (active-low)",                   "gpio"),
        ("IN2",  "input",     "Channel 2 control",                                "gpio"),
        ("IN3",  "input",     "Channel 3 control",                                "gpio"),
        ("IN4",  "input",     "Channel 4 control",                                "gpio"),
        ("IN5",  "input",     "Channel 5 control",                                "gpio"),
        ("IN6",  "input",     "Channel 6 control",                                "gpio"),
        ("IN7",  "input",     "Channel 7 control",                                "gpio"),
        ("IN8",  "input",     "Channel 8 control",                                "gpio"),
        ("K1-K8 NO/NC/COM", "motor_out", "Relay AC switching contacts (per channel)", None),
    ],
}


PINOUT_KIND_COLORS = {
    "power_5v":        "#dc2626",
    "power_3v3":       "#f97316",
    "ground":          "#000000",
    "input":           "#10b981",
    "output":          "#22d3ee",
    "bidir":           "#34d399",
    "pwm_in":          "#a855f7",
    "i2c_sda":         "#0ea5e9",
    "i2c_scl":         "#0284c7",
    "spi_mosi":        "#7c3aed",
    "spi_miso":        "#9333ea",
    "spi_sck":         "#6d28d9",
    "spi_cs":          "#4c1d95",
    "uart_tx":         "#f59e0b",
    "uart_rx":         "#d97706",
    "analog_in":       "#06b6d4",
    "motor_out":       "#ef4444",
    "interrupt":       "#facc15",
    "address_select":  "#64748b",
    "reset":           "#fb923c",
    "enable":          "#84cc16",
    "vref":            "#a78bfa",
    "special":         "#a855f7",
}


def pinout_for(board_stable_id: str | None) -> list | None:
    if not board_stable_id:
        return None
    return BOARD_PINOUTS.get(board_stable_id)


# ─── Pin-kind compatibility ────────────────────────────────────────────────
# Which board-pin kinds (right) are safe to wire to which compute-pin
# kinds (left). Prevents the operator from accidentally tying 5V to a
# signal input or routing motor output back to a GPIO.
PIN_COMPAT: dict[str, set[str]] = {
    "power_5v":  {"power_5v"},
    "power_3v3": {"power_3v3"},
    "ground":    {"ground"},
    "gpio":      {
        "input", "output", "bidir",
        "pwm_in", "enable", "reset",
        "address_select", "interrupt",
        "i2c_sda", "i2c_scl",
        "spi_mosi", "spi_miso", "spi_sck", "spi_cs",
        "uart_tx", "uart_rx",
        "special",
    },
    "adc":       {"analog_in", "vref"},
    "special":   {"reset", "enable", "vref", "special"},
    "reserved":  set(),
    "not_connected": set(),
}


def board_pin_kind(board_stable_id: str | None, board_pin: str | None) -> str | None:
    """Look up the kind of a named board pin. Returns None when the
    pinout isn't catalogued (in which case validation is skipped — we
    don't block on missing metadata)."""
    if not board_stable_id or not board_pin:
        return None
    po = BOARD_PINOUTS.get(board_stable_id)
    if not po:
        return None
    bp = board_pin.strip().lower()
    for name, kind, _desc, _hint in po:
        if name.lower() == bp:
            return kind
    return None


def connection_type(compute_kind: str | None, board_kind: str | None) -> str:
    """Human-friendly label for a connection — what BUS or function it
    represents. Used in the wizard table + tooltip on each wire."""
    if compute_kind == "power_5v" or board_kind == "power_5v":
        return "5V power"
    if compute_kind == "power_3v3" or board_kind == "power_3v3":
        return "3V3 power"
    if compute_kind == "ground" or board_kind == "ground":
        return "GND"
    if compute_kind == "adc" and board_kind == "analog_in":
        return "Analog"
    if board_kind in ("i2c_sda", "i2c_scl"):
        return "I²C"
    if board_kind in ("spi_mosi", "spi_miso", "spi_sck", "spi_cs"):
        return "SPI"
    if board_kind in ("uart_tx", "uart_rx"):
        return "UART"
    if board_kind == "pwm_in":
        return "PWM"
    if board_kind == "analog_in":
        return "Analog"
    if board_kind == "interrupt":
        return "GPIO (interrupt)"
    if board_kind == "enable":
        return "Enable line"
    if board_kind == "reset":
        return "Reset line"
    if board_kind == "vref":
        return "Reference voltage"
    if board_kind in ("input", "output", "bidir", "address_select", "special"):
        return "GPIO"
    if board_kind == "motor_out":
        return "Motor output (do not wire to GPIO)"
    return "Logic"


def is_compatible(compute_kind: str | None, board_kind: str | None) -> tuple[bool, str]:
    """Return (ok, reason). ok=True means the connection is allowed (or
    we don't have enough metadata to block it). reason is a human-
    readable explanation when ok=False."""
    if compute_kind is None or board_kind is None:
        # Don't have the metadata to make a judgement → allow with a
        # warning rather than block real-world wiring the catalog
        # doesn't know about yet.
        return True, ""
    if board_kind == "motor_out":
        return False, ("Refusing to wire a compute pin to a motor output — that's where the "
                       "actuator (motor/coil) connects, not the controller.")
    allowed = PIN_COMPAT.get(compute_kind, set())
    if board_kind in allowed:
        return True, ""
    return False, (f"Compute pin kind '{compute_kind}' cannot wire to a board pin of kind "
                   f"'{board_kind}'. Pick a compatible kind or override after confirming the schematic.")
