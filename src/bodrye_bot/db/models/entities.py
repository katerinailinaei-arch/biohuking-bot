from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import (
    ARRAY,
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKeyConstraint,
    Index,
    Integer,
    LargeBinary,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from bodrye_bot.db.base import Base, MutableRecord, OwnedRecord
from bodrye_bot.domain.workflow import Actor, WorkflowStatus

UUID_TYPE = Uuid(as_uuid=True)
TIMESTAMP = DateTime(timezone=True)
WORKFLOW_STATUS = Enum(
    WorkflowStatus,
    name="workflow_status",
    values_callable=lambda enum_class: [member.value for member in enum_class],
    validate_strings=True,
)
ACTOR = Enum(
    Actor,
    name="actor",
    values_callable=lambda enum_class: [member.value for member in enum_class],
    validate_strings=True,
)


class Source(OwnedRecord, MutableRecord, Base):
    __tablename__ = "sources"
    __table_args__ = (
        UniqueConstraint("id", "owner_id", name="uq_source_id_owner"),
        UniqueConstraint("owner_id", "canonical_url", name="uq_source_owner_url"),
        CheckConstraint("cardinality(roles) <= 16", name="source_roles_bounded"),
        CheckConstraint(
            "octet_length(config_json::text) <= 65536",
            name="source_config_bounded",
        ),
    )

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    canonical_url: Mapped[str] = mapped_column(String(2048), nullable=False)
    source_type: Mapped[str] = mapped_column(String(64), nullable=False)
    roles: Mapped[list[str]] = mapped_column(ARRAY(String(64)), nullable=False)
    access_method: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    checked_at: Mapped[datetime | None] = mapped_column(TIMESTAMP)
    failure_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    license_note: Mapped[str | None] = mapped_column(Text)
    config_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )


class SourceDocument(OwnedRecord, Base):
    __tablename__ = "source_documents"
    __table_args__ = (
        UniqueConstraint("id", "owner_id", name="uq_source_document_id_owner"),
        ForeignKeyConstraint(
            ["source_id", "owner_id"],
            ["sources.id", "sources.owner_id"],
            name="fk_source_document_source_owner",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "content_hash ~ '^[0-9a-f]{64}$'", name="source_document_hash_sha256"
        ),
        CheckConstraint(
            "bounded_excerpt IS NULL OR char_length(bounded_excerpt) <= 65536",
            name="source_document_excerpt_bounded",
        ),
        CheckConstraint(
            "octet_length(http_metadata::text) <= 65536",
            name="source_http_metadata_bounded",
        ),
        CheckConstraint(
            "raw_expires_at IS NULL OR raw_expires_at <= fetched_at + interval '24 hours'",
            name="source_document_raw_expiry_24h",
        ),
    )

    source_id: Mapped[UUID] = mapped_column(UUID_TYPE, nullable=False)
    url: Mapped[str] = mapped_column(String(2048), nullable=False)
    title: Mapped[str | None] = mapped_column(String(1000))
    published_at: Mapped[datetime | None] = mapped_column(TIMESTAMP)
    fetched_at: Mapped[datetime] = mapped_column(TIMESTAMP, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    bounded_excerpt: Mapped[str | None] = mapped_column(Text)
    raw_expires_at: Mapped[datetime | None] = mapped_column(TIMESTAMP)
    fetch_status: Mapped[str] = mapped_column(String(32), nullable=False)
    http_metadata: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )


