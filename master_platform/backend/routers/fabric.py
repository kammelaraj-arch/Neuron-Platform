"""Fabric / metadata-registry namespace.

A single read-only API surface that aggregates everything a client
needs to know about the platform — library catalog, system topology,
and twin schemas — without making N round-trips. Same auth + scope
model as the rest of /api; nothing is bypassed.

Design notes:
  * No hop is added for hot-path twin telemetry: that still flows
    through SSE on /api/devices/{dna}/twin/stream.
  * The fabric is intentionally read-only. Writes still go to the
    typed endpoints (/api/devices/..., /api/processes, etc.) so the
    audit + scope model is preserved.
  * Each twin schema includes the standard alarm + effects bindings
    from the digital_twin_controls_library, so a client widget can
    discover field types, units, sort hints, and UI bindings in one
    call.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_session
from ..library_loader import LIBRARY_DIRS, LIBRARY_GROUPS, load_catalog
from ..models import APIKey, Device, EdgeSystem, NodeSystem, RootSystem
from ..security.auth import require_scopes


router = APIRouter(prefix="/api/fabric", tags=["fabric"])


def _serialise(item) -> dict:
    return {
        "stable_id": item.stable_id,
        "name": item.name,
        "vendor": item.vendor,
        "version": item.version,
        "library": item.library,
        "category": item.category,
        "safety_class": item.safety_class,
        "source": item.source,
        "manifest": item.manifest,
    }


def _bindings_for_twin(catalog, twin_control_id: str) -> list[dict[str, Any]]:
    """Walk every UI widget manifest and pick out the ones whose
    compatibility.twin_controls includes this id."""
    out: list[dict[str, Any]] = []
    for ui in catalog.list_library("ui_controls_library"):
        if twin_control_id in (ui.manifest.get("compatibility", {}).get("twin_controls") or []):
            out.append({
                "widget_id": ui.stable_id,
                "name": ui.name,
                "category": ui.category,
                "ui_bindings": ui.manifest.get("ui_bindings") or [],
            })
    return out


def _components_compatible_with_twin(catalog, twin_control_id: str) -> list[dict[str, Any]]:
    """Hardware items that bind to this twin (so a UI can offer 'add a
    sensor compatible with this twin')."""
    out: list[dict[str, Any]] = []
    for lib in ("components_library", "control_board_library", "micro_compute_library"):
        for it in catalog.list_library(lib):
            tc = it.manifest.get("compatibility", {}).get("twin_controls") or []
            if twin_control_id in tc:
                out.append({
                    "stable_id": it.stable_id,
                    "name": it.name,
                    "library": it.library,
                    "category": it.category,
                })
    return out


@router.get("/catalog")
async def catalog(
    _: APIKey = Depends(require_scopes("library:read")),
) -> dict:
    """Every library item, grouped into the standard 6 sections."""
    cat = load_catalog()
    grouped: dict[str, list[dict]] = {}
    for group, libs in LIBRARY_GROUPS.items():
        items: list[dict] = []
        for lib in libs:
            items.extend(_serialise(it) for it in cat.list_library(lib))
        grouped[group] = items
    return {
        "groups": grouped,
        "totals": {g: len(v) for g, v in grouped.items()},
        "all_count": sum(len(v) for v in grouped.values()),
    }


@router.get("/topology")
async def topology(
    session: AsyncSession = Depends(get_session),
    _: APIKey = Depends(require_scopes("systems:read")),
) -> dict:
    """Root → Node → Edge → Device, with twin_controls and online
    status per device, in a single call."""
    roots = (await session.execute(select(RootSystem).order_by(RootSystem.created_at))).scalars().all()
    nodes = (await session.execute(select(NodeSystem).order_by(NodeSystem.created_at))).scalars().all()
    edges = (await session.execute(select(EdgeSystem).order_by(EdgeSystem.created_at))).scalars().all()
    devices = (await session.execute(select(Device).order_by(Device.created_at.desc()))).scalars().all()

    edges_by_node: dict[str, list[dict]] = {}
    for e in edges:
        edges_by_node.setdefault(e.node_id, []).append({
            "id": e.id, "name": e.name, "site_id": e.site_id, "address": e.address,
            "devices": [],
        })
    for d in devices:
        for ed in edges_by_node.get(d.edge_id, []):
            if False:  # placeholder; fast lookup below
                pass
    # Build device list per edge with one pass.
    edges_by_id = {e.id: e for e in edges}
    devices_by_edge: dict[str, list[dict]] = {}
    for d in devices:
        devices_by_edge.setdefault(d.edge_id, []).append({
            "device_dna": d.device_dna,
            "device_type": d.device_type,
            "compute_stable_id": d.compute_stable_id,
            "hardware_revision": d.hardware_revision,
            "base_firmware_version": d.base_firmware_version,
            "app_bundle_version": d.app_bundle_version,
            "config_schema_version": d.config_schema_version,
            "twin_controls": (d.dna_json or {}).get("twin_controls", []),
        })

    nodes_by_root: dict[str, list[dict]] = {}
    for n in nodes:
        nodes_by_root.setdefault(n.root_id, []).append({
            "id": n.id, "name": n.name, "region": n.region,
            "edges": [
                {**e, "devices": devices_by_edge.get(e["id"], [])}
                for e in edges_by_node.get(n.id, [])
            ],
        })

    return {
        "roots": [
            {"id": r.id, "name": r.name, "description": r.description,
             "nodes": nodes_by_root.get(r.id, [])}
            for r in roots
        ],
        "totals": {
            "roots": len(roots),
            "nodes": len(nodes),
            "edges": len(edges),
            "devices": len(devices),
        },
    }


@router.get("/twin-schema/{twin_control_id}")
async def twin_schema(
    twin_control_id: str,
    _: APIKey = Depends(require_scopes("library:read")),
) -> dict:
    """Per-twin schema: state machine, desired/reported/current fields
    with their declared types + units, plus every UI widget binding
    and every hardware component that targets this twin."""
    cat = load_catalog()
    item = cat.get(twin_control_id)
    if item is None:
        raise HTTPException(404, f"unknown twin control: {twin_control_id}")
    if item.library != "digital_twin_controls_library":
        raise HTTPException(400, f"{twin_control_id} is not a digital_twin_controls item")
    meta = item.manifest.get("metadata") or {}
    return {
        "twin_control": {
            "stable_id": item.stable_id,
            "name": item.name,
            "version": item.version,
            "vendor": item.vendor,
            "safety_class": item.safety_class,
            "category": item.category,
            "description": item.manifest.get("description"),
        },
        "state_machine": meta.get("state_machine"),
        "fields": {
            "desired": meta.get("desired") or {},
            "reported": meta.get("reported") or {},
            "current": meta.get("current") or {},
        },
        "ui_widgets": _bindings_for_twin(cat, twin_control_id),
        "hardware_components": _components_compatible_with_twin(cat, twin_control_id),
        "self_tests": item.manifest.get("self_tests") or [],
    }


@router.get("/manifest/{stable_id}")
async def manifest(
    stable_id: str,
    _: APIKey = Depends(require_scopes("library:read")),
) -> dict:
    """Single manifest fetch. Same item the catalog/library endpoints
    return, exposed at a memorable canonical URL."""
    item = load_catalog().get(stable_id)
    if item is None:
        raise HTTPException(404, f"unknown library item: {stable_id}")
    return _serialise(item)


@router.get("/fields")
async def fields(
    name: str,
    _: APIKey = Depends(require_scopes("library:read")),
) -> dict:
    """Discover where a named field appears across every twin schema.
    Useful for 'where does process_temp_c surface?' lookups during UI
    development.
    """
    cat = load_catalog()
    hits: list[dict] = []
    for it in cat.list_library("digital_twin_controls_library"):
        meta = it.manifest.get("metadata") or {}
        for ch in ("desired", "reported", "current"):
            if name in (meta.get(ch) or {}):
                hits.append({
                    "twin_control": it.stable_id,
                    "channel": ch,
                    "field": name,
                    "spec": meta[ch][name],
                })
    return {"field": name, "occurrences": hits}


@router.get("/widgets")
async def widgets(
    twin_control: str | None = None,
    _: APIKey = Depends(require_scopes("library:read")),
) -> dict:
    """Every UI widget with its bindings + metadata. Optional
    ``twin_control`` filter returns only widgets that bind to that twin
    id — useful for clients that want 'what should I render given a
    twin?' without hard-coding the widget map.
    """
    cat = load_catalog()
    out: list[dict[str, Any]] = []
    for w in cat.list_library("ui_controls_library"):
        compat = (w.manifest.get("compatibility") or {}).get("twin_controls") or []
        if twin_control and twin_control not in compat:
            continue
        out.append({
            "stable_id": w.stable_id,
            "name": w.name,
            "category": w.category,
            "version": w.version,
            "image": w.manifest.get("image"),
            "ui_bindings": w.manifest.get("ui_bindings") or [],
            "compatible_twin_controls": compat,
            "capabilities": w.manifest.get("capabilities") or [],
            "metadata": w.manifest.get("metadata") or {},
        })
    return {"twin_control_filter": twin_control, "widgets": out, "count": len(out)}


@router.get("/integrations")
async def integrations(
    _: APIKey = Depends(require_scopes("library:read")),
) -> dict:
    """Every api_library item with its drivers, preferred_path, and
    fallback_only flag. Drives a 'pick an integration' UI that respects
    the local-first / cloud-fallback principle (Tapo local LAN first,
    Tapo cloud only as fallback)."""
    cat = load_catalog()
    out: list[dict[str, Any]] = []
    for it in cat.list_library("api_library"):
        meta = it.manifest.get("metadata") or {}
        out.append({
            "stable_id": it.stable_id,
            "name": it.name,
            "vendor": it.vendor,
            "version": it.version,
            "category": it.category,
            "preferred_path": meta.get("preferred_path", "local"),
            "fallback_only": bool(meta.get("fallback_only", False)),
            "drivers": it.manifest.get("drivers") or [],
            "config_fields": meta.get("config_fields") or {},
            "compatibility": it.manifest.get("compatibility") or {},
        })
    # Local-first first, fallback-only last; alphabetical inside each group.
    out.sort(key=lambda x: (x["fallback_only"], x["stable_id"]))
    return {"integrations": out, "count": len(out)}
