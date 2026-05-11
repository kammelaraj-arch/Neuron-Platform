from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from fastapi import Form
from fastapi.responses import RedirectResponse
from sqlalchemy import desc

from ..db import get_session
from ..library_loader import load_catalog
from ..models import APIKey, Device, EdgeSystem, FirmwareChannel, FirmwareRelease, NodeSystem, RecipeDef, RecipeRun, RootSystem
from ..runtime import recipe_runner
from ..security import signing
from ..security.audit import record
from ..security.ui_auth import ui_require_admin, ui_require_login


_BASE = Path(__file__).resolve().parent.parent
templates = Jinja2Templates(directory=str(_BASE / "templates"))

router = APIRouter(tags=["ui"])


@router.get("/", response_class=HTMLResponse)
async def ui_index(
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    from sqlalchemy import func
    catalog = load_catalog()
    counts = {k: len(v) for k, v in catalog.by_library.items()}

    # Quick-start progress: does each tier exist?
    n_roots = await session.scalar(select(func.count()).select_from(RootSystem)) or 0
    n_nodes = await session.scalar(select(func.count()).select_from(NodeSystem)) or 0
    n_edges = await session.scalar(select(func.count()).select_from(EdgeSystem)) or 0
    devices = (await session.execute(select(Device).order_by(Device.created_at.desc()))).scalars().all()
    n_devices = len(devices)
    n_devices_with_dna = sum(1 for d in devices if d.dna_json)
    n_devices_with_firmware = sum(1 for d in devices if d.firmware_bundle_path)
    first_unbuilt_dna = next((d.device_dna for d in devices if not d.firmware_bundle_path), None)

    signed_in = bool(request.session.get("neuron_api_key_id"))

    steps = [
        {"key": "signin",   "label": "Sign in with admin API key", "done": signed_in,
         "href": "/login" if not signed_in else None,
         "hint": "Bootstrap key was printed once after the first deploy."},
        {"key": "root",     "label": "Create a Root system",       "done": n_roots > 0,
         "href": "/ui/systems",
         "hint": "Top of the hierarchy — your organisation."},
        {"key": "node",     "label": "Create a Node",              "done": n_nodes > 0,
         "href": "/ui/systems",
         "hint": "Optional regional grouping (e.g. UK)."},
        {"key": "edge",     "label": "Create an Edge",             "done": n_edges > 0,
         "href": "/ui/systems",
         "hint": "A factory site. Set its URL so Master can push commands."},
        {"key": "device",   "label": "Register a Device",          "done": n_devices > 0,
         "href": "/ui/devices",
         "hint": "Pick compute (Pico 2 W) + components from the catalog."},
        {"key": "build",    "label": "Run pin-map → DNA → Brain → firmware",
         "done": n_devices_with_firmware > 0,
         "href": f"/ui/devices/{first_unbuilt_dna}" if first_unbuilt_dna else "/ui/devices",
         "hint": "Four buttons on the device page do this in order."},
        {"key": "flash",    "label": "Flash a Pico 2 W",            "done": False,
         "href": "https://github.com/kammelaraj-arch/ShitalEco/blob/claude/shital-erp-platform-iR2UF/neuron-platform/level0-pico2w/provisioning/FLASHING.md",
         "external": True,
         "hint": "Bench step — drop the firmware bundle onto a real device."},
    ]
    completed = sum(1 for s in steps if s["done"])
    return templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "library_counts": counts,
            "device_count": n_devices,
            "n_roots": n_roots,
            "n_nodes": n_nodes,
            "n_edges": n_edges,
            "n_devices_with_dna": n_devices_with_dna,
            "n_devices_with_firmware": n_devices_with_firmware,
            "steps": steps,
            "completed": completed,
            "signed_in": signed_in,
            "all_done": completed >= len(steps),
        },
    )


@router.get("/ui/library", response_class=HTMLResponse)
async def ui_library(request: Request):
    catalog = load_catalog()
    grouped = {
        lib: catalog.list_library(lib)
        for lib in (
            "components_library",
            "control_board_library",
            "micro_compute_library",
            "digital_twin_controls_library",
            "ui_controls_library",
            "business_library",
            "functional_library",
            "api_library",
        )
    }
    return templates.TemplateResponse(
        "library.html", {"request": request, "grouped": grouped}
    )


