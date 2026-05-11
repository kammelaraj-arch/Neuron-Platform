"""Recipe runner state-machine tests."""
from __future__ import annotations

import asyncio

import pytest


@pytest.fixture
async def initialised_master(isolated_master):
    from backend.db import init_db
    await init_db()
    yield isolated_master


async def _seed_chain(actor_id="seed"):
    from backend.db import SessionLocal
    from backend.models import Device, EdgeSystem, NodeSystem, RootSystem
    async with SessionLocal() as session:
        root = RootSystem(name="r"); session.add(root); await session.flush()
        node = NodeSystem(root_id=root.id, name="n"); session.add(node); await session.flush()
        edge = EdgeSystem(node_id=node.id, name="e", site_id="s",
                          address="http://127.0.0.1:1")  # unreachable on purpose
        session.add(edge); await session.flush()
        dna = "DNA-RTST-AAAA-BBBB-CCCC"
        dev = Device(
            device_dna=dna, edge_id=edge.id, device_type="pico2w",
            compute_stable_id="compute.pico2w", hardware_revision="rev_a",
            base_firmware_version="1.0.0", app_bundle_version="1.0.0",
            config_schema_version="1.0.0",
        )
        session.add(dev)
        await session.commit()
        return dna


async def test_list_recipes_picks_up_file_on_disk(initialised_master):
    from backend.runtime import recipe_runner
    items = await recipe_runner.list_available_recipes()
    ids = [r["recipe_id"] for r in items]
    assert "recipe.cooking.hold_80c" in ids


async def test_start_recipe_creates_running_row(initialised_master):
    from backend.runtime import recipe_runner
    from backend.db import SessionLocal
    from backend.models import RecipeRun

    dna = await _seed_chain()
    run = await recipe_runner.start_run(
        recipe_id="recipe.cooking.hold_80c",
        device_dna=dna,
        params_override={"setpoint_c": 80, "hold_minutes": 0.01},
        started_by="t",
    )
    assert run.state in ("running", "completed")  # may have raced to completion
    async with SessionLocal() as session:
        again = await session.get(RecipeRun, run.id)
        assert again is not None
        assert again.recipe_id == "recipe.cooking.hold_80c"


async def test_stop_recipe_aborts(initialised_master):
    from backend.runtime import recipe_runner
    dna = await _seed_chain()
    run = await recipe_runner.start_run(
        recipe_id="recipe.cooking.hold_80c",
        device_dna=dna,
        params_override={"hold_minutes": 5},  # long enough to interrupt
        started_by="t",
    )
    await asyncio.sleep(0.5)
    aborted = await recipe_runner.stop_run(run.id)
    assert aborted.state in ("aborted", "completed", "failed")
    # Ensure no lingering background task — task removed itself on cancel.
    await asyncio.sleep(0.5)
    assert run.id not in recipe_runner._active or recipe_runner._active[run.id].done()


async def test_unknown_recipe_raises(initialised_master):
    from backend.runtime import recipe_runner
    dna = await _seed_chain()
    with pytest.raises(LookupError):
        await recipe_runner.start_run(
            recipe_id="recipe.does.not.exist",
            device_dna=dna,
            params_override=None,
            started_by="t",
        )
