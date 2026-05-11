#!/usr/bin/env python3
"""
Neuron Platform — manifest & schema validator.

Validates every JSON manifest under libraries/*/manifests/ against the
universal manifest_schema.json, plus cross-checks:

  - stable_id uniqueness across all libraries
  - manifest.library matches the library directory it lives in
  - dependency stable_ids resolve
  - compatibility.compute / .boards / .twin_controls / .ui_widgets resolve
  - drivers.runtime is consistent with the library type

Exit code is non-zero on any error.

Usage:
    python tools/validate_manifests.py [--root .] [--strict]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

try:
    import jsonschema
    from jsonschema import Draft7Validator
except ImportError:  # pragma: no cover
    sys.stderr.write(
        "ERROR: jsonschema is required. Install with: pip install jsonschema\n"
    )
    sys.exit(2)


SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+(?:[-+][\w.\-]+)?$")
LIBRARIES = (
    "components_library",
    "control_board_library",
    "micro_compute_library",
    "digital_twin_controls_library",
    "ui_controls_library",
    "business_library",
    "functional_library",
    "api_library",
)


@dataclass
class Report:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    manifests: dict[str, dict[str, Any]] = field(default_factory=dict)

    def err(self, msg: str) -> None:
        self.errors.append(msg)

    def warn(self, msg: str) -> None:
        self.warnings.append(msg)

    def ok(self) -> bool:
        return not self.errors


def load_json(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as fh:
        return json.load(fh)


def collect_manifests(root: Path, report: Report) -> None:
    for lib in LIBRARIES:
        manifest_dir = root / "libraries" / lib / "manifests"
        if not manifest_dir.is_dir():
            report.err(f"missing manifest directory: {manifest_dir}")
            continue
        for path in sorted(manifest_dir.glob("*.json")):
            try:
                doc = load_json(path)
            except json.JSONDecodeError as exc:
                report.err(f"{path}: invalid JSON ({exc})")
                continue
            stable_id = doc.get("stable_id")
            if not stable_id:
                report.err(f"{path}: missing stable_id")
                continue
            if stable_id in report.manifests:
                report.err(
                    f"{path}: duplicate stable_id '{stable_id}' "
                    f"(also in {report.manifests[stable_id]['__path__']})"
                )
                continue
            doc["__path__"] = str(path)
            doc["__library_dir__"] = lib
            report.manifests[stable_id] = doc


def validate_against_schema(schema: dict[str, Any], report: Report) -> None:
    validator = Draft7Validator(schema)
    for stable_id, doc in report.manifests.items():
        clean = {k: v for k, v in doc.items() if not k.startswith("__")}
        for err in sorted(validator.iter_errors(clean), key=lambda e: e.path):
            loc = "/".join(str(p) for p in err.path) or "<root>"
            report.err(f"{doc['__path__']}: schema {loc}: {err.message}")


def validate_cross_references(report: Report) -> None:
    by_id = report.manifests

    expected_runtimes_by_lib = {
        "components_library":            {"micropython", "cpython", "linux_pkg"},
        "control_board_library":         {"micropython", "cpython", "linux_pkg"},
        "micro_compute_library":         {"micropython", "cpython", "linux_pkg"},
        "digital_twin_controls_library": {"cpython", "node"},
        "ui_controls_library":           {"node", "cpython"},
        "business_library":              {"cpython"},
        "functional_library":            {"cpython", "micropython"},
        "api_library":                   {"cpython"},
    }

    for stable_id, doc in by_id.items():
        path = doc["__path__"]
        lib_dir = doc["__library_dir__"]

        if doc.get("library") != lib_dir:
            report.err(
                f"{path}: manifest.library='{doc.get('library')}' does not match "
                f"directory '{lib_dir}'"
            )

        version = doc.get("version", "")
        if not SEMVER_RE.match(version):
            report.err(f"{path}: version '{version}' is not SemVer 2.0.0")

        for dep in doc.get("dependencies", []):
            dep_id = dep.get("stable_id")
            if dep_id and dep_id not in by_id and not dep.get("optional"):
                report.err(f"{path}: dependency '{dep_id}' not found in any library")

        compat = doc.get("compatibility", {}) or {}
        for key in ("boards", "compute", "twin_controls", "ui_widgets"):
            for ref in compat.get(key, []) or []:
                if ref not in by_id:
                    report.warn(
                        f"{path}: compatibility.{key} references unknown id '{ref}'"
                    )

        runtimes_allowed = expected_runtimes_by_lib.get(lib_dir, set())
        for drv in doc.get("drivers", []) or []:
            rt = drv.get("runtime")
            if runtimes_allowed and rt not in runtimes_allowed:
                report.warn(
                    f"{path}: driver runtime '{rt}' unusual for '{lib_dir}' "
                    f"(allowed: {sorted(runtimes_allowed)})"
                )

        for tb in doc.get("twin_bindings", []) or []:
            if tb.get("control_id") and tb["control_id"] not in by_id:
                report.warn(
                    f"{path}: twin_bindings.control_id '{tb['control_id']}' unknown"
                )


def main() -> int:
    ap = argparse.ArgumentParser(description="Validate Neuron Platform manifests.")
    ap.add_argument("--root", default=".", help="Repository root (default: cwd).")
    ap.add_argument(
        "--strict", action="store_true", help="Treat warnings as errors."
    )
    args = ap.parse_args()

    root = Path(args.root).resolve()
    schema_path = root / "shared_schemas" / "manifest_schema.json"
    if not schema_path.exists():
        sys.stderr.write(f"manifest schema not found: {schema_path}\n")
        return 2

    schema = load_json(schema_path)
    try:
        Draft7Validator.check_schema(schema)
    except jsonschema.SchemaError as exc:
        sys.stderr.write(f"manifest_schema.json is itself invalid: {exc}\n")
        return 2

    report = Report()
    collect_manifests(root, report)
    validate_against_schema(schema, report)
    validate_cross_references(report)

    print(f"Manifests scanned: {len(report.manifests)}")
    for w in report.warnings:
        print(f"  WARN  {w}")
    for e in report.errors:
        print(f"  ERROR {e}")

    if report.errors or (args.strict and report.warnings):
        print(
            f"\nFAIL: {len(report.errors)} error(s), "
            f"{len(report.warnings)} warning(s)."
        )
        return 1
    print(
        f"\nOK: {len(report.manifests)} manifests valid "
        f"({len(report.warnings)} warning(s))."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
