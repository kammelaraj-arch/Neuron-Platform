from __future__ import annotations

import json
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from threading import RLock
from typing import Any

import jsonschema

from .config import settings

LIBRARY_DIRS = (
    "components_library",
    "control_board_library",
    "micro_compute_library",
    "digital_twin_controls_library",
    "ui_controls_library",
    "business_library",
    "functional_library",
    "api_library",
)

LIBRARY_GROUPS: dict[str, tuple[str, ...]] = {
    "Hardware": (
        "components_library",
        "control_board_library",
        "micro_compute_library",
    ),
    "Digital Twin": ("digital_twin_controls_library",),
    "UI": ("ui_controls_library",),
    "Business": ("business_library",),
    "Functional": ("functional_library",),
    "API": ("api_library",),
}


@dataclass
class LibraryItem:
    stable_id: str
    library: str
    category: str
    version: str
    name: str
    vendor: str
    manifest: dict[str, Any]
    path: Path | None = None
    source: str = "file"  # "file" or "db"
    db_id: str | None = None

    @property
    def safety_class(self) -> str:
        return self.manifest.get("safety_class", "nominal")

    @property
    def editable(self) -> bool:
        return self.source == "db"


@dataclass
class LibraryCatalog:
    by_id: dict[str, LibraryItem] = field(default_factory=dict)
    by_library: dict[str, list[LibraryItem]] = field(default_factory=dict)

    def add(self, item: LibraryItem) -> None:
        self.by_id[item.stable_id] = item
        self.by_library.setdefault(item.library, []).append(item)

    def get(self, stable_id: str) -> LibraryItem | None:
        return self.by_id.get(stable_id)

    def list_library(self, library: str) -> list[LibraryItem]:
        return list(self.by_library.get(library, []))


class LibraryLoadError(RuntimeError):
    pass


_lock = RLock()
_catalog: LibraryCatalog | None = None


def _load_schema() -> dict[str, Any]:
    schema_path = settings.shared_schemas_dir / "manifest_schema.json"
    if not schema_path.exists():
        raise LibraryLoadError(f"manifest schema not found: {schema_path}")
    with schema_path.open(encoding="utf-8") as fh:
        return json.load(fh)


def _scan(libraries_root: Path, schema: dict[str, Any]) -> LibraryCatalog:
    if not libraries_root.is_dir():
        raise LibraryLoadError(f"libraries dir not found: {libraries_root}")
    validator = jsonschema.Draft7Validator(schema)
    catalog = LibraryCatalog()
    for lib in LIBRARY_DIRS:
        manifest_dir = libraries_root / lib / "manifests"
        if not manifest_dir.is_dir():
            continue
        for path in sorted(manifest_dir.glob("*.json")):
            with path.open(encoding="utf-8") as fh:
                doc = json.load(fh)
            errors = sorted(validator.iter_errors(doc), key=lambda e: e.path)
            if errors:
                first = errors[0]
                raise LibraryLoadError(f"{path}: schema error: {first.message}")
            item = LibraryItem(
                stable_id=doc["stable_id"],
                library=doc["library"],
                category=doc["category"],
                version=doc["version"],
                name=doc["name"],
                vendor=doc["vendor"],
                manifest=doc,
                path=path,
            )
            if item.stable_id in catalog.by_id:
                raise LibraryLoadError(f"duplicate stable_id: {item.stable_id}")
            catalog.add(item)
    return catalog


def _merge_db_items(catalog: LibraryCatalog, schema: dict[str, Any]) -> None:
    """Merge DB-backed library items on top of file-based ones.

    DB items override file items if the same stable_id exists, so admins
    can correct a stock manifest without touching the source tree.
    """
    try:
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker

        from .config import settings as _s
        from .models_library import LibraryItemRow  # local import to avoid cycle
    except Exception:
        return

    sync_url = _s.db_url.replace("+aiosqlite", "")
    try:
        engine = create_engine(sync_url, future=True)
        Session = sessionmaker(engine, future=True)
        validator = jsonschema.Draft7Validator(schema)
        with Session() as ses:
            try:
                rows = ses.query(LibraryItemRow).filter(LibraryItemRow.deleted_at.is_(None)).all()
            except Exception:
                # Table may not exist on first call before init_db().
                return
            for row in rows:
                doc = row.manifest_json
                errs = list(validator.iter_errors(doc))
                if errs:
                    continue
                item = LibraryItem(
                    stable_id=doc["stable_id"],
                    library=doc["library"],
                    category=doc["category"],
                    version=doc["version"],
                    name=doc["name"],
                    vendor=doc["vendor"],
                    manifest=doc,
                    path=None,
                    source="db",
                    db_id=row.id,
                )
                # If this stable_id already exists from a file, replace it.
                old = catalog.by_id.get(item.stable_id)
                if old is not None:
                    catalog.by_library[old.library] = [
                        x for x in catalog.by_library[old.library] if x.stable_id != item.stable_id
                    ]
                catalog.by_id[item.stable_id] = item
                catalog.by_library.setdefault(item.library, []).append(item)
    except Exception:
        # Never let DB issues prevent the file catalog from loading.
        return


def load_catalog(force: bool = False) -> LibraryCatalog:
    global _catalog
    with _lock:
        if _catalog is None or force:
            schema = _load_schema()
            _catalog = _scan(settings.libraries_dir, schema)
            _merge_db_items(_catalog, schema)
        return _catalog


def invalidate_catalog() -> None:
    """Force the next load to re-read from disk + DB."""
    global _catalog
    with _lock:
        _catalog = None


@lru_cache(maxsize=1)
def shared_schema(name: str) -> dict[str, Any]:
    path = settings.shared_schemas_dir / f"{name}.json"
    if not path.exists():
        raise LibraryLoadError(f"shared schema missing: {path}")
    with path.open(encoding="utf-8") as fh:
        return json.load(fh)
