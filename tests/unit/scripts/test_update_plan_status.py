from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tests.unit.scripts.plan_fixture import TASKS_BY_PHASE, build_plan

ROOT = Path(__file__).resolve().parents[3]
UPDATER = ROOT / "scripts" / "update_plan_status.py"


class UpdatePlanStatusCliTests(unittest.TestCase):
    def run_updater(
        self, content: str, *arguments: str
    ) -> tuple[subprocess.CompletedProcess[str], str, list[str]]:
        with tempfile.TemporaryDirectory() as temp_dir:
            plan_path = Path(temp_dir) / "Plan.md"
            plan_path.write_text(content, encoding="utf-8")
            environment = os.environ.copy()
            environment["PYTHONUTF8"] = "1"
            result = subprocess.run(
                [sys.executable, str(UPDATER), *arguments, "--plan", str(plan_path)],
                cwd=ROOT,
                capture_output=True,
                text=True,
                encoding="utf-8",
                env=environment,
                check=False,
            )
            updated = plan_path.read_text(encoding="utf-8")
            leftovers = [path.name for path in plan_path.parent.glob(".Plan.md.*.tmp")]
            return result, updated, leftovers

    def test_complete_task_updates_heading_checkbox_and_journal(self) -> None:
        result, updated, leftovers = self.run_updater(
            build_plan(),
            "complete-task",
            "P0.T0",
            "--evidence",
            "unittest: 8 passed",
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("#### P0.T0. Task 0 — ✅ DONE", updated)
        self.assertIn("- [x] **P0.T0 завершена", updated)
        self.assertIn("| P0.T0 |", updated)
        self.assertIn("unittest: 8 passed", updated)
        self.assertEqual(leftovers, [])

    def test_complete_task_rejects_skipped_predecessor_without_writing(self) -> None:
        original = build_plan()

        result, updated, _ = self.run_updater(
            original,
            "complete-task",
            "P0.T1",
            "--evidence",
            "tests passed",
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(updated, original)
        self.assertIn("P0.T0", result.stderr)

    def test_complete_phase_updates_progress_and_journal(self) -> None:
        p0_tasks = set(TASKS_BY_PHASE[0])
        plan = build_plan(done_tasks=p0_tasks, evidence_tasks=p0_tasks)

        result, updated, leftovers = self.run_updater(
            plan,
            "complete-phase",
            "P0",
            "--evidence",
            "ruff, mypy, pytest: exit 0",
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("| P0 | Plan 0 | ruff, mypy, pytest: exit 0 | ✅ DONE | Нет |", updated)
        self.assertIn("| P0 |", updated)
        self.assertEqual(leftovers, [])

    def test_complete_phase_rejects_incomplete_tasks_without_writing(self) -> None:
        original = build_plan()

        result, updated, _ = self.run_updater(
            original,
            "complete-phase",
            "P0",
            "--evidence",
            "gate passed",
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(updated, original)
        self.assertIn("не завершены", result.stderr.lower())


if __name__ == "__main__":
    unittest.main()
