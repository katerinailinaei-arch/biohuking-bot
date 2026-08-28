from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_ci_rejects_a_skipped_postgresql_integration_suite() -> None:
    environment = os.environ.copy()
    environment["CI"] = "true"
    environment.pop("TEST_DATABASE_URL", None)

    result = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/integration", "--collect-only", "-q"],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode != 0
    assert "TEST_DATABASE_URL is required in CI" in result.stderr
