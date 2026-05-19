"""TMC2209 / TMC2226 single-wire UART driver for MicroPython on Pico 2 W.

Drives up to 4 drivers on a shared half-duplex bus, addressed by MS1/MS2
hardware-set address bits. Exposes register read/write, current-set,
microstep-set, StealthChop on/off, StallGuard threshold, and crucially
`detect_chip()` which reads IOIN.VERSION to identify whether each driver
on the bus is a TMC2208 / TMC2209 / TMC2226.

Wire-format: 8-byte read or 8-byte write, CRC-8 over the first 7 bytes.
TMC datasheet rev 1.09 §5.1.
"""
from __future__ import annotations

import time
from machine import UART

# Register addresses (subset — see datasheet §5.5)
REG_GCONF       = 0x00
REG_GSTAT       = 0x01
REG_IFCNT       = 0x02
REG_SLAVECONF   = 0x03
REG_IOIN        = 0x06   # VERSION lives in bits 31..24
REG_IHOLD_IRUN  = 0x10
REG_TPOWERDOWN  = 0x11
REG_TSTEP       = 0x12
REG_TCOOLTHRS   = 0x14
REG_SGTHRS      = 0x40
REG_SG_RESULT   = 0x41
REG_CHOPCONF    = 0x6C
REG_DRV_STATUS  = 0x6F
REG_PWMCONF     = 0x70

# Known chip VERSION values (bits 31..24 of IOIN)
CHIP_VERSIONS = {
    0x10: "TMC2208",
    0x20: "TMC2208",   # silicon rev variants
    0x21: "TMC2209",
    0x22: "TMC2226",   # MKS / BIGTREETECH TMC2226
    0x30: "TMC2240",   # newer family member, for forward-compat
}


def _crc8(buf: bytes) -> int:
    """TMC's specific CRC-8 (poly 0x07, init 0x00) over n bytes."""
    crc = 0
    for b in buf:
        for _ in range(8):
            if (crc >> 7) ^ (b & 1):
                crc = ((crc << 1) ^ 0x07) & 0xFF
            else:
                crc = (crc << 1) & 0xFF
            b >>= 1
    return crc


