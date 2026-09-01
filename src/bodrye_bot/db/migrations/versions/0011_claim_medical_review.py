"""Persist versioned confirmed extractions and fenced claim-level reviews.

Revision ID: 0011_claim_medical_review
Revises: 0010_digest_run_attempt
"""
from __future__ import annotations

from typing import Any

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0011_claim_medical_review"
down_revision: str | None = "0010_digest_run_attempt"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    _preflight_and_normalize_legacy_rows()
    op.create_table(
        "extraction_confirmations",
        _id(),
        _owner_id(),
        _created_at(),
        sa.Column("workflow_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("workflow_version", sa.Integer(), nullable=False),
        sa.Column("confirmation_number", sa.Integer(), nullable=False),
        sa.Column("is_current", sa.Boolean(), nullable=False),
        sa.Column("invalidated_at", sa.DateTime(timezone=True)),
        sa.Column("extraction_hash", sa.String(length=64), nullable=False),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_extraction_confirmations"),
        sa.UniqueConstraint("id", "owner_id", name="uq_extraction_confirmation_id_owner"),
        sa.UniqueConstraint(
            "id", "workflow_id", "owner_id", name="uq_extraction_confirmation_workflow_owner"
        ),
        sa.UniqueConstraint(
            "workflow_id",
            "owner_id",
            "confirmation_number",
            name="uq_extraction_confirmation_version",
        ),
        sa.ForeignKeyConstraint(
            ["workflow_id", "owner_id"],
            ["content_workflows.id", "content_workflows.owner_id"],
            name="fk_extraction_confirmation_workflow_owner",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            "workflow_version >= 1", name="extraction_confirmation_version_positive"
        ),
        sa.CheckConstraint(
            "confirmation_number >= 1", name="extraction_confirmation_number_positive"
        ),
        sa.CheckConstraint(
            "extraction_hash ~ '^[0-9a-f]{64}$'",
            name="extraction_confirmation_hash_sha256",
        ),
        sa.CheckConstraint(
            "(is_current AND invalidated_at IS NULL) OR "
            "(NOT is_current AND invalidated_at IS NOT NULL)",
            name="extraction_confirmation_current_consistent",
        ),
    )
    op.create_index(
        "uq_extraction_confirmation_current",
        "extraction_confirmations",
        ["workflow_id", "owner_id"],
        unique=True,
        postgresql_where=sa.text("is_current"),
    )

    op.add_column(
        "claims", sa.Column("extraction_confirmation_id", postgresql.UUID(as_uuid=True))
    )
    op.add_column("claims", sa.Column("causality", sa.Text()))
    op.add_column("claims", sa.Column("numeric_value", sa.Text()))
    op.add_column("claims", sa.Column("modality", sa.Text()))
    op.add_column(
        "claims",
        sa.Column(
            "medical_uncertainty",
            sa.Boolean(),
            server_default=sa.text("true"),
            nullable=False,
        ),
    )
    op.alter_column("claims", "medical_uncertainty", server_default=None)
    op.create_foreign_key(
        "fk_claim_extraction_confirmation_owner",
        "claims",
        "extraction_confirmations",
        ["extraction_confirmation_id", "workflow_id", "owner_id"],
        ["id", "workflow_id", "owner_id"],
        ondelete="CASCADE",
    )
    op.create_check_constraint(
        "claim_type_known",
        "claims",
        "claim_type IN ('effect', 'causal', 'association', 'risk', 'numeric', "
        "'diagnosis', 'treatment', 'dosage', 'prevention', 'safety')",
    )
    op.create_check_constraint(
        "claim_text_bounded", "claims", "char_length(exact_text) <= 3800"
    )

    op.create_table(
        "claim_source_documents",
        _id(),
        _owner_id(),
        _created_at(),
        sa.Column("claim_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_document_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_claim_source_documents"),
        sa.UniqueConstraint("id", "owner_id", name="uq_claim_source_id_owner"),
        sa.UniqueConstraint("claim_id", "source_document_id", name="uq_claim_source_document_pair"),
        sa.ForeignKeyConstraint(
            ["claim_id", "owner_id"],
            ["claims.id", "claims.owner_id"],
            name="fk_claim_source_claim_owner",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["source_document_id", "owner_id"],
            ["source_documents.id", "source_documents.owner_id"],
            name="fk_claim_source_document_owner",
            ondelete="RESTRICT",
        ),
    )

    op.create_table(
        "medical_review_attempts",
        _id(),
        _owner_id(),
        _created_at(),
        sa.Column("workflow_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("extraction_confirmation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("base_workflow_version", sa.Integer(), nullable=False),
        sa.Column("pending_workflow_version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("failure_class", sa.String(length=64)),
        sa.PrimaryKeyConstraint("id", name="pk_medical_review_attempts"),
        sa.UniqueConstraint("id", "owner_id", name="uq_medical_attempt_id_owner"),
        sa.ForeignKeyConstraint(
            ["workflow_id", "owner_id"],
            ["content_workflows.id", "content_workflows.owner_id"],
            name="fk_medical_attempt_workflow_owner",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["extraction_confirmation_id", "workflow_id", "owner_id"],
            [
                "extraction_confirmations.id",
                "extraction_confirmations.workflow_id",
                "extraction_confirmations.owner_id",
            ],
            name="fk_medical_attempt_extraction_owner",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            "status IN ('processing', 'completed', 'failed')",
            name="medical_attempt_status_known",
        ),
        sa.CheckConstraint("lease_until > started_at", name="medical_attempt_lease_after_start"),
    )
    op.create_index(
        "uq_medical_attempt_processing",
        "medical_review_attempts",
        ["workflow_id", "owner_id"],
        unique=True,
        postgresql_where=sa.text("status = 'processing'"),
    )

    op.add_column("provider_runs", sa.Column("medical_attempt_id", postgresql.UUID(as_uuid=True)))
    op.add_column("provider_runs", sa.Column("response_id", sa.String(length=32)))
    op.create_foreign_key(
        "fk_provider_run_medical_attempt_owner",
        "provider_runs",
        "medical_review_attempts",
        ["medical_attempt_id", "owner_id"],
        ["id", "owner_id"],
        ondelete="CASCADE",
    )
    op.create_unique_constraint("uq_provider_run_response_id", "provider_runs", ["response_id"])
    op.create_check_constraint(
        "provider_run_response_id_hex",
        "provider_runs",
        "response_id IS NULL OR response_id ~ '^[0-9a-f]{32}$'",
    )

    op.create_table(
        "claim_review_decisions",
        _id(),
        _owner_id(),
        _created_at(),
        sa.Column("workflow_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("extraction_confirmation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("attempt_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("workflow_version", sa.Integer(), nullable=False),
        sa.Column("extraction_hash", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("blocking_reasons", postgresql.ARRAY(sa.Text()), nullable=False),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("policy_version", sa.String(length=64), nullable=False),
        sa.Column("validity_seconds", sa.Integer(), nullable=False),
        sa.Column("model_run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("classification_response_id", sa.String(length=32), nullable=False),
        sa.Column("draft_version_id", postgresql.UUID(as_uuid=True)),
        sa.Column("draft_hash", sa.String(length=64)),
        sa.PrimaryKeyConstraint("id", name="pk_claim_review_decisions"),
        sa.UniqueConstraint("id", "owner_id", name="uq_claim_review_id_owner"),
        sa.UniqueConstraint("attempt_id", name="uq_claim_review_attempt"),
        sa.ForeignKeyConstraint(
            ["workflow_id", "owner_id"],
            ["content_workflows.id", "content_workflows.owner_id"],
            name="fk_claim_review_workflow_owner",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["attempt_id", "owner_id"],
            ["medical_review_attempts.id", "medical_review_attempts.owner_id"],
            name="fk_claim_review_attempt_owner",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["extraction_confirmation_id", "workflow_id", "owner_id"],
            [
                "extraction_confirmations.id",
                "extraction_confirmations.workflow_id",
                "extraction_confirmations.owner_id",
            ],
            name="fk_claim_review_extraction_owner",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["model_run_id", "owner_id"],
            ["provider_runs.id", "provider_runs.owner_id"],
            name="fk_claim_review_model_run_owner",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["draft_version_id", "workflow_id", "owner_id"],
            ["draft_versions.id", "draft_versions.workflow_id", "draft_versions.owner_id"],
            name="fk_claim_review_draft_owner",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint("workflow_version >= 1", name="claim_review_version_positive"),
        sa.CheckConstraint(
            "extraction_hash ~ '^[0-9a-f]{64}$'", name="claim_review_hash_sha256"
        ),
        sa.CheckConstraint("status IN ('passed', 'blocked')", name="claim_review_status_known"),
        sa.CheckConstraint(
            "cardinality(blocking_reasons) <= 64", name="claim_review_blocking_reasons_bounded"
        ),
        sa.CheckConstraint(
            "validity_seconds BETWEEN 1 AND 604800", name="claim_review_validity_bounded"
        ),
        sa.CheckConstraint(
            "classification_response_id ~ '^[0-9a-f]{32}$'",
            name="claim_review_response_id_hex",
        ),
        sa.CheckConstraint(
            "(draft_version_id IS NULL AND draft_hash IS NULL) OR "
            "(draft_version_id IS NOT NULL AND draft_hash ~ '^[0-9a-f]{64}$')",
            name="claim_review_draft_binding_paired",
        ),
    )

    op.add_column("evidence", sa.Column("review_decision_id", postgresql.UUID(as_uuid=True)))
    op.add_column("evidence", sa.Column("response_id", sa.String(length=32)))
    op.create_foreign_key(
        "fk_evidence_review_decision_owner",
        "evidence",
        "claim_review_decisions",
        ["review_decision_id", "owner_id"],
        ["id", "owner_id"],
        ondelete="CASCADE",
    )
    op.create_unique_constraint(
        "uq_evidence_review_claim_document",
        "evidence",
        ["review_decision_id", "claim_id", "source_document_id"],
    )
    op.create_check_constraint(
        "evidence_response_id_hex",
        "evidence",
        "response_id IS NULL OR response_id ~ '^[0-9a-f]{32}$'",
    )
    op.create_check_constraint(
        "evidence_verdict_known",
        "evidence",
        "verdict IN ('supported', 'refuted', 'insufficient', "
        "'manual_required', 'review_incomplete')",
    )
    op.create_check_constraint(
        "evidence_risk_known", "evidence", "risk IN ('green', 'yellow', 'red')"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE evidence DROP CONSTRAINT IF EXISTS ck_evidence_risk_known")
    op.execute("ALTER TABLE evidence DROP CONSTRAINT IF EXISTS ck_ck_evidence_risk_known")
    op.execute("ALTER TABLE evidence DROP CONSTRAINT IF EXISTS ck_evidence_verdict_known")
    op.execute("ALTER TABLE evidence DROP CONSTRAINT IF EXISTS ck_ck_evidence_verdict_known")
    op.execute("ALTER TABLE evidence DROP CONSTRAINT IF EXISTS ck_evidence_response_id_hex")
    op.execute("ALTER TABLE evidence DROP CONSTRAINT IF EXISTS uq_evidence_review_claim_document")
    op.execute("ALTER TABLE evidence DROP CONSTRAINT IF EXISTS fk_evidence_review_decision_owner")
    op.execute("ALTER TABLE evidence DROP COLUMN IF EXISTS response_id")
    op.execute("ALTER TABLE evidence DROP COLUMN IF EXISTS review_decision_id")
    op.execute("DROP TABLE IF EXISTS claim_review_decisions CASCADE")
    op.execute(
        "ALTER TABLE provider_runs DROP CONSTRAINT IF EXISTS ck_provider_run_response_id_hex"
    )
    op.execute("ALTER TABLE provider_runs DROP CONSTRAINT IF EXISTS uq_provider_run_response_id")
    op.execute(
        "ALTER TABLE provider_runs "
        "DROP CONSTRAINT IF EXISTS fk_provider_run_medical_attempt_owner"
    )
    op.execute("ALTER TABLE provider_runs DROP COLUMN IF EXISTS response_id")
    op.execute("ALTER TABLE provider_runs DROP COLUMN IF EXISTS medical_attempt_id")
    op.execute("DROP TABLE IF EXISTS medical_review_attempts CASCADE")
    op.execute("DROP TABLE IF EXISTS claim_source_documents CASCADE")
    op.execute(
        "ALTER TABLE claims "
        "DROP CONSTRAINT IF EXISTS fk_claim_extraction_confirmation_owner"
    )
    op.execute("ALTER TABLE claims DROP CONSTRAINT IF EXISTS ck_claim_text_bounded")
    op.execute("ALTER TABLE claims DROP CONSTRAINT IF EXISTS ck_ck_claim_text_bounded")
    op.execute("ALTER TABLE claims DROP CONSTRAINT IF EXISTS ck_claim_type_known")
    op.execute("ALTER TABLE claims DROP CONSTRAINT IF EXISTS ck_ck_claim_type_known")
    op.execute("ALTER TABLE claims DROP COLUMN IF EXISTS medical_uncertainty")
    op.execute("ALTER TABLE claims DROP COLUMN IF EXISTS modality")
    op.execute("ALTER TABLE claims DROP COLUMN IF EXISTS numeric_value")
    op.execute("ALTER TABLE claims DROP COLUMN IF EXISTS causality")
    op.execute("ALTER TABLE claims DROP COLUMN IF EXISTS extraction_confirmation_id")
    op.execute("DROP TABLE IF EXISTS extraction_confirmations CASCADE")


def _preflight_and_normalize_legacy_rows() -> None:
    op.execute("UPDATE evidence SET verdict = 'manual_required' WHERE verdict = 'manual_review'")
    op.execute(
        """
        DO $$
        BEGIN
          IF EXISTS (
            SELECT 1 FROM claims
            WHERE claim_type NOT IN (
              'effect', 'causal', 'association', 'risk', 'numeric',
              'diagnosis', 'treatment', 'dosage', 'prevention', 'safety'
            )
          ) THEN
            RAISE EXCEPTION 'medical migration preflight failed: unknown claims.claim_type';
          END IF;
          IF EXISTS (SELECT 1 FROM claims WHERE char_length(exact_text) > 3800) THEN
            RAISE EXCEPTION 'medical migration preflight failed: claims.exact_text exceeds 3800';
          END IF;
          IF EXISTS (
            SELECT 1 FROM evidence
            WHERE verdict NOT IN (
              'supported', 'refuted', 'insufficient', 'manual_required', 'review_incomplete'
            )
          ) THEN
            RAISE EXCEPTION 'medical migration preflight failed: unknown evidence.verdict';
          END IF;
          IF EXISTS (SELECT 1 FROM evidence WHERE risk NOT IN ('green', 'yellow', 'red')) THEN
            RAISE EXCEPTION 'medical migration preflight failed: unknown evidence.risk';
          END IF;
        END $$;
        """
    )


def _id() -> sa.Column[Any]:
    return sa.Column(
        "id",
        postgresql.UUID(as_uuid=True),
        server_default=sa.text("gen_random_uuid()"),
        nullable=False,
    )


def _owner_id() -> sa.Column[Any]:
    return sa.Column("owner_id", sa.BigInteger(), nullable=False)


def _created_at() -> sa.Column[Any]:
    return sa.Column(
        "created_at",
        sa.DateTime(timezone=True),
        server_default=sa.text("CURRENT_TIMESTAMP"),
        nullable=False,
    )
