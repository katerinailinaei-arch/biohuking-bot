from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import TypeVar
from uuid import uuid4

from pydantic import ValidationError

from bodrye_bot.domain.errors import SafeError, SafeErrorCode
from bodrye_bot.operations.usage import UsageRecord
from bodrye_bot.ports.llm import (
    AnglesRequest,
    AnglesResponse,
    ChangeRequest,
    ChangeResponse,
    ClaimsRequest,
    ClaimsResponse,
    DraftRequest,
    DraftResponse,
    EvidenceRequest,
    EvidenceResponse,
    ExtractRequest,
    ExtractResponse,
    Jitter,
    LLMTransport,
    ProviderHealth,
    RequestContext,
    Sleep,
    StrictModel,
    StyleInferenceRequest,
    StyleInferenceResponse,
    TransportRequest,
    TransportResponse,
    UsageReport,
)

ResponseT = TypeVar("ResponseT", bound=StrictModel)


async def _default_sleep(delay: float) -> None:
    await asyncio.sleep(delay)


def _default_jitter(attempt: int) -> float:
    return min(float(attempt), 2.0)


@dataclass
class _UsageAccumulator:
    attempts: int = 0
    latency_total: int = 0
    input_total: int = 0
    output_total: int = 0
    latency_known: bool = True
    input_known: bool = True
    output_known: bool = True
    final_request_id: str | None = None

    def observe(self, response: TransportResponse) -> None:
        self.attempts += 1
        self.final_request_id = response.request_id
        self.latency_total, self.latency_known = self._add(
            self.latency_total, self.latency_known, response.latency_ms
        )
        self.input_total, self.input_known = self._add(
            self.input_total, self.input_known, response.input_tokens
        )
        self.output_total, self.output_known = self._add(
            self.output_total, self.output_known, response.output_tokens
        )

    def observe_unknown_attempt(self) -> None:
        self.attempts += 1
        self.final_request_id = None
        self.latency_known = False
        self.input_known = False
        self.output_known = False

    @staticmethod
    def _add(total: int, all_known: bool, value: int | None) -> tuple[int, bool]:
        if value is None:
            return total, False
        return total + value, all_known

    def latency(self) -> int | None:
        return self.latency_total if self.attempts > 0 and self.latency_known else None

    def input_tokens(self) -> int | None:
        return self.input_total if self.attempts > 0 and self.input_known else None

    def output_tokens(self) -> int | None:
        return self.output_total if self.attempts > 0 and self.output_known else None


