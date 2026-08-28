from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path

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
KNOWN_STATUSES = {"⬜ NOT_STARTED", "🟡 IN_PROGRESS", "✅ DONE", "🔴 BLOCKED"}

TASK_HEADING_RE = re.compile(
    r"^#### (?P<key>P(?P<phase>\d+)\.T(?P<task>\d+))\..+ — (?P<status>.+)$",
    re.MULTILINE,
)
TASK_CHECKBOX_RE = re.compile(
    r"^- \[(?P<checked>[ xX])\] \*\*(?P<key>P\d+\.T\d+) завершена и имеет evidence\.\*\*$",
    re.MULTILINE,
)
PHASE_ROW_RE = re.compile(
    r"^\| P(?P<phase>\d+) \| (?P<plan>[^|]+) \| (?P<fact>[^|]+) "
    r"\| (?P<status>[^|]+) \| (?P<blockers>[^|]+) \|$",
    re.MULTILINE,
)
EVIDENCE_ROW_RE = re.compile(
    r"^\| [^|]+ \| (?P<target>P\d+(?:\.T\d+)?) \| [^|]+ \| [^|]+ \| [^|]+ \| [^|]+ \|$",
    re.MULTILINE,
)


class PlanValidationError(ValueError):
    pass


@dataclass(frozen=True)
class PlanState:
    task_statuses: dict[str, str]
    phase_statuses: dict[int, str]
    evidence_targets: frozenset[str]


def _expected_task_keys() -> tuple[str, ...]:
    return tuple(
        f"P{phase}.T{task}"
        for phase, tasks in TASKS_BY_PHASE.items()
        for task in tasks
    )


def validate_plan(content: str) -> PlanState:
    task_matches = list(TASK_HEADING_RE.finditer(content))
    task_keys = [match.group("key") for match in task_matches]
    expected_keys = list(_expected_task_keys())
    if task_keys != expected_keys:
        raise PlanValidationError(
            "Задачи должны идти по порядку P0.T0–P7.T17; "
            f"найдено: {', '.join(task_keys) or 'ничего'}"
        )

    task_statuses: dict[str, str] = {}
    for match in task_matches:
        status = match.group("status").strip()
        if status not in KNOWN_STATUSES:
            raise PlanValidationError(
                f"Неизвестный статус задачи {match.group('key')}: {status}"
            )
        task_statuses[match.group("key")] = status

    checkbox_matches = list(TASK_CHECKBOX_RE.finditer(content))
    checkbox_keys = [match.group("key") for match in checkbox_matches]
    if checkbox_keys != expected_keys:
        raise PlanValidationError(
            "У каждой задачи должен быть один checkbox в том же порядке"
        )
    for match in checkbox_matches:
        key = match.group("key")
        checked = match.group("checked").lower() == "x"
        done = task_statuses[key] == "✅ DONE"
        if checked != done:
            raise PlanValidationError(
                f"Checkbox {key} не совпадает со статусом {task_statuses[key]}"
            )

    phase_matches = list(PHASE_ROW_RE.finditer(content))
    phase_ids = [int(match.group("phase")) for match in phase_matches]
    expected_phases = list(TASKS_BY_PHASE)
    if phase_ids != expected_phases:
        raise PlanValidationError(
            f"Таблица прогресса должна содержать P0–P7; найдено: {phase_ids}"
        )

    phase_statuses: dict[int, str] = {}
    for match in phase_matches:
        phase = int(match.group("phase"))
        status = match.group("status").strip()
        if status not in KNOWN_STATUSES:
            raise PlanValidationError(
                f"Неизвестный статус фазы P{phase}: {status}"
            )
        phase_statuses[phase] = status

    evidence_targets = frozenset(
        match.group("target") for match in EVIDENCE_ROW_RE.finditer(content)
    )
    for key, status in task_statuses.items():
        if status == "✅ DONE" and key not in evidence_targets:
            raise PlanValidationError(f"Задача {key} отмечена DONE без evidence")

    for phase, status in phase_statuses.items():
        if status != "⬜ NOT_STARTED":
            unfinished_previous = [
                previous
                for previous in range(phase)
                if phase_statuses[previous] != "✅ DONE"
            ]
            if unfinished_previous:
                previous = unfinished_previous[0]
                raise PlanValidationError(
                    f"Запрещён перескок: P{phase} начата до Gate P{previous}"
                )
        if status == "✅ DONE":
            unfinished = [
                f"P{phase}.T{task}"
                for task in TASKS_BY_PHASE[phase]
                if task_statuses[f"P{phase}.T{task}"] != "✅ DONE"
            ]
            if unfinished:
                raise PlanValidationError(
                    f"Фаза P{phase} DONE, но задачи не завершены: {', '.join(unfinished)}"
                )
            if f"P{phase}" not in evidence_targets:
                raise PlanValidationError(f"Фаза P{phase} отмечена DONE без evidence")

    return PlanState(task_statuses, phase_statuses, evidence_targets)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Проверить структуру и статусы Plan.md")
    parser.add_argument("plan", nargs="?", default="Plan.md", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        content = args.plan.read_text(encoding="utf-8")
        state = validate_plan(content)
    except (OSError, UnicodeError, PlanValidationError) as error:
        print(f"PLAN_ERROR: {error}", file=sys.stderr)
        return 1
    print(
        f"PLAN_OK phases={len(state.phase_statuses)} tasks={len(state.task_statuses)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
