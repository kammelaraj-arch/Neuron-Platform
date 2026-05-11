"""Validate that every shipped manifest passes the Stage 1 validator."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def test_validate_manifests_strict(repo_root: Path):
    result = subprocess.run(
        [sys.executable, "tools/validate_manifests.py", "--root", ".", "--strict"],
        cwd=repo_root,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, (
        f"validator failed:\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
    )
    assert "OK:" in result.stdout
    # At least the original 25 stock manifests; growing the catalog is fine,
    # shrinking it accidentally is what we want to catch.
    import re
    m = re.search(r"Manifests scanned: (\d+)", result.stdout)
    assert m, "validator output missing scanned count"
    scanned = int(m.group(1))
    assert scanned >= 25, f"manifest count regressed: {scanned}"
