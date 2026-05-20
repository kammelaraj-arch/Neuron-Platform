"""Device identity — DNA, group, hardware UUID, reset cause."""

try:
    import machine
except ImportError:
    machine = None


# machine.reset_cause() return values on RP2350. The actual numeric values
# are exposed as module-level constants in machine — we mirror them as a
# friendly map for telemetry / crash log enrichment.
_RESET_CAUSE_NAMES = {
    0: "PWRON_RESET",
    1: "HARD_RESET",
    2: "WDT_RESET",          # hardware watchdog
    3: "DEEPSLEEP_RESET",
    4: "SOFT_RESET",
    5: "BROWNOUT_RESET",     # power supply sag — non-trivial diagnosis
}


class Identity:
    def __init__(self, cfg):
        self.dna           = cfg["device_dna"]
        self.group_id      = cfg["group_id"]
        self.compute       = cfg.get("compute", "pico2w")
        self.base_version  = cfg.get("base_firmware_version", "1.0.0")
        self.brain_version = (cfg.get("brain") or {}).get("version", "1.0.0")
        self.app_version   = cfg.get("app_version", "1.0.0")

    @property
    def hardware_uuid(self) -> str:
        if machine is None:
            return ""
        try:
            return machine.unique_id().hex()
        except Exception:
            return ""

    @property
    def last_reset_cause(self) -> str:
        if machine is None:
            return "unknown"
        try:
            return _RESET_CAUSE_NAMES.get(machine.reset_cause(), "code_{}".format(machine.reset_cause()))
        except Exception:
            return "unknown"

    def as_dict(self) -> dict:
        return {
            "dna":            self.dna,
            "group_id":       self.group_id,
            "compute":        self.compute,
            "base_firmware":  self.base_version,
            "brain_version":  self.brain_version,
            "app_version":    self.app_version,
            "hardware_uuid":  self.hardware_uuid,
            "last_reset":     self.last_reset_cause,
        }
