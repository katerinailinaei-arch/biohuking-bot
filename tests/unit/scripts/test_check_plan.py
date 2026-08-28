from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
import os
from pathlib import Path

from tests.unit.scripts.plan_fixture import build_plan


ROOT = Path(__file__).resolve().parents[3]
CHECKER = ROOT / "scripts" / "check_plan.py"


class CheckPlanCliTests(unittest.TestCase):
    def run_checker(self, content: str) -> subprocess.CompletedProcess[str]:
        with tempfile.TemporaryDirectory() as temp_dir:
            plan_path = Path(temp_dir) / "Plan.md"
            plan_path.write_text(content, encoding="utf-8")
            environment = os.environ.copy()
            environment["PYTHONUTF8"] = "1"
            return subprocess.run(
                [sys.executable, str(CHECKER), str(plan_path)],
                cwd=ROOT,
                capture_output=True,
                text=True,
                encoding="utf-8",
                env=environment,
                check=False,
            )

    def test_accepts_complete_current_plan_shape(self) -> None:
        result = self.run_checker(build_plan())

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("PLAN_OK phases=8 tasks=18", result.stdout)

    def test_rejects_unknown_task_status(self) -> None:
        plan = build_plan().replace("⬜ NOT_STARTED", "🟣 UNKNOWN", 1)

        result = self.run_checker(plan)

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("неизвестный статус", result.stderr.lower())

    def test_rejects_done_task_without_evidence(self) -> None:
        result = self.run_checker(build_plan(done_tasks={0}))

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("P0.T0", result.stderr)
        self.assertIn("evidence", result.stderr.lower())

    def test_rejects_phase_started_before_previous_phase_done(self) -> None:
        result = self.run_checker(build_plan(phase_statuses={1: "🟡 IN_PROGRESS"}))

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("перескок", result.stderr.lower())


if __name__ == "__main__":
    unittest.main()
