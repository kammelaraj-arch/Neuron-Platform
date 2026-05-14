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
        ("CH0",  "pwm_out",    "PWM channel 0 — wire to a servo / LED / motor signal", None),
        ("CH1",  "pwm_out",    "PWM channel 1",                                 None),
        ("CH2",  "pwm_out",    "PWM channel 2",                                 None),
        ("CH3",  "pwm_out",    "PWM channel 3",                                 None),
        ("CH4",  "pwm_out",    "PWM channel 4",                                 None),
        ("CH5",  "pwm_out",    "PWM channel 5",                                 None),
        ("CH6",  "pwm_out",    "PWM channel 6",                                 None),
        ("CH7",  "pwm_out",    "PWM channel 7",                                 None),
        ("CH8",  "pwm_out",    "PWM channel 8",                                 None),
        ("CH9",  "pwm_out",    "PWM channel 9",                                 None),
        ("CH10", "pwm_out",    "PWM channel 10",                                None),
        ("CH11", "pwm_out",    "PWM channel 11",                                None),
        ("CH12", "pwm_out",    "PWM channel 12",                                None),
        ("CH13", "pwm_out",    "PWM channel 13",                                None),
        ("CH14", "pwm_out",    "PWM channel 14",                                None),
        ("CH15", "pwm_out",    "PWM channel 15",                                None),
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

    # ─── CNC Shield V3 (Protoneer / clone, Arduino Uno form factor) ──────
    # The de-facto standard 3+1 axis GRBL stepper carrier — 4 socketed
    # driver slots (DRV8825 / A4988 / TMC22xx), all step/dir lines fixed
    # by GRBL convention.
    "board.cnc.cnc_shield_v3": [
        ("VCC",     "power_5v",  "5V logic supply (from Arduino / Pi via shield)", "power_5v"),
        ("GND",     "ground",    "Ground",                                          "ground"),
        ("VMOT",    "power_5v",  "Motor PSU positive (12-36V depending on driver)", None),
        ("VMOT-GND","ground",    "Motor PSU ground",                                "ground"),
        ("EN",      "enable",    "Driver enable (active LOW — all axes share)",     "gpio"),
        ("X-STEP",  "output",    "X-axis step pulse",                               "gpio"),
        ("X-DIR",   "output",    "X-axis direction",                                "gpio"),
        ("Y-STEP",  "output",    "Y-axis step pulse",                               "gpio"),
        ("Y-DIR",   "output",    "Y-axis direction",                                "gpio"),
        ("Z-STEP",  "output",    "Z-axis step pulse",                               "gpio"),
        ("Z-DIR",   "output",    "Z-axis direction",                                "gpio"),
        ("A-STEP",  "output",    "4th-axis step (jumper-cloned from X / Y / Z)",    "gpio"),
        ("A-DIR",   "output",    "4th-axis direction (jumper-cloned)",              "gpio"),
        ("X+",      "input",     "X+ endstop / limit switch (active LOW + pull-up)", "gpio"),
        ("Y+",      "input",     "Y+ endstop",                                      "gpio"),
        ("Z+",      "input",     "Z+ endstop",                                      "gpio"),
        ("SpnEN",   "output",    "Spindle enable / coolant",                        "gpio"),
        ("SpnDIR",  "output",    "Spindle direction",                               "gpio"),
        ("Abort",   "input",     "GRBL abort input",                                "gpio"),
        ("Hold",    "input",     "GRBL feed-hold input",                            "gpio"),
        ("Resume",  "input",     "GRBL cycle-start input",                          "gpio"),
        ("CoolEN",  "output",    "Coolant flood enable",                            "gpio"),
        ("RESET",   "reset",     "Shield-wide reset (active LOW)",                  "gpio"),
    ],

    # ─── CNC Shield V4 (Arduino Nano form factor, 3-axis) ────────────────
    "board.cnc.cnc_shield_v4": [
        ("VCC",     "power_5v",  "5V logic (from Nano)",                            "power_5v"),
        ("GND",     "ground",    "Ground",                                          "ground"),
        ("VMOT",    "power_5v",  "Motor PSU positive (12-36V)",                     None),
        ("EN",      "enable",    "All-axis driver enable (active LOW)",             "gpio"),
        ("X-STEP",  "output",    "X step",                                          "gpio"),
        ("X-DIR",   "output",    "X direction",                                     "gpio"),
        ("Y-STEP",  "output",    "Y step",                                          "gpio"),
        ("Y-DIR",   "output",    "Y direction",                                     "gpio"),
        ("Z-STEP",  "output",    "Z step",                                          "gpio"),
        ("Z-DIR",   "output",    "Z direction",                                     "gpio"),
        ("X+",      "input",     "X+ endstop",                                      "gpio"),
        ("Y+",      "input",     "Y+ endstop",                                      "gpio"),
        ("Z+",      "input",     "Z+ endstop",                                      "gpio"),
        ("SpnEN",   "output",    "Spindle enable",                                  "gpio"),
        ("SpnDIR",  "output",    "Spindle direction",                               "gpio"),
        ("CoolEN",  "output",    "Coolant enable",                                  "gpio"),
        ("Abort",   "input",     "GRBL abort",                                      "gpio"),
        ("Hold",    "input",     "GRBL hold",                                       "gpio"),
        ("Resume",  "input",     "GRBL resume",                                     "gpio"),
        ("RESET",   "reset",     "Shield reset",                                    "gpio"),
    ],

    # ─── RAMPS 1.4 (3D-printer mainboard, Marlin-targeted) ───────────────
    "board.cnc.ramps_1_4": [
        ("VCC",       "power_5v", "5V logic from MEGA",                              "power_5v"),
        ("GND",       "ground",   "Ground",                                          "ground"),
        ("PS-ON",     "output",   "Power-supply ON (active LOW)",                    "gpio"),
        ("X-STEP",    "output",   "X step (D54)",                                    "gpio"),
        ("X-DIR",     "output",   "X direction (D55)",                               "gpio"),
        ("X-EN",      "enable",   "X enable (D38, active LOW)",                      "gpio"),
        ("Y-STEP",    "output",   "Y step (D60)",                                    "gpio"),
        ("Y-DIR",     "output",   "Y direction (D61)",                               "gpio"),
        ("Y-EN",      "enable",   "Y enable (D56)",                                  "gpio"),
        ("Z-STEP",    "output",   "Z step (D46)",                                    "gpio"),
        ("Z-DIR",     "output",   "Z direction (D48)",                               "gpio"),
        ("Z-EN",      "enable",   "Z enable (D62)",                                  "gpio"),
        ("E0-STEP",   "output",   "Extruder 0 step (D26)",                           "gpio"),
        ("E0-DIR",    "output",   "Extruder 0 direction (D28)",                      "gpio"),
        ("E0-EN",     "enable",   "Extruder 0 enable (D24)",                         "gpio"),
        ("E1-STEP",   "output",   "Extruder 1 step (D36)",                           "gpio"),
        ("E1-DIR",    "output",   "Extruder 1 direction (D34)",                      "gpio"),
        ("E1-EN",     "enable",   "Extruder 1 enable (D30)",                         "gpio"),
        ("X-MIN",     "input",    "X-min endstop (D3)",                              "gpio"),
        ("X-MAX",     "input",    "X-max endstop (D2)",                              "gpio"),
        ("Y-MIN",     "input",    "Y-min endstop (D14)",                             "gpio"),
        ("Y-MAX",     "input",    "Y-max endstop (D15)",                             "gpio"),
        ("Z-MIN",     "input",    "Z-min endstop (D18)",                             "gpio"),
        ("Z-MAX",     "input",    "Z-max endstop (D19)",                             "gpio"),
        ("D8-MOSFET", "output",   "D8 — heated bed (high-current MOSFET)",           "gpio"),
        ("D9-MOSFET", "pwm_in",   "D9 — fan / part-cooling (PWM)",                   "pwm"),
        ("D10-MOSFET","pwm_in",   "D10 — hot-end heater (PWM)",                      "pwm"),
        ("T0",        "analog_in","Thermistor 0 (extruder, A13)",                    "adc"),
        ("T1",        "analog_in","Thermistor 1 (extruder 2, A14)",                  "adc"),
        ("T2",        "analog_in","Thermistor 2 (bed, A15)",                         "adc"),
        ("AUX-1 SDA", "i2c_sda",  "I²C SDA (AUX-1)",                                 "i2c_sda"),
        ("AUX-1 SCL", "i2c_scl",  "I²C SCL (AUX-1)",                                 "i2c_scl"),
        ("AUX-2 RX",  "uart_rx",  "Serial RX (AUX-2)",                               "uart"),
        ("AUX-2 TX",  "uart_tx",  "Serial TX (AUX-2)",                               "uart"),
    ],

    # ─── Adafruit Motor Shield V2 (4-DC + 2-stepper + 2-servo) ───────────
    # Talks to the host over I²C (PCA9685 inside), so logically just
    # two pins on the compute side — the rest is dispatched by address.
    "board.motor.adafruit_motor_shield_v2": [
        ("VCC",     "power_5v",  "Logic supply (5V from host)",                     "power_5v"),
        ("GND",     "ground",    "Ground",                                          "ground"),
        ("VIN",     "power_5v",  "Motor PSU (5-12V via terminal block or jumper)",  None),
        ("SDA",     "i2c_sda",   "I²C data — default address 0x60",                 "i2c_sda"),
        ("SCL",     "i2c_scl",   "I²C clock",                                       "i2c_scl"),
        ("M1",      "motor_out", "DC motor 1 output pair",                          None),
        ("M2",      "motor_out", "DC motor 2 output pair",                          None),
        ("M3",      "motor_out", "DC motor 3 output pair (or stepper 2 coil A)",    None),
        ("M4",      "motor_out", "DC motor 4 output pair (or stepper 2 coil B)",    None),
        ("SERVO-1", "pwm_in",    "Servo header 1 (pin 9)",                          "pwm"),
        ("SERVO-2", "pwm_in",    "Servo header 2 (pin 10)",                         "pwm"),
    ],

    # ─── L293D Motor Shield V1 (Arduino, the classic) ────────────────────
    "board.motor.l293d_shield_v1": [
        ("VCC",     "power_5v",  "Logic 5V",                                        "power_5v"),
        ("GND",     "ground",    "Ground",                                          "ground"),
        ("EXT-PWR", "power_5v",  "Motor PSU (jumper-selectable, up to 36V)",        None),
        ("M1",      "motor_out", "DC motor 1 (or stepper 1 coil A)",                None),
        ("M2",      "motor_out", "DC motor 2 (or stepper 1 coil B)",                None),
        ("M3",      "motor_out", "DC motor 3 (or stepper 2 coil A)",                None),
        ("M4",      "motor_out", "DC motor 4 (or stepper 2 coil B)",                None),
        ("SERVO-1", "pwm_in",    "Servo 1 (pin 10)",                                "pwm"),
        ("SERVO-2", "pwm_in",    "Servo 2 (pin 9)",                                 "pwm"),
    ],

    # ─── Pimoroni Inventor 2040 W (RP2040 mini-fleet) ────────────────────
    "board.hat.pimoroni_inventor_2040": [
        ("VBUS",   "power_5v",  "USB 5V in",                                        "power_5v"),
        ("VSYS",   "power_5v",  "Battery / barrel-jack 3-5.5V",                     None),
        ("GND",    "ground",    "Ground",                                           "ground"),
        ("MOTOR1A","motor_out", "Motor 1 +",                                        None),
        ("MOTOR1B","motor_out", "Motor 1 -",                                        None),
        ("MOTOR2A","motor_out", "Motor 2 +",                                        None),
        ("MOTOR2B","motor_out", "Motor 2 -",                                        None),
        ("ENC-1A", "input",     "Motor 1 encoder A",                                "gpio"),
        ("ENC-1B", "input",     "Motor 1 encoder B",                                "gpio"),
        ("ENC-2A", "input",     "Motor 2 encoder A",                                "gpio"),
        ("ENC-2B", "input",     "Motor 2 encoder B",                                "gpio"),
        ("SERVO-1","pwm_in",    "Servo 1 (GP2)",                                    "pwm"),
        ("SERVO-2","pwm_in",    "Servo 2 (GP3)",                                    "pwm"),
        ("SERVO-3","pwm_in",    "Servo 3 (GP4)",                                    "pwm"),
        ("SERVO-4","pwm_in",    "Servo 4 (GP5)",                                    "pwm"),
        ("RGB-DAT","output",    "Onboard RGB LED data line",                        "gpio"),
        ("USER-SW","input",     "User push-button (boot / interrupt)",              "gpio"),
        ("PIEZO",  "pwm_in",    "Piezo buzzer (GP9, PWM)",                          "pwm"),
        ("QW-SDA", "i2c_sda",   "Qwiic / STEMMA QT SDA",                            "i2c_sda"),
        ("QW-SCL", "i2c_scl",   "Qwiic / STEMMA QT SCL",                            "i2c_scl"),
    ],

    # ─── Waveshare PCA9685 + ADS1115 Servo/ADC HAT (Pi) ───────────────────
    "board.hat.waveshare_servo_hat": [
        ("VCC",   "power_3v3","Logic 3.3V from Pi header",                          "power_3v3"),
        ("V+",    "power_5v", "Servo PSU (4-6V external)",                          None),
        ("GND",   "ground",   "Ground (shared with Pi GND)",                        "ground"),
        ("SDA",   "i2c_sda",  "I²C data (Pi GPIO2)",                                "i2c_sda"),
        ("SCL",   "i2c_scl",  "I²C clock (Pi GPIO3)",                               "i2c_scl"),
        ("OE",    "enable",   "Output-enable (active LOW)",                         "gpio"),
        ("CH0",   "pwm_in",   "PWM/servo channel 0",                                "pwm"),
        ("CH1",   "pwm_in",   "PWM/servo channel 1",                                "pwm"),
        ("CH2",   "pwm_in",   "PWM/servo channel 2",                                "pwm"),
        ("CH3",   "pwm_in",   "PWM/servo channel 3",                                "pwm"),
        ("CH4",   "pwm_in",   "PWM/servo channel 4",                                "pwm"),
        ("CH5",   "pwm_in",   "PWM/servo channel 5",                                "pwm"),
        ("CH6",   "pwm_in",   "PWM/servo channel 6",                                "pwm"),
        ("CH7",   "pwm_in",   "PWM/servo channel 7",                                "pwm"),
        ("CH8",   "pwm_in",   "PWM/servo channel 8",                                "pwm"),
        ("CH9",   "pwm_in",   "PWM/servo channel 9",                                "pwm"),
        ("CH10",  "pwm_in",   "PWM/servo channel 10",                               "pwm"),
        ("CH11",  "pwm_in",   "PWM/servo channel 11",                               "pwm"),
        ("CH12",  "pwm_in",   "PWM/servo channel 12",                               "pwm"),
        ("CH13",  "pwm_in",   "PWM/servo channel 13",                               "pwm"),
        ("CH14",  "pwm_in",   "PWM/servo channel 14",                               "pwm"),
        ("CH15",  "pwm_in",   "PWM/servo channel 15",                               "pwm"),
        ("ADS-A0","analog_in","Onboard ADS1115 channel 0",                          "adc"),
        ("ADS-A1","analog_in","Onboard ADS1115 channel 1",                          "adc"),
        ("ADS-A2","analog_in","Onboard ADS1115 channel 2",                          "adc"),
        ("ADS-A3","analog_in","Onboard ADS1115 channel 3",                          "adc"),
    ],

    # ─── BigTreeTech SKR Mini E3 V3 (3D printer mainboard, STM32) ─────────
    "board.cnc.skr_mini_e3_v3": [
        ("VCC",      "power_5v","5V logic",                                         "power_5v"),
        ("GND",      "ground",  "Ground",                                           "ground"),
        ("VIN",      "power_5v","24V PSU input",                                    None),
        ("X-STEP",   "output",  "X step (PB13)",                                    "gpio"),
        ("X-DIR",    "output",  "X direction (PB12)",                               "gpio"),
        ("X-EN",     "enable",  "X enable (PB14, active LOW)",                      "gpio"),
        ("Y-STEP",   "output",  "Y step (PB10)",                                    "gpio"),
        ("Y-DIR",    "output",  "Y direction (PB2)",                                "gpio"),
        ("Y-EN",     "enable",  "Y enable (PB11)",                                  "gpio"),
        ("Z-STEP",   "output",  "Z step (PB0)",                                     "gpio"),
        ("Z-DIR",    "output",  "Z direction (PC5)",                                "gpio"),
        ("Z-EN",     "enable",  "Z enable (PB1)",                                   "gpio"),
        ("E0-STEP",  "output",  "Extruder 0 step (PB3)",                            "gpio"),
        ("E0-DIR",   "output",  "Extruder 0 direction (PB4)",                       "gpio"),
        ("E0-EN",    "enable",  "Extruder 0 enable (PD2)",                          "gpio"),
        ("X-STOP",   "input",   "X endstop (PC0)",                                  "gpio"),
        ("Y-STOP",   "input",   "Y endstop (PC1)",                                  "gpio"),
        ("Z-STOP",   "input",   "Z endstop / BLTouch (PC2)",                        "gpio"),
        ("PROBE",    "input",   "Z probe / BLTouch (PC14)",                         "gpio"),
        ("HE",       "pwm_in",  "Hot-end heater (PC8, PWM)",                        "pwm"),
        ("HB",       "pwm_in",  "Heated-bed (PC9, PWM)",                            "pwm"),
        ("FAN-0",    "pwm_in",  "Part-cooling fan 0 (PC6)",                         "pwm"),
        ("FAN-1",    "pwm_in",  "Part-cooling fan 1 (PC7)",                         "pwm"),
        ("TH-E",     "analog_in","Hot-end thermistor (PA0)",                        "adc"),
        ("TH-B",     "analog_in","Bed thermistor (PC4)",                            "adc"),
        ("EXP1-1..10","output", "EXP1 12864 LCD header",                            None),
        ("EXP2-1..10","output", "EXP2 12864 LCD header",                            None),
    ],
}


