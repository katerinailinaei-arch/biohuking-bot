from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, ClassVar


class DatasetValidationError(ValueError):
    """The frozen eval dataset is malformed or incomplete."""


@dataclass(frozen=True, repr=False)
class EvalCase:
    id: str
    category: str
    input: MappingProxyType[str, str]
    expected_schema: str
    hard_assertions: tuple[str, ...]
    blind_label: str


@dataclass(frozen=True)
class EvalDataset:
    version: str
    sha256: str
    cases: tuple[EvalCase, ...]

    REQUIRED_COVERAGE: ClassVar[frozenset[str]] = frozenset(
        {
            "claim_supported",
            "claim_refuted",
            "claim_insufficient",
            "claim_manual_review",
            "trap_numeric",
            "trap_causal",
            "trap_association",
            "source_unavailable",
            "source_prompt_injection",
            "edit_number",
            "edit_modality",
            "edit_population",
            "edit_action",
            "length_short",
            "length_medium",
            "length_long",
        }
    )

    @property
    def fixture_ids(self) -> tuple[str, ...]:
        return tuple(case.id for case in self.cases)

    @property
    def calibration_topics(self) -> frozenset[str]:
        return frozenset(
            case.input["topic"] for case in self.cases if case.category == "calibration"
        )

    @property
    def style_holdout_ids(self) -> tuple[str, ...]:
        return tuple(case.id for case in self.cases if case.category == "style_holdout")

    @property
    def coverage(self) -> frozenset[str]:
        return frozenset(assertion for case in self.cases for assertion in case.hard_assertions)


@dataclass(frozen=True)
class EvalCaseResult:
    fixture_id: str
    category: str
    blind_label: str
    schema_valid: bool
    blind_rating: float | None
    style_rating: float | None
    hard_violations: tuple[str, ...]
    latency_ms: int | None
    input_tokens: int | None
    output_tokens: int | None


@dataclass(frozen=True)
class EvalReport:
    dataset_version: str
    dataset_hash: str
    provider: str
    model: str
    prompt_version: str
    schema_version: str
    model_score: float | None
    style_score: float | None
    safety_score: float | None
    results: tuple[EvalCaseResult, ...]

    def __post_init__(self) -> None:
        for value in (
            self.dataset_version,
            self.provider,
            self.model,
            self.prompt_version,
            self.schema_version,
        ):
            if _SAFE_METADATA.fullmatch(value) is None or _SECRET_PREFIX.match(value):
                raise ValueError("safe report metadata is required")
        if _SHA256.fullmatch(self.dataset_hash) is None:
            raise ValueError("safe report metadata is required")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(
            self.to_dict(),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.to_json().encode("utf-8")).hexdigest()


_DATASET_VERSION = "bodrye-eval-v1"
_FIELDS = {"id", "category", "input", "expected_schema", "hard_assertions", "blind_label"}
_SAFE_METADATA = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,127}$")
_SECRET_PREFIX = re.compile(r"^(?:sk|gsk|xox[baprs])[-_]", re.IGNORECASE)
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def load_dataset(path: Path) -> EvalDataset:
    try:
        raw = path.read_bytes()
        text = raw.decode("utf-8")
    except (OSError, UnicodeError) as error:
        raise DatasetValidationError("dataset cannot be read") from error

    cases: list[EvalCase] = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as error:
            raise DatasetValidationError(f"invalid JSON at line {line_number}") from error
        cases.append(_parse_case(value, line_number))

    ids = [case.id for case in cases]
    if len(ids) != len(set(ids)):
        raise DatasetValidationError("fixture IDs must be unique")

    dataset = EvalDataset(
        version=_DATASET_VERSION,
        sha256=hashlib.sha256(raw).hexdigest(),
        cases=tuple(cases),
    )
    _validate_coverage(dataset)
    return dataset


def _parse_case(value: object, line_number: int) -> EvalCase:
    if not isinstance(value, dict):
        raise DatasetValidationError(f"line {line_number} must be an object")
    fields = set(value)
    if fields != _FIELDS:
        raise DatasetValidationError(f"line {line_number} has missing or extra fields")

    identifier = _required_string(value["id"], "id", line_number)
    category = _required_string(value["category"], "category", line_number)
    if category not in {"calibration", "style_holdout"}:
        raise DatasetValidationError(f"line {line_number} has invalid category")
    raw_input = value["input"]
    if not isinstance(raw_input, dict) or not raw_input:
        raise DatasetValidationError(f"line {line_number} input must be a non-empty object")
    safe_input: dict[str, str] = {}
    for key, item in raw_input.items():
        if not isinstance(key, str) or not key or not isinstance(item, str) or not item:
            raise DatasetValidationError(f"line {line_number} input values must be strings")
        safe_input[key] = item
    if "topic" not in safe_input or "text" not in safe_input:
        raise DatasetValidationError(f"line {line_number} input needs topic and text")

    raw_assertions = value["hard_assertions"]
    if not isinstance(raw_assertions, list) or any(
        not isinstance(item, str) or not item for item in raw_assertions
    ):
        raise DatasetValidationError(f"line {line_number} hard_assertions must be strings")
    if len(raw_assertions) != len(set(raw_assertions)):
        raise DatasetValidationError(f"line {line_number} hard_assertions must be unique")

    expected_schema = _required_string(value["expected_schema"], "expected_schema", line_number)
    if expected_schema not in {
        "claims-medical-v2",
        "evidence-medical-v2",
        "change-v1",
        "draft-v1",
    }:
        raise DatasetValidationError(f"line {line_number} has invalid expected schema")

    return EvalCase(
        id=identifier,
        category=category,
        input=MappingProxyType(safe_input),
        expected_schema=expected_schema,
        hard_assertions=tuple(raw_assertions),
        blind_label=_required_string(value["blind_label"], "blind_label", line_number),
    )


def _required_string(value: object, field: str, line_number: int) -> str:
    if not isinstance(value, str) or not value.strip():
        raise DatasetValidationError(f"line {line_number} field {field} must be a string")
    return value


def _validate_coverage(dataset: EvalDataset) -> None:
    topic_count = len(dataset.calibration_topics)
    if not 8 <= topic_count <= 10:
        raise DatasetValidationError("dataset needs 8-10 unique calibration topics")
    if len(dataset.style_holdout_ids) != 3:
        raise DatasetValidationError("dataset needs exactly 3 style holdouts")
    missing = dataset.REQUIRED_COVERAGE - dataset.coverage
    if missing:
        raise DatasetValidationError(f"dataset coverage is incomplete: {sorted(missing)!r}")


def valid_score(value: float | None, *, maximum: float) -> bool:
    return value is not None and math.isfinite(value) and 0.0 <= value <= maximum


__all__ = [
    "DatasetValidationError",
    "EvalCase",
    "EvalCaseResult",
    "EvalDataset",
    "EvalReport",
    "load_dataset",
    "valid_score",
]
