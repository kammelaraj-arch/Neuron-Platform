"""Pytest fixtures shared across the Neuron Platform test suite.

The Master Platform code lives at neuron-platform/master_platform/backend/*
and the Edge Runtime at neuron-platform/edge_runtime/edge/*. Both are
imported as relative packages (``backend`` and ``edge`` respectively) so the
fixtures here add the right sys.path entries and isolate file-based state
(SQLite DB, signing keys, build artifacts) under a per-test tmp directory.
"""
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

import pytest


_REPO = Path(__file__).resolve().parent.parent  # neuron-platform/
MASTER = _REPO / "master_platform"
EDGE = _REPO / "edge_runtime"
PICO = _REPO / "level0-pico2w"


@pytest.fixture(scope="session")
def repo_root() -> Path:
    return _REPO


@pytest.fixture(scope="session", autouse=True)
def _path_setup():
    sys.path.insert(0, str(MASTER))
    sys.path.insert(0, str(EDGE))
    sys.path.insert(0, str(PICO))
    yield


@pytest.fixture
def isolated_master(tmp_path, monkeypatch):
    """Cwd into a clean directory + point the Master at it.

    Each test gets a private SQLite DB, build_artifacts/ dir, signing key,
    and session secret — no cross-test pollution.
    """
    work = tmp_path / "master"
    (work / "data").mkdir(parents=True)
    (work / "build_artifacts").mkdir(parents=True)

    monkeypatch.setenv("NEURON_DB_URL", f"sqlite+aiosqlite:///{work}/data/neuron.db")
    monkeypatch.setenv("NEURON_LIBRARIES_DIR", str(_REPO / "libraries"))
    monkeypatch.setenv("NEURON_SHARED_SCHEMAS_DIR", str(_REPO / "shared_schemas"))
    monkeypatch.setenv("NEURON_BUILD_ARTIFACTS_DIR", str(work / "build_artifacts"))

    monkeypatch.chdir(work)

    # Wipe any previously imported copies so settings re-read env vars.
    for mod in list(sys.modules):
        if mod.startswith("backend"):
            del sys.modules[mod]

    yield work


@pytest.fixture
def isolated_edge(tmp_path, monkeypatch):
    work = tmp_path / "edge"
    (work / "data").mkdir(parents=True)
    monkeypatch.setenv("EDGE_DB_URL", f"sqlite+aiosqlite:///{work}/data/edge.db")
    monkeypatch.setenv("EDGE_POLICY_PATH", str(EDGE / "policies" / "edge_default.json"))
    monkeypatch.chdir(work)
    for mod in list(sys.modules):
        if mod.startswith("edge"):
            del sys.modules[mod]
    yield work
