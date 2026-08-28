from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from check_plan import (
    PHASE_ROW_RE,
    TASKS_BY_PHASE,
    PlanValidationError,
    validate_plan,
)


def _escape_cell(value: str) -> str:
    compact = " ".join(value.split())
    if not compact:
        raise PlanValidationError("Evidence не может быть пустым")
    return compact.replace("|", r"\|")


def _base_commit(plan_path: Path) -> str:
    result = subprocess.run(
        ["git", "rev-parse", "--short", "HEAD"],
        cwd=plan_path.parent,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    return result.stdout.strip() if result.returncode == 0 else "—"


def _evidence_row(target: str, evidence: str, plan_path: Path) -> str:
    timestamp = datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
    return (
        f"| {timestamp} | {target} | {_base_commit(plan_path)}+worktree | "
        f"{_escape_cell(evidence)} | Plan.md | Нет |"
    )


def _append_evidence(content: str, row: str) -> str:
    placeholder = "| — | — | — | — | — | — |"
    if placeholder in content:
        return content.replace(placeholder, row, 1)
    journal_header = (
        "| UTC date | Task/Phase | Commit | Commands and result | "
        "Evidence artifact | Limitations |"
    )
    header_index = content.find(journal_header)
    if header_index < 0:
        raise PlanValidationError("В Plan.md не найден evidence journal")
    separator_end = content.find("\n", content.find("\n", header_index) + 1)
    if separator_end < 0:
        raise PlanValidationError("Повреждён заголовок evidence journal")
    return content[: separator_end + 1] + row + "\n" + content[separator_end + 1 :]


def _replace_phase_row(
    content: str, phase: int, *, fact: str, status: str, blockers: str
) -> str:
    for match in PHASE_ROW_RE.finditer(content):
        if int(match.group("phase")) != phase:
            continue
        replacement = (
            f"| P{phase} | {match.group('plan').strip()} | {_escape_cell(fact)} | "
            f"{status} | {_escape_cell(blockers)} |"
        )
        return content[: match.start()] + replacement + content[match.end() :]
    raise PlanValidationError(f"Не найдена строка прогресса P{phase}")


def _task_coordinates(target: str) -> tuple[int, int]:
    match = re.fullmatch(r"P(?P<phase>\d+)\.T(?P<task>\d+)", target)
    if match is None:
        raise PlanValidationError(f"Неверный task ID: {target}")
    phase = int(match.group("phase"))
    task = int(match.group("task"))
    if task not in TASKS_BY_PHASE.get(phase, ()):
        raise PlanValidationError(f"Неизвестная задача: {target}")
    return phase, task


def complete_task(content: str, target: str, evidence: str, plan_path: Path) -> str:
    state = validate_plan(content)
    phase, task = _task_coordinates(target)
    if state.task_statuses[target] == "✅ DONE":
        raise PlanValidationError(f"Задача {target} уже DONE")
    if task > 0:
        predecessor = next(
            (
                key
                for key in reversed(tuple(state.task_statuses))
                if int(key.split(".T", 1)[1]) < task
            ),
            None,
        )
        if predecessor and state.task_statuses[predecessor] != "✅ DONE":
            raise PlanValidationError(
                f"Нельзя завершить {target}: снача нужна {predecessor}"
            )

    heading = re.compile(
        rf"^(#### {re.escape(target)}\..+ — )[^\r\n]+$", re.MULTILINE
    )
    updated, heading_count = heading.subn(r"\g<1>✅ DONE", content, count=1)
    if heading_count != 1:
        raise PlanValidationError(f"Не найден heading {target}")
    checkbox = f"- [ ] **{target} завершена и имеет evidence.**"
    checked = f"- [x] **{target} завершена и имеет evidence.**"
    if checkbox not in updated:
        raise PlanValidationError(f"Не найден checkbox {target}")
    updated = updated.replace(checkbox, checked, 1)
    updated = _append_evidence(updated, _evidence_row(target, evidence, plan_path))

    completed = [
        key
        for key, status in validate_plan(updated).task_statuses.items()
        if key.startswith(f"P{phase}.") and status == "✅ DONE"
    ]
    updated = _replace_phase_row(
        updated,
        phase,
        fact=f"Выполнено: {', '.join(completed)}",
        status="🟡 IN_PROGRESS",
        blockers="Нет",
    )
    validate_plan(updated)
    return updated


def complete_phase(content: str, target: str, evidence: str, plan_path: Path) -> str:
    state = validate_plan(content)
    match = re.fullmatch(r"P(?P<phase>\d+)", target)
    if match is None:
        raise PlanValidationError(f"Неверный phase ID: {target}")
    phase = int(match.group("phase"))
    if phase not in TASKS_BY_PHASE:
        raise PlanValidationError(f"Неизвестная фаза: {target}")
    if phase > 0 and state.phase_statuses[phase - 1] != "✅ DONE":
        raise PlanValidationError(f"Нельзя закрыть {target} до Gate P{phase - 1}")
    unfinished = [
        f"P{phase}.T{task}"
        for task in TASKS_BY_PHASE[phase]
        if state.task_statuses[f"P{phase}.T{task}"] != "✅ DONE"
    ]
    if unfinished:
        raise PlanValidationError(f"Задачи не завершены: {', '.join(unfinished)}")

    updated = _append_evidence(content, _evidence_row(target, evidence, plan_path))
    updated = _replace_phase_row(
        updated, phase, fact=evidence, status="✅ DONE", blockers="Нет"
    )
    validate_plan(updated)
    return updated


def atomic_write(path: Path, content: str) -> None:
    path = path.resolve()
    temporary_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            temporary_name = temporary.name
            temporary.write(content)
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary_name, path)
    finally:
        if temporary_name and os.path.exists(temporary_name):
            os.unlink(temporary_name)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Атомарно обновить статус Plan.md")
    subparsers = parser.add_subparsers(dest="command", required=True)
    for command in ("complete-task", "complete-phase"):
        command_parser = subparsers.add_parser(command)
        command_parser.add_argument("target")
        command_parser.add_argument("--evidence", required=True)
        command_parser.add_argument("--plan", type=Path, default=Path("Plan.md"))
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        original = args.plan.read_text(encoding="utf-8")
        if args.command == "complete-task":
            updated = complete_task(original, args.target, args.evidence, args.plan)
        else:
            updated = complete_phase(original, args.target, args.evidence, args.plan)
        atomic_write(args.plan, updated)
    except (OSError, UnicodeError, PlanValidationError) as error:
        print(f"PLAN_UPDATE_ERROR: {error}", file=sys.stderr)
        return 1
    print(f"PLAN_UPDATED {args.target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