class SourcePayloadCache(OwnedRecord, Base):
    __tablename__ = "source_payload_cache"
    __table_args__ = (
        UniqueConstraint("id", "owner_id", name="uq_source_payload_id_owner"),
        UniqueConstraint(
            "source_document_id", name="uq_source_payload_document"
        ),
        ForeignKeyConstraint(
            ["source_document_id", "owner_id"],
            ["source_documents.id", "source_documents.owner_id"],
            name="fk_source_payload_document_owner",
            ondelete="CASCADE",
        ),
        CheckConstraint(
            "expires_at <= fetched_at + interval '24 hours'",
            name="source_payload_expiry_24h",
        ),
        CheckConstraint(
            "octet_length(payload) <= 10485760", name="source_payload_max_10mib"
        ),
    )

    source_document_id: Mapped[UUID] = mapped_column(UUID_TYPE, nullable=False)
    payload: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    fetched_at: Mapped[datetime] = mapped_column(TIMESTAMP, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(TIMESTAMP, nullable=False)


class DigestItem(OwnedRecord, MutableRecord, Base):
    __tablename__ = "digest_items"
    __table_args__ = (
        UniqueConstraint("id", "owner_id", name="uq_digest_item_id_owner"),
        ForeignKeyConstraint(
            ["source_document_id", "owner_id"],
            ["source_documents.id", "source_documents.owner_id"],
            name="fk_digest_document_owner",
            ondelete="CASCADE",
        ),
        CheckConstraint(
            "octet_length(score_components::text) <= 32768",
            name="digest_score_components_bounded",
        ),
    )

    source_document_id: Mapped[UUID] = mapped_column(UUID_TYPE, nullable=False)
    topic_fingerprint: Mapped[str] = mapped_column(String(128), nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    rubric: Mapped[str] = mapped_column(String(128), nullable=False)
    audience_reason: Mapped[str] = mapped_column(Text, nullable=False)
    selection_reason: Mapped[str] = mapped_column(Text, nullable=False)
    preliminary_risk: Mapped[str] = mapped_column(String(32), nullable=False)
    score_components: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    digest_date: Mapped[datetime] = mapped_column(TIMESTAMP, nullable=False)
    disposition: Mapped[str] = mapped_column(String(32), nullable=False)


class ContentWorkflow(OwnedRecord, MutableRecord, Base):
    __tablename__ = "content_workflows"
    __table_args__ = (
        UniqueConstraint("id", "owner_id", name="uq_workflow_id_owner"),
        CheckConstraint("version >= 1", name="workflow_version_positive"),
        ForeignKeyConstraint(
            ["selected_angle_id", "id", "owner_id"],
            ["angles.id", "angles.workflow_id", "angles.owner_id"],
            name="fk_workflow_selected_angle_owner",
            ondelete="RESTRICT",
            use_alter=True,
        ),
        ForeignKeyConstraint(
            ["current_version_id", "id", "owner_id"],
            [
                "draft_versions.id",
                "draft_versions.workflow_id",
                "draft_versions.owner_id",
            ],
            name="fk_workflow_current_draft_owner",
            ondelete="RESTRICT",
            use_alter=True,
        ),
    )

    origin_type: Mapped[str] = mapped_column(String(32), nullable=False)
    origin_id: Mapped[UUID | None] = mapped_column(UUID_TYPE)
    status: Mapped[WorkflowStatus] = mapped_column(WORKFLOW_STATUS, nullable=False)
    selected_angle_id: Mapped[UUID | None] = mapped_column(UUID_TYPE)
    recommended_format: Mapped[str] = mapped_column(String(16), nullable=False)
    current_version_id: Mapped[UUID | None] = mapped_column(UUID_TYPE)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class Angle(OwnedRecord, Base):
    __tablename__ = "angles"
    __table_args__ = (
        UniqueConstraint("id", "owner_id", name="uq_angle_id_owner"),
        UniqueConstraint(
            "id", "workflow_id", "owner_id", name="uq_angle_id_workflow_owner"
        ),
        ForeignKeyConstraint(
            ["workflow_id", "owner_id"],
            ["content_workflows.id", "content_workflows.owner_id"],
            name="fk_angle_workflow_owner",
            ondelete="CASCADE",
        ),
    )

    workflow_id: Mapped[UUID] = mapped_column(UUID_TYPE, nullable=False)
    angle_type: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    hook: Mapped[str] = mapped_column(Text, nullable=False)
    promise: Mapped[str] = mapped_column(Text, nullable=False)
    tone_note: Mapped[str] = mapped_column(Text, nullable=False)
    selected_at: Mapped[datetime | None] = mapped_column(TIMESTAMP)


class ProviderRun(OwnedRecord, MutableRecord, Base):
    __tablename__ = "provider_runs"
    __table_args__ = (
        UniqueConstraint("id", "owner_id", name="uq_provider_run_id_owner"),
        ForeignKeyConstraint(
            ["workflow_id", "owner_id"],
            ["content_workflows.id", "content_workflows.owner_id"],
            name="fk_provider_run_workflow_owner",
            ondelete="SET NULL (workflow_id)",
        ),
        CheckConstraint(
            "input_tokens IS NULL OR input_tokens >= 0",
            name="provider_input_tokens_nonnegative",
        ),
        CheckConstraint(
            "output_tokens IS NULL OR output_tokens >= 0",
            name="provider_output_tokens_nonnegative",
        ),
    )

    workflow_id: Mapped[UUID | None] = mapped_column(UUID_TYPE)
    operation: Mapped[str] = mapped_column(String(64), nullable=False)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    model: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(64), nullable=False)
    schema_version: Mapped[str] = mapped_column(String(64), nullable=False)
    provider_request_id: Mapped[str | None] = mapped_column(String(255))
    input_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    error_class: Mapped[str | None] = mapped_column(String(128))


class DraftVersion(OwnedRecord, Base):
    __tablename__ = "draft_versions"
    __table_args__ = (
        UniqueConstraint("id", "owner_id", name="uq_draft_id_owner"),
        UniqueConstraint(
            "id", "workflow_id", "owner_id", name="uq_draft_id_workflow_owner"
        ),
        UniqueConstraint(
            "workflow_id", "version_number", name="uq_draft_workflow_version"
        ),
        ForeignKeyConstraint(
            ["workflow_id", "owner_id"],
            ["content_workflows.id", "content_workflows.owner_id"],
            name="fk_draft_workflow_owner",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["created_by_run_id", "owner_id"],
            ["provider_runs.id", "provider_runs.owner_id"],
            name="fk_draft_provider_run_owner",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["supersedes_id", "workflow_id", "owner_id"],
            ["draft_versions.id", "draft_versions.workflow_id", "draft_versions.owner_id"],
            name="fk_draft_supersedes_owner",
            ondelete="RESTRICT",
        ),
        CheckConstraint("version_number >= 1", name="draft_version_positive"),
        CheckConstraint("char_length(body) <= 3800", name="draft_body_max_3800"),
        CheckConstraint("body_hash ~ '^[0-9a-f]{64}$'", name="draft_hash_sha256"),
        CheckConstraint("cardinality(headlines) <= 3", name="draft_headlines_max_three"),
        CheckConstraint(
            "cardinality(public_sources) <= 3", name="draft_public_sources_max_three"
        ),
    )

    workflow_id: Mapped[UUID] = mapped_column(UUID_TYPE, nullable=False)
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    body_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    format: Mapped[str] = mapped_column(String(16), nullable=False)
    headlines: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False)
    public_sources: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False)
    style_profile_version: Mapped[int] = mapped_column(Integer, nullable=False)
    created_by_run_id: Mapped[UUID | None] = mapped_column(UUID_TYPE)
    supersedes_id: Mapped[UUID | None] = mapped_column(UUID_TYPE)