class BaseLLMProvider:
    connect_timeout_seconds = 5
    total_timeout_seconds = 60
    max_retries = 2

    def __init__(
        self,
        *,
        transport: LLMTransport,
        provider_name: str,
        model: str,
        sleep: Sleep = _default_sleep,
        jitter: Jitter = _default_jitter,
    ) -> None:
        self._transport = transport
        self.provider_name = provider_name
        self.model = model
        self._sleep = sleep
        self._jitter = jitter
        self._quota_exhausted = False
        self._usage: dict[str, UsageRecord] = {}

    async def extract(self, request: ExtractRequest) -> ExtractResponse:
        return await self._invoke("extract", request, ExtractResponse)

    async def classify_claims(self, request: ClaimsRequest) -> ClaimsResponse:
        return await self._invoke("classify_claims", request, ClaimsResponse)

    async def synthesize_evidence(self, request: EvidenceRequest) -> EvidenceResponse:
        return await self._invoke("synthesize_evidence", request, EvidenceResponse)

    async def propose_angles(self, request: AnglesRequest) -> AnglesResponse:
        return await self._invoke("propose_angles", request, AnglesResponse)

    async def generate_draft(self, request: DraftRequest) -> DraftResponse:
        return await self._invoke("generate_draft", request, DraftResponse)

    async def assess_change(self, request: ChangeRequest) -> ChangeResponse:
        return await self._invoke("assess_change", request, ChangeResponse)

    async def infer_style_candidates(
        self, request: StyleInferenceRequest
    ) -> StyleInferenceResponse:
        return await self._invoke("infer_style_candidates", request, StyleInferenceResponse)

    async def healthcheck(self) -> ProviderHealth:
        return ProviderHealth(
            available=not self._quota_exhausted,
            provider=self.provider_name,
            model=self.model,
        )

    async def estimate_or_report_usage(self, response_id: str) -> UsageReport:
        try:
            return self._usage[response_id].to_report()
        except KeyError:
            raise SafeError.for_code(
                SafeErrorCode.INTERNAL_ERROR,
                developer_detail="provider_error_class=usage_not_found",
            ) from None

    async def _invoke(
        self,
        operation: str,
        request: RequestContext,
        response_type: type[ResponseT],
    ) -> ResponseT:
        trace_id = uuid4().hex
        usage = _UsageAccumulator()
        if self._quota_exhausted:
            raise self._failure(
                SafeErrorCode.LLM_QUOTA_EXHAUSTED,
                "quota_exhausted",
                operation,
                request,
                trace_id,
                usage,
            )

        response = await self._send_with_retries(
            operation,
            request,
            trace_id=trace_id,
            usage=usage,
            schema_repair=False,
        )
        if response.refusal:
            raise self._failure(
                SafeErrorCode.LLM_INVALID_OUTPUT,
                "refusal",
                operation,
                request,
                trace_id,
                usage,
            )

        try:
            result = self._validate_response(response, response_type, trace_id)
        except (json.JSONDecodeError, ValidationError, TypeError) as first_error:
            if self._must_block_without_repair(first_error) or self._contains_conservative_marker(
                response
            ):
                raise self._failure(
                    SafeErrorCode.LLM_INVALID_OUTPUT,
                    "schema_invalid",
                    operation,
                    request,
                    trace_id,
                    usage,
                ) from None
            repaired = await self._send_with_retries(
                operation,
                request,
                trace_id=trace_id,
                usage=usage,
                schema_repair=True,
            )
            if repaired.refusal:
                raise self._failure(
                    SafeErrorCode.LLM_INVALID_OUTPUT,
                    "refusal",
                    operation,
                    request,
                    trace_id,
                    usage,
                ) from None
            try:
                result = self._validate_response(repaired, response_type, trace_id)
            except (json.JSONDecodeError, ValidationError, TypeError):
                raise self._failure(
                    SafeErrorCode.LLM_INVALID_OUTPUT,
                    "schema_invalid_after_repair",
                    operation,
                    request,
                    trace_id,
                    usage,
                ) from None

        response_id = result.model_dump().get("response_id")
        assert isinstance(response_id, str)
        self._record_usage(
            response_id,
            operation,
            request,
            usage,
            "succeeded",
            None,
        )
        return result

    async def _send_with_retries(
        self,
        operation: str,
        request: RequestContext,
        *,
        trace_id: str,
        usage: _UsageAccumulator,
        schema_repair: bool,
    ) -> TransportResponse:
        for attempt in range(self.max_retries + 1):
            try:
                response = await self._transport.complete(
                    TransportRequest(
                        operation=operation,
                        model=self.model,
                        payload=request.model_dump(mode="json"),
                        schema_repair=schema_repair,
                        connect_timeout_seconds=self.connect_timeout_seconds,
                        total_timeout_seconds=self.total_timeout_seconds,
                    )
                )
            except TimeoutError:
                usage.observe_unknown_attempt()
                raise self._failure(
                    SafeErrorCode.LLM_TIMEOUT,
                    "timeout",
                    operation,
                    request,
                    trace_id,
                    usage,
                ) from None
            except Exception:
                usage.observe_unknown_attempt()
                raise self._failure(
                    SafeErrorCode.LLM_UNAVAILABLE,
                    "transport_error",
                    operation,
                    request,
                    trace_id,
                    usage,
                ) from None

            usage.observe(response)
            if 200 <= response.status_code < 300:
                return response
            if response.status_code == 429:
                remaining = self._remaining_quota(response.headers)
                if remaining is not None and remaining <= 0:
                    self._quota_exhausted = True
                    raise self._failure(
                        SafeErrorCode.LLM_QUOTA_EXHAUSTED,
                        "quota_exhausted",
                        operation,
                        request,
                        trace_id,
                        usage,
                    )
                if remaining is None:
                    raise self._failure(
                        SafeErrorCode.LLM_RATE_LIMIT,
                        "rate_limit_unknown_quota",
                        operation,
                        request,
                        trace_id,
                        usage,
                    )
                retryable = True
            else:
                retryable = response.status_code >= 500
            if retryable and attempt < self.max_retries:
                await self._sleep(self._jitter(attempt + 1))
                continue
            code = (
                SafeErrorCode.LLM_RATE_LIMIT
                if response.status_code == 429
                else SafeErrorCode.LLM_UNAVAILABLE
            )
            error_class = (
                "rate_limit" if response.status_code == 429 else f"http_{response.status_code}"
            )
            raise self._failure(
                code,
                error_class,
                operation,
                request,
                trace_id,
                usage,
            )
        raise AssertionError("retry loop must return or raise")

    @staticmethod
    def _remaining_quota(headers: Mapping[str, str]) -> int | None:
        values = [
            value
            for key, value in headers.items()
            if key.casefold() == "x-ratelimit-remaining-tokens"
        ]
        if len(values) != 1:
            return None
        try:
            return int(values[0])
        except ValueError:
            return None

    @staticmethod
    def _validate_response(
        response: TransportResponse,
        response_type: type[ResponseT],
        trace_id: str,
    ) -> ResponseT:
        body = response.json_body
        if body is None:
            body = json.loads(response.text_body or "")
        if not isinstance(body, Mapping):
            raise TypeError("provider output must be a JSON object")
        normalized = dict(body)
        normalized["response_id"] = trace_id
        return response_type.model_validate_json(json.dumps(normalized))

    @staticmethod
    def _must_block_without_repair(
        error: json.JSONDecodeError | ValidationError | TypeError,
    ) -> bool:
        if isinstance(error, json.JSONDecodeError | TypeError):
            return False
        unsafe_types = {"extra_forbidden", "enum", "bool_parsing", "bool_type"}
        return any(item["type"] in unsafe_types for item in error.errors())

    @classmethod
    def _contains_conservative_marker(cls, response: TransportResponse) -> bool:
        body = response.json_body
        if body is None:
            try:
                body = json.loads(response.text_body or "")
            except (json.JSONDecodeError, TypeError):
                return False
        return cls._value_contains_conservative_marker(body)

    @classmethod
    def _value_contains_conservative_marker(cls, value: object) -> bool:
        if isinstance(value, Mapping):
            for raw_key, nested in value.items():
                key = str(raw_key).casefold()
                if key == "medical_uncertainty" and nested is True:
                    return True
                if (
                    key
                    in {
                        "manual_review",
                        "requires_manual_review",
                        "uncertain",
                        "uncertainty",
                    }
                    and nested is True
                ):
                    return True
                if key in {"verdict", "review_status", "status"} and isinstance(nested, str):
                    if nested.casefold() in {
                        "insufficient",
                        "manual_review",
                        "uncertain",
                    }:
                        return True
                if cls._value_contains_conservative_marker(nested):
                    return True
            return False
        if isinstance(value, list | tuple):
            return any(cls._value_contains_conservative_marker(item) for item in value)
        return False

    def _failure(
        self,
        code: SafeErrorCode,
        error_class: str,
        operation: str,
        request: RequestContext,
        trace_id: str,
        usage: _UsageAccumulator,
    ) -> SafeError:
        error = replace(
            SafeError.for_code(
                code,
                developer_detail=f"provider_error_class={error_class}",
            ),
            trace_id=trace_id,
        )
        self._record_usage(
            trace_id,
            operation,
            request,
            usage,
            "failed",
            error_class,
        )
        return error

    def _record_usage(
        self,
        key: str,
        operation: str,
        request: RequestContext,
        usage: _UsageAccumulator,
        status: str,
        error_class: str | None,
    ) -> None:
        self._usage[key] = UsageRecord(
            owner_id=request.owner_id,
            workflow_id=request.workflow_id,
            operation=operation,
            provider=self.provider_name,
            model=self.model,
            status=status,
            prompt_version=request.prompt_version,
            schema_version=request.schema_version,
            provider_request_id=usage.final_request_id,
            latency_ms=usage.latency(),
            input_tokens=usage.input_tokens(),
            output_tokens=usage.output_tokens(),
            error_class=error_class,
            trace_id=key,
        )


__all__ = ["BaseLLMProvider"]