@router.get("/ui/systems", response_class=HTMLResponse)
async def ui_systems(
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    roots = (await session.execute(select(RootSystem).order_by(RootSystem.created_at))).scalars().all()
    nodes = (await session.execute(select(NodeSystem).order_by(NodeSystem.created_at))).scalars().all()
    edges = (await session.execute(select(EdgeSystem).order_by(EdgeSystem.created_at))).scalars().all()
    flash = request.session.pop("systems_flash", None)
    return templates.TemplateResponse(
        "systems.html",
        {"request": request, "roots": roots, "nodes": nodes, "edges": edges,
         "signed_in": bool(request.session.get("neuron_api_key_id")),
         "flash": flash},
    )


@router.post("/ui/systems/root/new")
async def ui_systems_new_root(
    request: Request,
    name: str = Form(...),
    description: str = Form(""),
    session: AsyncSession = Depends(get_session),
    actor: APIKey = Depends(ui_require_admin),
):
    existing = await session.scalar(select(RootSystem).where(RootSystem.name == name))
    if existing:
        request.session["systems_flash"] = {"kind": "amber", "msg": f"Root '{name}' already exists."}
    else:
        row = RootSystem(name=name.strip(), description=description.strip() or None)
        session.add(row); await session.flush()
        await record(session, actor=actor.id, actor_kind="ui_session",
                     action="create_root", target_kind="root_system", target_id=row.id)
        await session.commit()
        request.session["systems_flash"] = {"kind": "emerald", "msg": f"Created root '{name}'."}
    return RedirectResponse("/ui/systems", status_code=303)


@router.post("/ui/systems/node/new")
async def ui_systems_new_node(
    request: Request,
    root_id: str = Form(...),
    name: str = Form(...),
    region: str = Form(""),
    session: AsyncSession = Depends(get_session),
    actor: APIKey = Depends(ui_require_admin),
):
    if await session.get(RootSystem, root_id) is None:
        request.session["systems_flash"] = {"kind": "red", "msg": "Selected root does not exist."}
        return RedirectResponse("/ui/systems", status_code=303)
    row = NodeSystem(root_id=root_id, name=name.strip(), region=region.strip() or None)
    session.add(row); await session.flush()
    await record(session, actor=actor.id, actor_kind="ui_session",
                 action="create_node", target_kind="node_system", target_id=row.id,
                 detail={"root_id": root_id})
    await session.commit()
    request.session["systems_flash"] = {"kind": "emerald", "msg": f"Created node '{name}'."}
    return RedirectResponse("/ui/systems", status_code=303)


@router.post("/ui/systems/edge/new")
async def ui_systems_new_edge(
    request: Request,
    node_id: str = Form(...),
    name: str = Form(...),
    site_id: str = Form(...),
    address: str = Form(""),
    session: AsyncSession = Depends(get_session),
    actor: APIKey = Depends(ui_require_admin),
):
    if await session.get(NodeSystem, node_id) is None:
        request.session["systems_flash"] = {"kind": "red", "msg": "Selected node does not exist."}
        return RedirectResponse("/ui/systems", status_code=303)
    row = EdgeSystem(node_id=node_id, name=name.strip(), site_id=site_id.strip(),
                     address=address.strip() or None)
    session.add(row); await session.flush()
    await record(session, actor=actor.id, actor_kind="ui_session",
                 action="create_edge", target_kind="edge_system", target_id=row.id,
                 detail={"node_id": node_id, "site_id": site_id, "address": address or None})
    await session.commit()
    request.session["systems_flash"] = {"kind": "emerald", "msg": f"Created edge '{name}' at site '{site_id}'."}
    return RedirectResponse("/ui/systems", status_code=303)


@router.get("/ui/live", response_class=HTMLResponse)
async def ui_live_wall(
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    """Wall-display: every device's rich widget tile streaming live via SSE."""
    devices = (await session.execute(select(Device).order_by(Device.created_at.desc()))).scalars().all()
    return templates.TemplateResponse(
        "live_wall.html",
        {"request": request, "devices": devices,
         "signed_in": bool(request.session.get("neuron_api_key_id"))},
    )


@router.get("/ui/devices", response_class=HTMLResponse)
async def ui_devices(
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    devices = (await session.execute(select(Device).order_by(Device.created_at.desc()))).scalars().all()
    edges = (await session.execute(select(EdgeSystem).order_by(EdgeSystem.name))).scalars().all()
    catalog = load_catalog()
    computes = catalog.list_library("micro_compute_library")
    components = catalog.list_library("components_library")
    flash = request.session.pop("devices_flash", None)
    return templates.TemplateResponse(
        "devices.html",
        {"request": request, "devices": devices, "edges": edges,
         "computes": computes, "components": components,
         "signed_in": bool(request.session.get("neuron_api_key_id")),
         "flash": flash},
    )


@router.post("/ui/devices/new")
async def ui_devices_new(
    request: Request,
    edge_id: str = Form(...),
    compute_stable_id: str = Form(...),
    component_ids: list[str] = Form(default=[]),
    hardware_revision: str = Form("rev_a"),
    base_firmware_version: str = Form("1.0.0"),
    app_bundle_version: str = Form("1.0.0"),
    config_schema_version: str = Form("1.0.0"),
    auto_build: str = Form(""),
    session: AsyncSession = Depends(get_session),
    actor: APIKey = Depends(ui_require_admin),
):
    from ..dna import new_dna_id
    if await session.get(EdgeSystem, edge_id) is None:
        request.session["devices_flash"] = {"kind": "red", "msg": "Selected edge does not exist."}
        return RedirectResponse("/ui/devices", status_code=303)
    catalog = load_catalog()
    if catalog.get(compute_stable_id) is None:
        request.session["devices_flash"] = {"kind": "red", "msg": f"Unknown compute: {compute_stable_id}"}
        return RedirectResponse("/ui/devices", status_code=303)
    components_json = []
    for cid in component_ids:
        if not cid: continue
        if catalog.get(cid) is None: continue
        components_json.append({
            "component_stable_id": cid,
            "instance_id": cid.split(".")[-1],
        })
    device = Device(
        device_dna=new_dna_id(),
        edge_id=edge_id,
        device_type="pico2w",
        compute_stable_id=compute_stable_id,
        hardware_revision=hardware_revision.strip() or "rev_a",
        base_firmware_version=base_firmware_version.strip() or "1.0.0",
        app_bundle_version=app_bundle_version.strip() or "1.0.0",
        config_schema_version=config_schema_version.strip() or "1.0.0",
        components_json=components_json,
    )
    session.add(device); await session.flush()
    await record(session, actor=actor.id, actor_kind="ui_session",
                 action="register_device", target_kind="device", target_id=device.device_dna,
                 detail={"edge_id": edge_id, "compute": compute_stable_id,
                         "n_components": len(components_json)})
    await session.commit()
    request.session["devices_flash"] = {
        "kind": "emerald",
        "msg": f"Registered {device.device_dna} — go to its page to auto-pin/DNA/Brain/firmware.",
    }
    if auto_build:
        return RedirectResponse(f"/ui/devices/{device.device_dna}", status_code=303)
    return RedirectResponse("/ui/devices", status_code=303)


@router.get("/ui/devices/{device_dna}", response_class=HTMLResponse)
async def ui_device_detail(
    device_dna: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    device = await session.get(Device, device_dna)
    return templates.TemplateResponse(
        "device.html",
        {"request": request, "device": device,
         "signed_in": bool(request.session.get("neuron_api_key_id"))},
    )


@router.post("/ui/devices/{device_dna}/pinmap-auto")
async def ui_device_pinmap_auto(
    device_dna: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
    actor: APIKey = Depends(ui_require_admin),
):
    from ..pin_allocator import auto_allocate, PinAllocationError
    device = await session.get(Device, device_dna)
    if device is None:
        return RedirectResponse("/ui/devices", status_code=303)
    catalog = load_catalog()
    components = [{"component_stable_id": c["component_stable_id"],
                   "instance_id": c.get("instance_id")} for c in (device.components_json or [])]
    try:
        pinmap = auto_allocate(
            compute_stable_id=device.compute_stable_id,
            component_assignments=components,
            catalog=catalog,
            device_dna=device.device_dna,
            board_stable_id=device.board_stable_id,
        )
    except PinAllocationError as exc:
        request.session["devices_flash"] = {"kind": "red", "msg": f"Pin allocation failed: {exc}"}
        return RedirectResponse(f"/ui/devices/{device_dna}", status_code=303)
    device.pinmap_json = pinmap
    await record(session, actor=actor.id, actor_kind="ui_session",
                 action="pinmap_auto", target_kind="device", target_id=device_dna,
                 detail={"conflicts": pinmap["conflicts"]})
    await session.commit()
    return RedirectResponse(f"/ui/devices/{device_dna}", status_code=303)


@router.post("/ui/devices/{device_dna}/generate-dna")
async def ui_device_generate_dna(
    device_dna: str,
    session: AsyncSession = Depends(get_session),
    actor: APIKey = Depends(ui_require_admin),
):
    from ..dna import build_dna
    device = await session.get(Device, device_dna)
    if device is None:
        return RedirectResponse("/ui/devices", status_code=303)
    edge = await session.get(EdgeSystem, device.edge_id)
    node = await session.get(NodeSystem, edge.node_id) if edge else None
    catalog = load_catalog()
    components = [{
        "stable_id": c["component_stable_id"],
        "instance_id": c.get("instance_id") or c["component_stable_id"].split(".")[-1],
        "version": (catalog.get(c["component_stable_id"]).version
                    if catalog.get(c["component_stable_id"]) else "0.0.0"),
        "role": c.get("role") or "component",
    } for c in (device.components_json or [])]
    twin_controls = sorted({
        tc for c in components
        for tc in ((catalog.get(c["stable_id"]).manifest.get("compatibility", {}) or {}).get("twin_controls", [])
                   if catalog.get(c["stable_id"]) else [])
    })
    dna = build_dna(
        device_dna=device.device_dna, device_type=device.device_type,
        compute_stable_id=device.compute_stable_id, board_stable_id=device.board_stable_id,
        hardware_revision=device.hardware_revision, serial_number=device.serial_number,
        mac_address=device.mac_address,
        base_firmware_version=device.base_firmware_version,
        app_bundle_version=device.app_bundle_version,
        config_schema_version=device.config_schema_version,
        parent_node_id=node.id if node else "unknown",
        site_id=edge.site_id if edge else "unknown",
        issued_by=actor.id, components=components, twin_controls=twin_controls,
    )
    device.dna_json = dna
    await record(session, actor=actor.id, actor_kind="ui_session",
                 action="generate_dna", target_kind="device", target_id=device_dna)
    await session.commit()
    return RedirectResponse(f"/ui/devices/{device_dna}", status_code=303)


@router.post("/ui/devices/{device_dna}/generate-brain")
async def ui_device_generate_brain(
    device_dna: str,
    session: AsyncSession = Depends(get_session),
    actor: APIKey = Depends(ui_require_admin),
):
    from ..brain import build_brain
    device = await session.get(Device, device_dna)
    if device is None or not device.dna_json:
        return RedirectResponse(f"/ui/devices/{device_dna}", status_code=303)
    catalog = load_catalog()
    brain = build_brain(
        device_dna=device.device_dna,
        config_schema_version=device.config_schema_version,
        twin_controls=device.dna_json.get("twin_controls", []),
        catalog=catalog,
    )
    device.brain_json = brain
    await record(session, actor=actor.id, actor_kind="ui_session",
                 action="generate_brain", target_kind="device", target_id=device_dna)
    await session.commit()
    return RedirectResponse(f"/ui/devices/{device_dna}", status_code=303)


@router.post("/ui/devices/{device_dna}/build-firmware")
async def ui_device_build_firmware(
    device_dna: str,
    session: AsyncSession = Depends(get_session),
    actor: APIKey = Depends(ui_require_admin),
):
    from ..firmware import build_bundle
    device = await session.get(Device, device_dna)
    if device is None or not (device.dna_json and device.brain_json and device.pinmap_json):
        return RedirectResponse(f"/ui/devices/{device_dna}", status_code=303)
    path, info = build_bundle(
        device_dna=device.device_dna, dna=device.dna_json,
        brain=device.brain_json, pinmap=device.pinmap_json,
        catalog=load_catalog(),
    )
    sig = info["manifest"].get("signature") or {}
    release = FirmwareRelease(
        device_dna=device.device_dna,
        app_bundle_version=device.app_bundle_version,
        base_firmware_version=device.base_firmware_version,
        config_schema_version=device.config_schema_version,
        hardware_revision=device.hardware_revision,
        bundle_path=str(path), bundle_sha256=info["sha256"],
        manifest_json=info["manifest"],
        sig_alg=sig.get("alg", "ed25519"), sig_kid=sig.get("kid", ""), sig_value=sig.get("value", ""),
    )
    session.add(release); await session.flush()
    ch = await session.get(FirmwareChannel, (device.device_dna, "dev"))
    if ch is None:
        ch = FirmwareChannel(device_dna=device.device_dna, channel="dev")
        session.add(ch)
    ch.active_release_id = release.id
    ch.updated_by = actor.id
    device.firmware_bundle_path = str(path)
    await record(session, actor=actor.id, actor_kind="ui_session",
                 action="build_firmware", target_kind="device", target_id=device_dna,
                 detail={"size_bytes": info["size_bytes"], "sha256": info["sha256"],
                         "release_id": release.id})
    await session.commit()
    return RedirectResponse(f"/ui/devices/{device_dna}", status_code=303)


@router.get("/ui/devices/{device_dna}/twin-fragment", response_class=HTMLResponse)
async def ui_device_twin_fragment(
    device_dna: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    """HTMX fragment polled every few seconds by the device detail page."""
    import httpx
    device = await session.get(Device, device_dna)
    if device is None:
        return HTMLResponse("<div class='text-red-400 text-xs'>device not found</div>", status_code=404)
    edge = await session.get(EdgeSystem, device.edge_id)
    payload: dict = {"online": False, "reason": "edge_address_unset"}
    if edge and edge.address:
        url = f"{edge.address.rstrip('/')}/api/v1/twin/{device_dna}"
        try:
            async with httpx.AsyncClient(timeout=2.0) as client:
                resp = await client.get(url)
            if resp.status_code == 200:
                payload = {"online": True, "twin": resp.json(), "edge_url": edge.address}
            else:
                payload = {"online": False, "reason": f"edge HTTP {resp.status_code}"}
        except httpx.HTTPError as exc:
            payload = {"online": False, "reason": f"edge unreachable ({exc.__class__.__name__})"}
    return templates.TemplateResponse(
        "_device_twin.html", {"request": request, "device": device, "payload": payload}
    )


@router.get("/ui/processes", response_class=HTMLResponse)
async def ui_processes(
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    recipes = await recipe_runner.list_available_recipes()
    runs = (await session.execute(
        select(RecipeRun).order_by(RecipeRun.started_at.desc()).limit(30)
    )).scalars().all()
    twin_kinds = ["twin.heater_control", "twin.motor_control", "twin.weighscale_control"]
    return templates.TemplateResponse(
        "processes.html",
        {"request": request, "recipes": recipes, "runs": runs, "twin_kinds": twin_kinds},
    )


@router.get("/ui/devices/{device_dna}/ota", response_class=HTMLResponse)
async def ui_device_ota(
    device_dna: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    device = await session.get(Device, device_dna)
    if device is None:
        return RedirectResponse("/ui/devices", status_code=303)
    rels = (
        await session.execute(
            select(FirmwareRelease)
            .where(FirmwareRelease.device_dna == device_dna)
            .order_by(desc(FirmwareRelease.created_at))
        )
    ).scalars().all()
    chs = (
        await session.execute(
            select(FirmwareChannel).where(FirmwareChannel.device_dna == device_dna)
        )
    ).scalars().all()
    channels = {c.channel: c.active_release_id for c in chs}
    last = request.session.pop("ota_last_action", None)
    return templates.TemplateResponse(
        "ota.html",
        {
            "request": request,
            "device": device,
            "releases": rels,
            "releases_by_id": {r.id: r for r in rels},
            "channels": channels,
            "pubkey_b64": signing.public_key_b64(),
            "pubkey_kid": signing.signing_kid(),
            "last_action": last,
        },
    )


@router.post("/ui/devices/{device_dna}/dry-run-promote")
async def ui_device_dry_run_promote(
    device_dna: str,
    request: Request,
    channel: str = Form(...),
    release_id: str = Form(...),
    rollback_allowed: str = Form(""),
    session: AsyncSession = Depends(get_session),
    _: APIKey = Depends(ui_require_login),
):
    """Walks the OTA gates without committing. The result is rendered as a
    one-shot flash on the OTA page so the operator can preview before
    clicking the real Promote button.
    """
    from .devices import dry_run_promote, PromoteBody
    from ..models import APIKey as APIKeyModel
    bogus_admin = APIKeyModel(
        id="ui_session", owner="ui", tier="readonly",
        scopes_json=["devices:read"], rate_per_minute=60, rate_burst=10,
        hash_alg="argon2id", key_hash="x", status="active",
    )
    body = PromoteBody(channel=channel, release_id=release_id,
                       rollback_allowed=bool(rollback_allowed))
    verdict = await dry_run_promote(  # type: ignore[arg-type]
        device_dna=device_dna, body=body, session=session, _=bogus_admin,
    )
    request.session["ota_last_action"] = {
        "kind": "emerald" if verdict.get("ok") else "amber",
        "message": ("Dry-run PASSED — every gate green. " if verdict.get("ok")
                    else "Dry-run FAILED — ") + " · ".join(
            f"{g['id']}={'✓' if g['ok'] else '✗'}" for g in verdict.get("gates", [])
        ),
    }
    return RedirectResponse(f"/ui/devices/{device_dna}/ota", status_code=303)


@router.post("/ui/devices/{device_dna}/promote")
async def ui_device_promote(
    device_dna: str,
    request: Request,
    channel: str = Form(...),
    release_id: str = Form(...),
    session: AsyncSession = Depends(get_session),
    actor: APIKey = Depends(ui_require_admin),
):
    device = await session.get(Device, device_dna)
    release = await session.get(FirmwareRelease, release_id)
    if device is None or release is None or release.device_dna != device_dna:
        request.session["ota_last_action"] = {"kind": "red", "message": "Release or device not found."}
        return RedirectResponse(f"/ui/devices/{device_dna}/ota", status_code=303)
    if channel not in ("dev", "beta", "stable"):
        request.session["ota_last_action"] = {"kind": "red", "message": f"Invalid channel: {channel}."}
        return RedirectResponse(f"/ui/devices/{device_dna}/ota", status_code=303)

    cur = await session.get(FirmwareChannel, (device_dna, channel))
    if cur is None:
        cur = FirmwareChannel(device_dna=device_dna, channel=channel)
        session.add(cur)
    cur.active_release_id = release.id
    cur.updated_by = actor.id
    if channel == "stable":
        device.firmware_bundle_path = release.bundle_path
    await record(
        session, actor=actor.id, actor_kind="ui_session",
        action="promote_firmware", target_kind="device", target_id=device_dna,
        detail={"channel": channel, "release_id": release.id,
                "app_bundle_version": release.app_bundle_version},
    )
    await session.commit()
    request.session["ota_last_action"] = {
        "kind": "emerald",
        "message": f"Promoted {release.app_bundle_version} → {channel}.",
    }
    return RedirectResponse(f"/ui/devices/{device_dna}/ota", status_code=303)


@router.post("/ui/devices/{device_dna}/rollback")
async def ui_device_rollback(
    device_dna: str,
    request: Request,
    channel: str = Form(...),
    session: AsyncSession = Depends(get_session),
    actor: APIKey = Depends(ui_require_admin),
):
    device = await session.get(Device, device_dna)
    if device is None or channel not in ("dev", "beta", "stable"):
        return RedirectResponse(f"/ui/devices/{device_dna}/ota", status_code=303)
    rels = (
        await session.execute(
            select(FirmwareRelease)
            .where(FirmwareRelease.device_dna == device_dna)
            .order_by(desc(FirmwareRelease.created_at))
        )
    ).scalars().all()
    if len(rels) < 2:
        request.session["ota_last_action"] = {
            "kind": "amber",
            "message": "Nothing to roll back to — only one release exists.",
        }
        return RedirectResponse(f"/ui/devices/{device_dna}/ota", status_code=303)
    prev = rels[1]
    cur = await session.get(FirmwareChannel, (device_dna, channel))
    if cur is None:
        cur = FirmwareChannel(device_dna=device_dna, channel=channel)
        session.add(cur)
    cur.active_release_id = prev.id
    cur.updated_by = actor.id
    if channel == "stable":
        device.firmware_bundle_path = prev.bundle_path
    await record(
        session, actor=actor.id, actor_kind="ui_session",
        action="rollback_firmware", target_kind="device", target_id=device_dna,
        detail={"channel": channel, "release_id": prev.id,
                "app_bundle_version": prev.app_bundle_version},
    )
    await session.commit()
    request.session["ota_last_action"] = {
        "kind": "emerald",
        "message": f"Rolled {channel} back to {prev.app_bundle_version}.",
    }
    return RedirectResponse(f"/ui/devices/{device_dna}/ota", status_code=303)


# ─── Recipe definition CRUD (DB-backed) ─────────────────────────────────────
# IMPORTANT: these specific paths must be declared BEFORE the
# /ui/recipes/{recipe_id} catch-all below, otherwise FastAPI matches the
# pattern first and 'new' / 'edit' end up as recipe_ids.
@router.get("/ui/recipes/new", response_class=HTMLResponse)
async def ui_recipe_new_form(
    request: Request,
    error: str | None = None,
    _: APIKey = Depends(ui_require_admin),
):
    starter = {
        "recipe_id": "recipe.cooking.my_recipe",
        "name": "My recipe",
        "version": "0.1.0",
        "twin_targets": ["twin.heater_control"],
        "params": {"setpoint_c": 80, "hold_minutes": 30},
        "steps": [
            {"id": "preheat",   "action": "set_state",     "args": {"state": "PREHEAT"}},
            {"id": "wait_hot",  "action": "await",         "args": {"expr": "reported.process_temp_c >= 79.5"}},
            {"id": "hold",      "action": "set_state",     "args": {"state": "HOLD"}},
            {"id": "hold_timer","action": "wait_minutes",  "args": {"minutes": "${params.hold_minutes}"}},
            {"id": "cooldown",  "action": "set_state",     "args": {"state": "COOLDOWN"}},
        ],
    }
    import json as _json
    return templates.TemplateResponse(
        "recipe_form.html",
        {"request": request, "mode": "create", "row": None,
         "recipe_text": _json.dumps(starter, indent=2), "error": error},
    )


@router.post("/ui/recipes/new")
async def ui_recipe_new_submit(
    request: Request,
    recipe_text: str = Form(...),
    notes: str = Form(""),
    session: AsyncSession = Depends(get_session),
    actor: APIKey = Depends(ui_require_admin),
):
    err = await _persist_recipe(session=session, actor=actor,
                                 recipe_text=recipe_text, existing=None, notes=notes)
    if err:
        return templates.TemplateResponse(
            "recipe_form.html",
            {"request": request, "mode": "create", "row": None,
             "recipe_text": recipe_text, "error": err},
            status_code=400,
        )
    return RedirectResponse("/ui/processes", status_code=303)


@router.get("/ui/recipes/edit/{db_id}", response_class=HTMLResponse)
async def ui_recipe_edit_form(
    db_id: str,
    request: Request,
    error: str | None = None,
    session: AsyncSession = Depends(get_session),
    _: APIKey = Depends(ui_require_admin),
):
    row = await session.get(RecipeDef, db_id)
    if row is None or row.deleted_at is not None:
        return RedirectResponse("/ui/processes", status_code=303)
    import json as _json
    return templates.TemplateResponse(
        "recipe_form.html",
        {"request": request, "mode": "edit", "row": row,
         "recipe_text": _json.dumps(row.recipe_json, indent=2), "error": error},
    )


@router.post("/ui/recipes/edit/{db_id}")
async def ui_recipe_edit_submit(
    db_id: str,
    request: Request,
    recipe_text: str = Form(...),
    notes: str = Form(""),
    session: AsyncSession = Depends(get_session),
    actor: APIKey = Depends(ui_require_admin),
):
    row = await session.get(RecipeDef, db_id)
    if row is None or row.deleted_at is not None:
        return RedirectResponse("/ui/processes", status_code=303)
    err = await _persist_recipe(session=session, actor=actor,
                                 recipe_text=recipe_text, existing=row, notes=notes)
    if err:
        return templates.TemplateResponse(
            "recipe_form.html",
            {"request": request, "mode": "edit", "row": row,
             "recipe_text": recipe_text, "error": err},
            status_code=400,
        )
    return RedirectResponse("/ui/processes", status_code=303)


@router.post("/ui/recipes/delete/{db_id}")
async def ui_recipe_delete(
    db_id: str,
    session: AsyncSession = Depends(get_session),
    actor: APIKey = Depends(ui_require_admin),
):
    from datetime import datetime, timezone
    row = await session.get(RecipeDef, db_id)
    if row is None:
        return RedirectResponse("/ui/processes", status_code=303)
    row.deleted_at = datetime.now(timezone.utc)
    await record(session, actor=actor.id, actor_kind="ui_session",
                 action="delete_recipe", target_kind="recipe_def",
                 target_id=row.recipe_id)
    await session.commit()
    return RedirectResponse("/ui/processes", status_code=303)


@router.get("/ui/recipes/{recipe_id}", response_class=HTMLResponse)
async def ui_recipe_run(
    recipe_id: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    """Run page for a single recipe (DB or file-backed)."""
    recipe = await recipe_runner._get_recipe(recipe_id)
    if recipe is None:
        return templates.TemplateResponse(
            "processes.html",
            {"request": request, "recipes": await recipe_runner.list_available_recipes(),
             "runs": [], "twin_kinds": []},
        )
    devices = (await session.execute(select(Device).order_by(Device.created_at.desc()))).scalars().all()
    return templates.TemplateResponse(
        "recipe_run.html",
        {"request": request, "recipe": recipe, "devices": devices},
    )


async def _persist_recipe(*, session, actor, recipe_text, existing, notes):
    """Validate + save a recipe doc. Returns an error string if rejected,
    None on success."""
    import json as _json
    try:
        doc = _json.loads(recipe_text)
    except _json.JSONDecodeError as exc:
        return f"Invalid JSON: {exc}"
    if not isinstance(doc, dict):
        return "Recipe must be a JSON object."
    rid = doc.get("recipe_id")
    if not isinstance(rid, str) or not rid.strip():
        return "recipe_id is required and must be a non-empty string."
    if "steps" not in doc or not isinstance(doc["steps"], list) or not doc["steps"]:
        return "steps[] is required and must be a non-empty list."
    for i, step in enumerate(doc["steps"]):
        if not isinstance(step, dict) or not step.get("id") or not step.get("action"):
            return f"Step {i} must have both id and action."
    if existing is None:
        clash = await session.scalar(
            select(RecipeDef).where(RecipeDef.recipe_id == rid,
                                    RecipeDef.deleted_at.is_(None))
        )
        if clash is not None:
            return f"recipe_id '{rid}' already exists."
        row = RecipeDef(
            recipe_id=rid, name=doc.get("name", rid),
            version=doc.get("version", "0.1.0"),
            recipe_json=doc, notes=notes or None, created_by=actor.id,
        )
        session.add(row)
        await session.flush()
        action = "create_recipe"
    else:
        if rid != existing.recipe_id:
            return "recipe_id is immutable."
        existing.name = doc.get("name", existing.name)
        existing.version = doc.get("version", existing.version)
        existing.recipe_json = doc
        if notes:
            existing.notes = notes
        action = "update_recipe"
    await record(session, actor=actor.id, actor_kind="ui_session",
                 action=action, target_kind="recipe_def", target_id=rid)
    await session.commit()
    return None