class Claim(OwnedRecord, MutableRecord, Base):
    __tablename__ = "claims"
    __table_args__ = (
        UniqueConstraint("id", "owner_id", name="uq_claim_id_owner"),
        UniqueConstraint(
            "id", "workflow_id", "owner_id", name="uq_claim_id_workflow_owner"
        ),
        ForeignKeyConstraint(
            ["workflow_id", "owner_id"],
            ["content_workflows.id", "content_workflows.owner_id"],
            name="fk_claim_workflow_owner",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["draft_version_id", "workflow_id", "owner_id"],
            ["draft_versions.id", "draft_versions.workflow_id", "draft_versions.owner_id"],
            name="fk_claim_draft_owner",
            ondelete="CASCADE",
        ),
    )

    workflow_id: Mapped[UUID] = mapped_column(UUID_TYPE, nullable=False)
    draft_version_id: Mapped[UUID | None] = mapped_column(UUID_TYPE)
    exact_text: Mapped[str] = mapped_column(Text, nullable=False)
    claim_type: Mapped[str] = mapped_column(String(32), nullable=False)
    population: Mapped[str | None] = mapped_column(Text)
    context: Mapped[str | None] = mapped_column(Text)
    is_medical: Mapped[bool] = mapped_column(Boolean, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)


class Evidence(OwnedRecord, Base):
    __tablename__ = "evidence"
    __table_args__ = (
        UniqueConstraint("id", "owner_id", name="uq_evidence_id_owner"),
        ForeignKeyConstraint(
            ["claim_id", "owner_id"],
            ["claims.id", "claims.owner_id"],
            name="fk_evidence_claim_owner",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["source_document_id", "owner_id"],
            ["source_documents.id", "source_documents.owner_id"],
            name="fk_evidence_document_owner",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["review_model_run_id", "owner_id"],
            ["provider_runs.id", "provider_runs.owner_id"],
            name="fk_evidence_provider_run_owner",
            ondelete="RESTRICT",
        ),
        CheckConstraint("excerpt_hash ~ '^[0-9a-f]{64}$'", name="evidence_hash_sha256"),
        CheckConstraint(
            "char_length(exact_excerpt) <= 65536", name="evidence_excerpt_bounded"
        ),
    )

    claim_id: Mapped[UUID] = mapped_column(UUID_TYPE, nullable=False)
    source_document_id: Mapped[UUID] = mapped_column(UUID_TYPE, nullable=False)
    verdict: Mapped[str] = mapped_column(String(32), nullable=False)
    risk: Mapped[str] = mapped_column(String(16), nullable=False)
    exact_excerpt: Mapped[str] = mapped_column(Text, nullable=False)
    excerpt_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    applicability: Mapped[str] = mapped_column(Text, nullable=False)
    limitations: Mapped[str] = mapped_column(Text, nullable=False)
    reviewed_at: Mapped[datetime] = mapped_column(TIMESTAMP, nullable=False)
    review_model_run_id: Mapped[UUID | None] = mapped_column(UUID_TYPE)


