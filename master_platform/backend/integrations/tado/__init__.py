"""Tado integration package.

Legacy Tado (V3+) cloud control via the my.tado.com REST API. Auth is
the 2025 OAuth2 **device-code** flow (Tado retired the password grant
in early 2025), with a long-lived refresh token stored encrypted on
the VendorAccount.

Public surface:
    TadoClient        — auth + homes/zones/control
    TadoError         — raised on any API/auth failure

The discovery driver in ``vendor_discovery`` and the control router in
``routers/tado_ui`` both build a TadoClient from a VendorAccount row.
"""
from .client import TadoClient, TadoError

__all__ = ["TadoClient", "TadoError"]
