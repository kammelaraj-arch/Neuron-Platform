"""Device identity — DNA, group, build version, hardware UUID."""

try:
    import machine
except ImportError:
    machine = None


class Identity:
    def __init__(self, cfg):
        self.dna = cfg["device_dna"]
        self.group_id = cfg["group_id"]
        self.compute = cfg.get("compute", "pico2w")
        self.base_version = cfg.get("base_firmware_version", "1.0.0")
        self.brain_version = cfg.get("brain", {}).get("version", "1.0.0")

    @property
    def hardware_uuid(self) -> str:
        """RP2350 unique ID — 8 bytes hex. Falls back to empty on host."""
        if machine is None:
            return ""
        try:
            return machine.unique_id().hex()
        except Exception:
            return ""

    def as_dict(self) -> dict:
        return {
            "dna": self.dna,
            "group_id": self.group_id,
            "compute": self.compute,
            "base_firmware_version": self.base_version,
            "brain_version": self.brain_version,
            "hardware_uuid": self.hardware_uuid,
        }