PINOUT_KIND_COLORS_OUT = {  # for the actuator-side rail (matches the legend)
    "pwm_out":      "#a855f7",
    "motor_out":    "#ef4444",
    "led_out":      "#22d3ee",
    "analog_out":   "#06b6d4",
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


ACTUATOR_SIDE_KINDS = {"pwm_out", "motor_out", "led_out", "analog_out"}


def pin_side(kind: str | None) -> str:
    """compute (wires to Pi side) vs actuator (wires to motor / sensor)."""
    if kind in ACTUATOR_SIDE_KINDS:
        return "actuator"
    return "compute"


def split_pinout(po: list) -> tuple[list, list]:
    """Split a board pinout into (compute_side_pins, actuator_side_pins)."""
    cs, ac = [], []
    for row in po or []:
        if pin_side(row[1]) == "actuator":
            ac.append(row)
        else:
            cs.append(row)
    return cs, ac


def pinout_for(board_stable_id: str | None) -> list | None:
    if not board_stable_id:
        return None
    return BOARD_PINOUTS.get(board_stable_id)


# Valid pin kinds for the operator-defined "custom pins" UI. Matches the
# kind keys in PINOUT_KIND_COLORS / PINOUT_KIND_COLORS_OUT so wires get
# the correct colour on the digital-twin canvas.
VALID_PIN_KINDS = tuple(PINOUT_KIND_COLORS.keys())


def merged_pinout(
    board_stable_id: str | None,
    custom: list | None,
) -> list | None:
    """Catalogue pinout (if any) merged with the BoardInstance's
    operator-defined custom pins. Custom entries with names that
    already exist in the catalogue override the catalogue one (so the
    operator can rename / re-kind a catalogue pin on a specific
    installation). Tuple shape preserved: (name, kind, description, hint)."""
    base = list(BOARD_PINOUTS.get(board_stable_id, []) if board_stable_id else [])
    if not custom:
        return base or None
    by_name = {row[0].lower(): i for i, row in enumerate(base)}
    for entry in custom:
        if not isinstance(entry, dict): continue
        name = (entry.get("name") or "").strip()
        if not name: continue
        kind = (entry.get("kind") or "special").strip().lower()
        desc = (entry.get("description") or "").strip() or None
        hint = (entry.get("hint") or "").strip() or None
        row = (name, kind, desc, hint)
        key = name.lower()
        if key in by_name:
            base[by_name[key]] = row
        else:
            base.append(row)
            by_name[key] = len(base) - 1
    return base or None


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
