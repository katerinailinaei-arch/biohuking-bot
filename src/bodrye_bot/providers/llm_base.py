from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping
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
        except KeyError as exc:
            raise SafeError.for_code(
                SafeErrorCode.INTERNAL_ERROR,
                developer_detail="provider_error_class=usage_not_found",
            ) from exc

    async def _invoke(
        self,
        operation: str,
        request: RequestContext,
        response_type: type[ResponseT],
    ) -> ResponseT:
        if self._quota_exhausted:
            raise self._failure(
                SafeErrorCode.LLM_QUOTA_EXHAUSTED,
                "quota_exhausted",
                operation,
                request,
            )

        response = await self._send_with_retries(operation, request, schema_repair=False)
        if response.refusal:
            raise self._failure(
                SafeErrorCode.LLM_INVALID_OUTPUT, "refusal", operation, request, response
            )

        try:
            result = self._validate_response(response, response_type)
        except (json.JSONDecodeError, ValidationError) as first_error:
            if self._must_block_without_repair(first_error):
                raise self._failure(
                    SafeErrorCode.LLM_INVALID_OUTPUT,
                    "schema_invalid",
                    operation,
                    request,
                    response,
                ) from first_error
            repaired = await self._send_with_retries(operation, request, schema_repair=True)
            if repaired.refusal:
                raise self._failure(
                    SafeErrorCode.LLM_INVALID_OUTPUT,
                    "refusal",
                    operation,
                    request,
                    repaired,
                ) from first_error
            try:
                result = self._validate_response(repaired, response_type)
            except (json.JSONDecodeError, ValidationError) as second_error:
                raise self._failure(
                    SafeErrorCode.LLM_INVALID_OUTPUT,
                    "schema_invalid_after_repair",
                    operation,
                    request,
                    repaired,
                ) from second_error
            response = repaired

        response_id = result.model_dump().get("response_id")
        assert isinstance(response_id, str)
        self._record_usage(response_id, operation, request, response, "succeeded", None)
        return result

    async def _send_with_retries(
        self, operation: str, request: RequestContext, *, schema_repair: bool
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
            except TimeoutError as exc:
                raise self._failure(
                    SafeErrorCode.LLM_TIMEOUT, "timeout", operation, request
                ) from exc

            if 200 <= response.status_code < 300:
                return response
            if response.status_code == 429 and self._quota_is_exhausted(response.headers):
                self._quota_exhausted = True
                raise self._failure(
                    SafeErrorCode.LLM_QUOTA_EXHAUSTED,
                    "quota_exhausted",
                    operation,
                    request,
                    response,
                )
            retryable = response.status_code == 429 or response.status_code >= 500
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
            raise self._failure(code, error_class, operation, request, response)
        raise AssertionError("retry loop must return or raise")

    @staticmethod
    def _quota_is_exhausted(headers: Mapping[str, str]) -> bool:
        raw = headers.get("x-ratelimit-remaining-tokens")
        if raw is None:
            return False
        try:
            return int(raw) <= 0
        except ValueError:
            return False

    @staticmethod
    def _validate_response(
        response: TransportResponse, response_type: type[ResponseT]
    ) -> ResponseT:
        body = response.json_body
        if body is None:
            body = json.loads(response.text_body or "")
        if not isinstance(body, Mapping):
            raise ValidationError.from_exception_data(response_type.__name__, [])
        normalized = dict(body)
        normalized["response_id"] = response.request_id or uuid4().hex
        return response_type.model_validate(normalized)

    @staticmethod
    def _must_block_without_repair(error: json.JSONDecodeError | ValidationError) -> bool:
        if isinstance(error, json.JSONDecodeError):
            return False
        unsafe_types = {"extra_forbidden", "enum", "bool_parsing", "bool_type"}
        return any(item["type"] in unsafe_types for item in error.errors())

    def _failure(
        self,
        code: SafeErrorCode,
        error_class: str,
        operation: str,
        request: RequestContext,
        response: TransportResponse | None = None,
    ) -> SafeError:
        error = SafeError.for_code(code, developer_detail=f"provider_error_class={error_class}")
        self._record_usage(error.trace_id, operation, request, response, "failed", error_class)
        return error

    def _record_usage(
        self,
        key: str,
        operation: str,
        request: RequestContext,
        response: TransportResponse | None,
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
            provider_request_id=response.request_id if response else None,
            latency_ms=response.latency_ms if response else None,
            input_tokens=response.input_tokens if response else None,
            output_tokens=response.output_tokens if response else None,
            error_class=error_class,
            trace_id=key,
        )


__all__ = ["BaseLLMProvider"]
