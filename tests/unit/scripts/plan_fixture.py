from __future__ import annotations

from collections.abc import Iterable

TASKS_BY_PHASE: dict[int, tuple[int, ...]] = {
    0: (0, 1, 2, 3, 4),
    1: (5, 6, 7),
    2: (8,),
    3: (9, 10),
    4: (11, 12),
    5: (13,),
    6: (14, 15, 16),
    7: (17,),
}


def build_plan(
    *,
    done_tasks: Iterable[int] = (),
    phase_statuses: dict[int, str] | None = None,
    evidence_tasks: Iterable[int] = (),
) -> str:
    done = set(done_tasks)
    evidence = set(evidence_tasks)
    statuses = phase_statuses or {}
    lines = ["# Test plan", "", "## 3. Фазы и задачи", ""]

    for phase, tasks in TASKS_BY_PHASE.items():
        lines.extend((f"### P{phase}. Phase {phase}", ""))
        for task in tasks:
            task_status = "✅ DONE" if task in done else "⬜ NOT_STARTED"
            checkbox = "x" if task in done else " "
            lines.extend(
                (
                    f"#### P{phase}.T{task}. Task {task} — {task_status}",
                    "",
                    f"- [{checkbox}] **P{phase}.T{task} завершена и имеет evidence.**",
                    "",
                )
            )

    lines.extend(
        (
            "## 6. Таблица прогресса",
            "",
            "| Фаза | План | Факт | Статус | Блокеры |",
            "|---|---|---|---|---|",
        )
    )
    for phase in TASKS_BY_PHASE:
        status = statuses.get(phase, "⬜ NOT_STARTED")
        blocker = f"Gate P{phase - 1 if phase else 0}"
        lines.append(f"| P{phase} | Plan {phase} | Ещё не выполнено | {status} | {blocker} |")

    lines.extend(
        (
            "",
            "### 7.3. Формат evidence journal",
            "",
            "| UTC date | Task/Phase | Commit | Commands and result "
            "| Evidence artifact | Limitations |",
            "|---|---|---|---|---|---|",
        )
    )
    for task in sorted(evidence):
        phase = next(phase for phase, tasks in TASKS_BY_PHASE.items() if task in tasks)
        lines.append(
            f"| 2026-08-28T12:00:00Z | P{phase}.T{task} | abc1234 "
            "| tests passed | test-report | Нет |"
        )
    lines.extend(("| — | — | — | — | — | — |", ""))
    return "\n".join(lines)