class ReviewDecision(OwnedRecord, Base):
    __tablename__ = "review_decisions"
    __table_args__ = (
        UniqueConstraint("id", "owner_id", name="uq_review_decision_id_owner"),
        ForeignKeyConstraint(
            ["draft_version_id", "owner_id"],
            ["draft_versions.id", "draft_versions.owner_id"],
            name="fk_review_draft_owner",
            ondelete="CASCADE",
        ),
        CheckConstraint(
            "cardinality(blocking_reasons) <= 64",
            name="review_blocking_reasons_bounded",
        ),
        CheckConstraint(
            "cardinality(changed_claim_ids) <= 256",
            name="review_changed_claims_bounded",
        ),
        CheckConstraint(
            "status IN ('pending', 'passed', 'blocked', 'review_incomplete')",
            name="review_status_known",
        ),
    )

    draft_version_id: Mapped[UUID] = mapped_column(UUID_TYPE, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    blocking_reasons: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False)
    changed_claim_ids: Mapped[list[UUID]] = mapped_column(ARRAY(UUID_TYPE), nullable=False)
    reviewed_at: Mapped[datetime] = mapped_column(TIMESTAMP, nullable=False)
    policy_version: Mapped[str] = mapped_column(String(64), nullable=False)


class Approval(OwnedRecord, MutableRecord, Base):
    __tablename__ = "approvals"
    __table_args__ = (
        UniqueConstraint("id", "owner_id", name="uq_approval_id_owner"),
        UniqueConstraint(
            "id",
            "draft_version_id",
            "owner_id",
            name="uq_approval_id_draft_owner",
        ),
        ForeignKeyConstraint(
            ["workflow_id", "owner_id"],
            ["content_workflows.id", "content_workflows.owner_id"],
            name="fk_approval_workflow_owner",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["draft_version_id", "workflow_id", "owner_id"],
            ["draft_versions.id", "draft_versions.workflow_id", "draft_versions.owner_id"],
            name="fk_approval_draft_workflow_owner",
            ondelete="CASCADE",
        ),
        CheckConstraint(
            "content_hash ~ '^[0-9a-f]{64}$'", name="approval_hash_sha256"
        ),
        Index(
            "uq_active_approval",
            "workflow_id",
            unique=True,
            postgresql_where=text("revoked_at IS NULL"),
        ),
    )

    workflow_id: Mapped[UUID] = mapped_column(UUID_TYPE, nullable=False)
    draft_version_id: Mapped[UUID] = mapped_column(UUID_TYPE, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    approved_by: Mapped[Actor] = mapped_column(ACTOR, nullable=False)
    approved_at: Mapped[datetime] = mapped_column(TIMESTAMP, nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(TIMESTAMP)
    revoke_reason: Mapped[str | None] = mapped_column(Text)


class PublicationJob(OwnedRecord, MutableRecord, Base):
    __tablename__ = "publication_jobs"
    __table_args__ = (
        UniqueConstraint("id", "owner_id", name="uq_publication_job_id_owner"),
        UniqueConstraint("idempotency_key", name="uq_publication_idempotency"),
        ForeignKeyConstraint(
            ["draft_version_id", "owner_id"],
            ["draft_versions.id", "draft_versions.owner_id"],
            name="fk_publication_draft_owner",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["approval_id", "draft_version_id", "owner_id"],
            ["approvals.id", "approvals.draft_version_id", "approvals.owner_id"],
            name="fk_publication_approval_draft_owner",
            ondelete="CASCADE",
        ),
        Index(
            "uq_publication_message",
            "telegram_message_id",
            unique=True,
            postgresql_where=text("telegram_message_id IS NOT NULL"),
        ),
    )

    draft_version_id: Mapped[UUID] = mapped_column(UUID_TYPE, nullable=False)
    approval_id: Mapped[UUID] = mapped_column(UUID_TYPE, nullable=False)
    scheduled_at_utc: Mapped[datetime] = mapped_column(TIMESTAMP, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    attempt_id: Mapped[UUID | None] = mapped_column(UUID_TYPE)
    lease_until: Mapped[datetime | None] = mapped_column(TIMESTAMP)
    telegram_message_id: Mapped[int | None] = mapped_column(BigInteger)
    safe_error_code: Mapped[str | None] = mapped_column(String(64))
    last_attempt_at: Mapped[datetime | None] = mapped_column(TIMESTAMP)


class StyleProfile(OwnedRecord, MutableRecord, Base):
    __tablename__ = "style_profiles"
    __table_args__ = (
        UniqueConstraint("id", "owner_id", name="uq_style_profile_id_owner"),
        UniqueConstraint("owner_id", "version", name="uq_style_profile_owner_version"),
        ForeignKeyConstraint(
            ["supersedes_id", "owner_id"],
            ["style_profiles.id", "style_profiles.owner_id"],
            name="fk_style_profile_supersedes_owner",
            ondelete="RESTRICT",
        ),
        CheckConstraint("version >= 1", name="style_profile_version_positive"),
    )

    version: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    activated_at: Mapped[datetime | None] = mapped_column(TIMESTAMP)
    supersedes_id: Mapped[UUID | None] = mapped_column(UUID_TYPE)


class StyleRule(OwnedRecord, MutableRecord, Base):
    __tablename__ = "style_rules"
    __table_args__ = (
        UniqueConstraint("id", "owner_id", name="uq_style_rule_id_owner"),
        ForeignKeyConstraint(
            ["profile_id", "owner_id"],
            ["style_profiles.id", "style_profiles.owner_id"],
            name="fk_style_rule_profile_owner",
            ondelete="CASCADE",
        ),
    )

    profile_id: Mapped[UUID] = mapped_column(UUID_TYPE, nullable=False)
    scope: Mapped[str] = mapped_column(String(64), nullable=False)
    rule_text: Mapped[str] = mapped_column(Text, nullable=False)
    positive_example: Mapped[str | None] = mapped_column(Text)
    negative_example: Mapped[str | None] = mapped_column(Text)
    origin: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    confirmed_at: Mapped[datetime | None] = mapped_column(TIMESTAMP)


class StyleExample(OwnedRecord, Base):
    __tablename__ = "style_examples"
    __table_args__ = (
        UniqueConstraint("id", "owner_id", name="uq_style_example_id_owner"),
        ForeignKeyConstraint(
            ["profile_id", "owner_id"],
            ["style_profiles.id", "style_profiles.owner_id"],
            name="fk_style_example_profile_owner",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["draft_version_id", "owner_id"],
            ["draft_versions.id", "draft_versions.owner_id"],
            name="fk_style_example_draft_owner",
            ondelete="CASCADE",
        ),
        CheckConstraint("rating IS NULL OR rating BETWEEN 1 AND 5", name="style_rating_range"),
        CheckConstraint("cardinality(tags) <= 32", name="style_tags_bounded"),
    )

    profile_id: Mapped[UUID] = mapped_column(UUID_TYPE, nullable=False)
    draft_version_id: Mapped[UUID | None] = mapped_column(UUID_TYPE)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    rubric: Mapped[str] = mapped_column(String(128), nullable=False)
    format: Mapped[str] = mapped_column(String(16), nullable=False)
    tags: Mapped[list[str]] = mapped_column(ARRAY(String(64)), nullable=False)
    rating: Mapped[int | None] = mapped_column(Integer)
    is_holdout: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class CostEvent(OwnedRecord, Base):
    __tablename__ = "cost_events"
    __table_args__ = (
        UniqueConstraint("id", "owner_id", name="uq_cost_event_id_owner"),
        ForeignKeyConstraint(
            ["workflow_id", "owner_id"],
            ["content_workflows.id", "content_workflows.owner_id"],
            name="fk_cost_workflow_owner",
            ondelete="SET NULL (workflow_id)",
        ),
        ForeignKeyConstraint(
            ["provider_run_id", "owner_id"],
            ["provider_runs.id", "provider_runs.owner_id"],
            name="fk_cost_provider_run_owner",
            ondelete="SET NULL (provider_run_id)",
        ),
        CheckConstraint("amount_rub >= 0", name="cost_amount_nonnegative"),
    )

    workflow_id: Mapped[UUID | None] = mapped_column(UUID_TYPE)
    provider_run_id: Mapped[UUID | None] = mapped_column(UUID_TYPE)
    operation: Mapped[str] = mapped_column(String(64), nullable=False)
    amount_rub: Mapped[Decimal] = mapped_column(Numeric(12, 4), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="RUB")
    estimated: Mapped[bool] = mapped_column(Boolean, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(TIMESTAMP, nullable=False)


class AuditEvent(OwnedRecord, Base):
    __tablename__ = "audit_events"
    __table_args__ = (
        UniqueConstraint("id", "owner_id", name="uq_audit_event_id_owner"),
        ForeignKeyConstraint(
            ["workflow_id", "owner_id"],
            ["content_workflows.id", "content_workflows.owner_id"],
            name="fk_audit_workflow_owner",
            ondelete="SET NULL (workflow_id)",
        ),
        CheckConstraint(
            "octet_length(metadata_json::text) <= 65536",
            name="audit_metadata_bounded",
        ),
        CheckConstraint(
            "event_type IN ("
            "'workflow.state_changed', "
            "'configuration.changed', "
            "'style.rule_decision', "
            "'publication.approval_recorded', "
            "'publication.schedule_changed', "
            "'memory.deletion_recorded', "
            "'publication.delivery_resolved_manually', "
            "'operations.backup_result_recorded'"
            ")",
            name="audit_event_type_known",
        ),
        CheckConstraint(
            "object_type IN ("
            "'workflow', 'configuration', 'style_rule', 'approval', "
            "'schedule', 'deletion', 'delivery', 'backup'"
            ")",
            name="audit_object_type_known",
        ),
        CheckConstraint(
            "trace_id IS NULL OR trace_id ~ '^[0-9a-f]{32}$'",
            name="audit_trace_id_safe",
        ),
    )

    workflow_id: Mapped[UUID | None] = mapped_column(UUID_TYPE)
    event_type: Mapped[str] = mapped_column(String(128), nullable=False)
    actor: Mapped[Actor] = mapped_column(ACTOR, nullable=False)
    object_type: Mapped[str] = mapped_column(String(64), nullable=False)
    object_id: Mapped[UUID | None] = mapped_column(UUID_TYPE)
    trace_id: Mapped[str | None] = mapped_column(String(64))
    metadata_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )


class LibraryItem(OwnedRecord, MutableRecord, Base):
    __tablename__ = "library_items"
    __table_args__ = (
        UniqueConstraint("id", "owner_id", name="uq_library_item_id_owner"),
        ForeignKeyConstraint(
            ["workflow_id", "owner_id"],
            ["content_workflows.id", "content_workflows.owner_id"],
            name="fk_library_workflow_owner",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["draft_version_id", "owner_id"],
            ["draft_versions.id", "draft_versions.owner_id"],
            name="fk_library_draft_owner",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["source_document_id", "owner_id"],
            ["source_documents.id", "source_documents.owner_id"],
            name="fk_library_document_owner",
            ondelete="CASCADE",
        ),
    )

    workflow_id: Mapped[UUID | None] = mapped_column(UUID_TYPE)
    draft_version_id: Mapped[UUID | None] = mapped_column(UUID_TYPE)
    source_document_id: Mapped[UUID | None] = mapped_column(UUID_TYPE)
    item_type: Mapped[str] = mapped_column(String(32), nullable=False)
    title: Mapped[str] = mapped_column(String(1000), nullable=False)
    note: Mapped[str | None] = mapped_column(Text)


class DeletionTombstone(OwnedRecord, MutableRecord, Base):
    __tablename__ = "deletion_tombstones"
    __table_args__ = (
        UniqueConstraint("id", "owner_id", name="uq_deletion_tombstone_id_owner"),
        UniqueConstraint(
            "owner_id", "object_type", "object_id", name="uq_deletion_target"
        ),
        CheckConstraint(
            "expires_at >= requested_at", name="deletion_expiry_after_request"
        ),
    )

    object_type: Mapped[str] = mapped_column(String(64), nullable=False)
    object_id: Mapped[UUID] = mapped_column(UUID_TYPE, nullable=False)
    requested_at: Mapped[datetime] = mapped_column(TIMESTAMP, nullable=False)
    applied_at: Mapped[datetime | None] = mapped_column(TIMESTAMP)
    expires_at: Mapped[datetime] = mapped_column(TIMESTAMP, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)


class BackupRun(OwnedRecord, MutableRecord, Base):
    __tablename__ = "backup_runs"
    __table_args__ = (
        UniqueConstraint("id", "owner_id", name="uq_backup_run_id_owner"),
        CheckConstraint(
            "completed_at IS NULL OR completed_at >= started_at",
            name="backup_completion_after_start",
        ),
    )

    status: Mapped[str] = mapped_column(String(32), nullable=False)
    started_at: Mapped[datetime] = mapped_column(TIMESTAMP, nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(TIMESTAMP)
    checksum: Mapped[str | None] = mapped_column(String(128))
    object_key: Mapped[str | None] = mapped_column(String(1024))
    safe_error_code: Mapped[str | None] = mapped_column(String(64))


class SourceHealthEvent(OwnedRecord, Base):
    __tablename__ = "source_health_events"
    __table_args__ = (
        UniqueConstraint("id", "owner_id", name="uq_source_health_event_id_owner"),
        ForeignKeyConstraint(
            ["source_id", "owner_id"],
            ["sources.id", "sources.owner_id"],
            name="fk_source_health_source_owner",
            ondelete="CASCADE",
        ),
        CheckConstraint(
            "octet_length(details_json::text) <= 16384",
            name="source_health_details_bounded",
        ),
    )

    source_id: Mapped[UUID] = mapped_column(UUID_TYPE, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    safe_error_code: Mapped[str | None] = mapped_column(String(64))
    occurred_at: Mapped[datetime] = mapped_column(TIMESTAMP, nullable=False)
    details_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
