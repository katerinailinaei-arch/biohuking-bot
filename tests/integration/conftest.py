from __future__ import annotations

import os

import pytest

if os.getenv("CI") and not os.getenv("TEST_DATABASE_URL"):
    raise pytest.UsageError("TEST_DATABASE_URL is required in CI")
