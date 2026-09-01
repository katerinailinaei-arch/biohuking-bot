"""Persist confirmed extraction bindings and claim-level medical reviews.

Revision ID: 0011_claim_medical_review
Revises: 0010_digest_run_attempt
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0011_claim_medical_review"
down_revision: str | None = "0010_digest_run_attempt"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "extraction_confirmations",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("owner_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column("workflow_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("workflow_version", sa.Integer(), nullable=False),
        sa.Column("extraction_hash", sa.String(length=64), nullable=False),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_extraction_confirmations"),
        sa.UniqueConstraint("id", "owner_id", name="uq_extraction_confirmation_id_owner"),
        sa.UniqueConstraint(
            "id",
            "workflow_id",
            "owner_id",
            name="uq_extraction_confirmation_workflow_owner",
        ),
        sa.UniqueConstraint(
            "workflow_id", "owner_id", name="uq_extraction_confirmation_current"
        ),
        sa.ForeignKeyConstraint(
            ["workflow_id", "owner_id"],
            ["content_workflows.id", "content_workflows.owner_id"],
            name="fk_extraction_confirmation_workflow_owner",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            "workflow_version >= 1",
            name="extraction_confirmation_version_positive",
        ),
        sa.CheckConstraint(
            "extraction_hash ~ '^[0-9a-f]{64}$'",
            name="extraction_confirmation_hash_sha256",
        ),
    )

    op.add_column(
        "claims",
        sa.Column("extraction_confirmation_id", postgresql.UUID(as_uuid=True)),
    )
    op.add_column("claims", sa.Column("causality", sa.Text()))
    op.add_column("claims", sa.Column("numeric_value", sa.Text()))
    op.add_column("claims", sa.Column("modality", sa.Text()))
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
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("owner_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column("claim_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_document_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_claim_source_documents"),
        sa.UniqueConstraint("id", "owner_id", name="uq_claim_source_id_owner"),
        sa.UniqueConstraint(
            "claim_id", "source_document_id", name="uq_claim_source_document_pair"
        ),
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

    op.create_check_constraint(
        "evidence_verdict_known",
        "evidence",
        "verdict IN ('supported', 'refuted', 'insufficient', "
        "'manual_required', 'review_incomplete')",
    )
    op.create_check_constraint(
        "evidence_risk_known",
        "evidence",
        "risk IN ('green', 'yellow', 'red')",
    )

    op.create_table(
        "claim_review_decisions",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("owner_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column("workflow_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "extraction_confirmation_id", postgresql.UUID(as_uuid=True), nullable=False
        ),
        sa.Column("workflow_version", sa.Integer(), nullable=False),
        sa.Column("extraction_hash", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("blocking_reasons", postgresql.ARRAY(sa.Text()), nullable=False),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("policy_version", sa.String(length=64), nullable=False),
        sa.Column("model_run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_claim_review_decisions"),
        sa.UniqueConstraint("id", "owner_id", name="uq_claim_review_id_owner"),
        sa.ForeignKeyConstraint(
            ["workflow_id", "owner_id"],
            ["content_workflows.id", "content_workflows.owner_id"],
            name="fk_claim_review_workflow_owner",
            ondelete="CASCADE",
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
        sa.CheckConstraint(
            "workflow_version >= 1", name="claim_review_version_positive"
        ),
        sa.CheckConstraint(
            "extraction_hash ~ '^[0-9a-f]{64}$'",
            name="claim_review_hash_sha256",
        ),
        sa.CheckConstraint(
            "status IN ('passed', 'blocked')", name="claim_review_status_known"
        ),
        sa.CheckConstraint(
            "cardinality(blocking_reasons) <= 64",
            name="claim_review_blocking_reasons_bounded",
        ),
    )


def downgrade() -> None:
    op.drop_table("claim_review_decisions")
    op.execute(
        "ALTER TABLE evidence DROP CONSTRAINT IF EXISTS ck_evidence_risk_known"
    )
    op.execute(
        "ALTER TABLE evidence DROP CONSTRAINT IF EXISTS ck_ck_evidence_risk_known"
    )
    op.execute(
        "ALTER TABLE evidence DROP CONSTRAINT IF EXISTS ck_evidence_verdict_known"
    )
    op.execute(
        "ALTER TABLE evidence DROP CONSTRAINT IF EXISTS ck_ck_evidence_verdict_known"
    )
    op.drop_table("claim_source_documents")
    op.drop_constraint(
        "fk_claim_extraction_confirmation_owner", "claims", type_="foreignkey"
    )
    op.execute("ALTER TABLE claims DROP CONSTRAINT IF EXISTS ck_claim_text_bounded")
    op.execute("ALTER TABLE claims DROP CONSTRAINT IF EXISTS ck_ck_claim_text_bounded")
    op.execute("ALTER TABLE claims DROP CONSTRAINT IF EXISTS ck_claim_type_known")
    op.execute("ALTER TABLE claims DROP CONSTRAINT IF EXISTS ck_ck_claim_type_known")
    op.drop_column("claims", "modality")
    op.drop_column("claims", "numeric_value")
    op.drop_column("claims", "causality")
    op.drop_column("claims", "extraction_confirmation_id")
    op.drop_table("extraction_confirmations")