class TMC:
    """One driver on a shared UART bus, addressed by `address` (0–3)."""

    def __init__(self, uart: UART, address: int):
        if not 0 <= address <= 3:
            raise ValueError("address must be 0..3 (set by MS1/MS2 hardware)")
        self.uart = uart
        self.address = address
        self.chip: str | None = None   # filled by detect_chip()
        self.version_byte: int | None = None

    # ── low-level register I/O ─────────────────────────────────────────
    def _write_reg(self, reg: int, value: int) -> None:
        pkt = bytearray(8)
        pkt[0] = 0x05
        pkt[1] = self.address
        pkt[2] = reg | 0x80
        pkt[3] = (value >> 24) & 0xFF
        pkt[4] = (value >> 16) & 0xFF
        pkt[5] = (value >>  8) & 0xFF
        pkt[6] =  value        & 0xFF
        pkt[7] = _crc8(pkt[:7])
        # Drain any stale RX bytes
        while self.uart.any():
            self.uart.read()
        self.uart.write(pkt)
        # In half-duplex the driver also sees its own write; consume the echo.
        time.sleep_ms(2)
        while self.uart.any():
            self.uart.read()

    def _read_reg(self, reg: int) -> int | None:
        pkt = bytearray(4)
        pkt[0] = 0x05
        pkt[1] = self.address
        pkt[2] = reg & 0x7F
        pkt[3] = _crc8(pkt[:3])
        while self.uart.any():
            self.uart.read()
        self.uart.write(pkt)
        # Half-duplex echo (4 bytes) + reply (8 bytes). Wait & gather.
        deadline = time.ticks_add(time.ticks_ms(), 50)
        buf = bytearray()
        while time.ticks_diff(deadline, time.ticks_ms()) > 0:
            if self.uart.any():
                buf.extend(self.uart.read())
            if len(buf) >= 12:
                break
            time.sleep_ms(1)
        if len(buf) < 12:
            return None
        reply = buf[-8:]
        if reply[0] != 0x05 or reply[1] != 0xFF or (reply[2] & 0x7F) != (reg & 0x7F):
            return None
        if _crc8(reply[:7]) != reply[7]:
            return None
        return (reply[3] << 24) | (reply[4] << 16) | (reply[5] << 8) | reply[6]

    # ── high-level helpers ──────────────────────────────────────────────
    def detect_chip(self) -> dict:
        """Identify which TMC chip variant is at this address.

        Returns {"address": int, "chip": str, "version_byte": int, "present": bool}.
        `chip` is one of CHIP_VERSIONS.values() or "unknown" or "absent".
        """
        ioin = self._read_reg(REG_IOIN)
        if ioin is None:
            return {"address": self.address, "chip": "absent",
                    "version_byte": None, "present": False}
        ver = (ioin >> 24) & 0xFF
        self.version_byte = ver
        self.chip = CHIP_VERSIONS.get(ver, f"unknown_0x{ver:02X}")
        return {"address": self.address, "chip": self.chip,
                "version_byte": ver, "present": True}

    def set_current_ma(self, run_ma: int, hold_ratio: float = 0.5,
                       rsense_ohm: float = 0.11) -> None:
        """Set run + hold current via IHOLD_IRUN. Default 0.11Ω sense
        resistor matches BIGTREETECH / FYSETC / MKS boards."""
        # CS = (I * sqrt(2) * 32 * Rsense / Vfs) - 1, Vfs = 0.325 V
        run_cs  = max(0, min(31, int((run_ma / 1000) * 1.41 * 32 * rsense_ohm / 0.325 - 1)))
        hold_cs = max(0, min(31, int(run_cs * hold_ratio)))
        val = (hold_cs & 0x1F) | ((run_cs & 0x1F) << 8) | (10 << 16)   # IHOLDDELAY=10
        self._write_reg(REG_IHOLD_IRUN, val)

    def set_microsteps(self, microsteps: int) -> None:
        """Set MRES in CHOPCONF. Valid: 256, 128, 64, 32, 16, 8, 4, 2, 0(=full)."""
        mres_map = {256: 0, 128: 1, 64: 2, 32: 3, 16: 4, 8: 5, 4: 6, 2: 7, 0: 8}
        if microsteps not in mres_map:
            raise ValueError(f"microsteps must be one of {sorted(mres_map)}")
        chopconf = self._read_reg(REG_CHOPCONF) or 0x10000053
        chopconf = (chopconf & ~(0xF << 24)) | (mres_map[microsteps] << 24)
        self._write_reg(REG_CHOPCONF, chopconf)

    def set_stealthchop(self, on: bool) -> None:
        """en_spreadCycle bit in GCONF (bit 2). 0=StealthChop, 1=SpreadCycle."""
        gconf = self._read_reg(REG_GCONF) or 0
        if on:
            gconf &= ~(1 << 2)   # clear → StealthChop
        else:
            gconf |=  (1 << 2)   # set → SpreadCycle (required for StallGuard)
        self._write_reg(REG_GCONF, gconf)

    def set_stallguard_threshold(self, sgthrs: int) -> None:
        """0..255. Higher = more sensitive. Per-axis tuning required."""
        self._write_reg(REG_SGTHRS, sgthrs & 0xFF)

    def set_tcoolthrs(self, value: int) -> None:
        """Enable StallGuard at all speeds when set to 0xFFFFF."""
        self._write_reg(REG_TCOOLTHRS, value & 0xFFFFF)

    def read_sg_result(self) -> int | None:
        v = self._read_reg(REG_SG_RESULT)
        return None if v is None else (v & 0x3FF)

    def read_drv_status(self) -> int | None:
        return self._read_reg(REG_DRV_STATUS)
