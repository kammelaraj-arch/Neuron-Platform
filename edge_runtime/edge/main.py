from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI

from .config import settings
from .db import init_db
from .middleware import AccessPolicyMiddleware
from .policy import load_policy
from .routes_devices import router as devices_router
from .routes_emergency import router as emergency_router
from .routes_health import router as health_router, health_monitor_loop
from .routes_twin import router as twin_router
from .routes_ui import router as ui_router


_log = logging.getLogger("neuron.edge")


@asynccontextmanager
async def lifespan(app: FastAPI):
    Path("data").mkdir(parents=True, exist_ok=True)
    await init_db()
    stop_event = asyncio.Event()
    monitor_task = asyncio.create_task(health_monitor_loop(stop_event))

    # Optional MQTT bridge. When EDGE_MQTT_HOST is set, the Edge connects
    # to the local Mosquitto broker and bridges twin_cache <-> MQTT topics.
    mqtt_task = None
    if settings.mqtt_host:
        from .mqtt_bridge import configure
        bridge = configure(settings.mqtt_host, settings.mqtt_port)
        if bridge is not None:
            mqtt_task = asyncio.create_task(bridge.run())

    # Optional vendor integrations. Each runs its own loop and publishes
    # into the Edge's twin_cache; the cache then fans out via MQTT + SSE
    # automatically. Failures don't stop the Edge.
    integration_tasks: list[asyncio.Task] = []
    try:
        from .integrations import tapo as tapo_integration
        integration_tasks.append(asyncio.create_task(tapo_integration.run_loop(stop_event)))
    except Exception as exc:  # noqa: BLE001
        _log.warning("tapo integration failed to start: %s", exc)

    try:
        yield
    finally:
        stop_event.set()
        try:
            await asyncio.wait_for(monitor_task, timeout=2.0)
        except asyncio.TimeoutError:
            monitor_task.cancel()
        if mqtt_task is not None:
            from .mqtt_bridge import bridge as _b
            if _b is not None:
                _b.stop()
            mqtt_task.cancel()
        for t in integration_tasks:
            t.cancel()


policy = load_policy()

app = FastAPI(
    title="Neuron Edge Runtime",
    version="0.3.0",
    description="Site-local orchestration with deny-by-default allow-list and emergency channel.",
    lifespan=lifespan,
)

app.add_middleware(
    AccessPolicyMiddleware,
    policy=policy,
    port=settings.bind_port,
)

app.include_router(ui_router)
app.include_router(health_router)
app.include_router(devices_router)
app.include_router(twin_router)
app.include_router(emergency_router)
