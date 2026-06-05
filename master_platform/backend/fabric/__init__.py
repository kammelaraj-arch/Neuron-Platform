"""Fabric layer — unified device view + groups.

Sits on top of the existing typed device tables (Device, VendorDevice,
PlotterDevice, + future biometric / media) and presents them as ONE
list. Each adapter knows how to map its source rows into the common
FabricDevice shape; consumers (the /ui/fabric browser, the reusable
device picker, future composition surfaces) never care which integration
backed a given row.

Identity is **hardware-anchored where the underlying integration
already gives us one** (vendor_device_id is a Ring serial / Tado zone
id / Tapo deviceId hex — all hardware-derived), and DNA where the
operator went through the wizard. New synthetic UUIDs are only minted
for kinds with no inherent hardware anchor (the demo PlotterDevice
seed, for now).
"""
from .devices import (
    FabricDevice,
    list_devices,
    get_device,
    fabric_id_for_vendor_device,
    fabric_id_for_plotter_device,
    fabric_id_for_plotter_scene,
    fabric_id_for_native_device,
)

__all__ = [
    "FabricDevice",
    "list_devices",
    "get_device",
    "fabric_id_for_vendor_device",
    "fabric_id_for_plotter_device",
    "fabric_id_for_plotter_scene",
    "fabric_id_for_native_device",
]
