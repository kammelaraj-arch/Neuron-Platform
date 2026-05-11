"""DNA loader. Reads dna.json (written by the Master firmware bundle) and
exposes its stable identity + version triplet to the rest of the firmware.
"""
import json


class DNA:
    def __init__(self, path="dna.json"):
        self._path = path
        self._doc = self._load()

    def _load(self):
        try:
            with open(self._path) as fh:
                return json.load(fh)
        except Exception:
            return {}

    @property
    def device_dna(self):
        return self._doc.get("device_dna") or self._doc.get("device_id") or "DNA-UNPROV"

    @property
    def device_type(self):
        return self._doc.get("device_type", "pico2w")

    @property
    def compute_stable_id(self):
        return self._doc.get("compute_stable_id", "compute.pico2w")

    @property
    def hardware_revision(self):
        return self._doc.get("hardware_revision", "unknown")

    @property
    def base_firmware_version(self):
        return self._doc.get("base_firmware_version", "0.0.0")

    @property
    def app_bundle_version(self):
        return self._doc.get("app_bundle_version", "0.0.0")

    @property
    def config_schema_version(self):
        return self._doc.get("config_schema_version", "0.0.0")

    @property
    def twin_controls(self):
        return list(self._doc.get("twin_controls", []))

    def fingerprint(self):
        fp = (self._doc.get("fingerprint") or {})
        return fp.get("hash", "")

    def to_dict(self):
        return dict(self._doc)


dna = DNA()
