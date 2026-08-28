"""Create the complete owner-scoped PostgreSQL schema.

Revision ID: 0001_initial
Revises:
Create Date: 2026-08-28
"""

from __future__ import annotations

from collections.abc import Iterable

from alembic import op

revision: str = "0001_initial"
down_revision: str | None = None
branch_labels: str | None = None
depends_on: str | None = None


WORKFLOW_STATUSES = (
    "ingested",
    "extracted",
    "extraction_confirmed",
    "claims_review_pending",
    "claims_review_passed",
    "claims_review_blocked",
    "angles_ready",
    "angle_selected",
    "draft",
    "draft_review_pending",
    "draft_review_passed",
    "draft_review_blocked",
    "approved",
    "scheduled",
    "processing",
    "published",
    "failed",
    "delivery_unknown",
    "cancelled",
    "rejected",
)


def _execute_all(statements: Iterable[str]) -> None:
    for statement in statements:
        op.execute(statement)


def upgrade() -> None:
    workflow_values = ", ".join(f"'{status}'" for status in WORKFLOW_STATUSES)
    op.execute(f"CREATE TYPE workflow_status AS ENUM ({workflow_values})")
    op.execute("CREATE TYPE actor AS ENUM ('owner', 'system', 'worker')")

    _execute_all(
        (
            """
            CREATE TABLE sources (
                id uuid DEFAULT gen_random_uuid() NOT NULL,
                owner_id bigint NOT NULL,
                created_at timestamptz DEFAULT CURRENT_TIMESTAMP NOT NULL,
                updated_at timestamptz DEFAULT CURRENT_TIMESTAMP NOT NULL,
                name varchar(255) NOT NULL,
                canonical_url varchar(2048) NOT NULL,
                source_type varchar(64) NOT NULL,
                roles varchar(64)[] NOT NULL,
                access_method varchar(64) NOT NULL,
                status varchar(32) NOT NULL,
                checked_at timestamptz,
                failure_count integer DEFAULT 0 NOT NULL,
                license_note text,
                config_json jsonb DEFAULT '{}'::jsonb NOT NULL,
                CONSTRAINT pk_sources PRIMARY KEY (id),
                CONSTRAINT uq_source_id_owner UNIQUE (id, owner_id),
                CONSTRAINT uq_source_owner_url UNIQUE (owner_id, canonical_url),
                CONSTRAINT ck_source_roles_bounded CHECK (cardinality(roles) <= 16),
                CONSTRAINT ck_source_config_bounded
                    CHECK (octet_length(config_json::text) <= 65536)
            )
            """,
            """
            CREATE TABLE source_documents (
                id uuid DEFAULT gen_random_uuid() NOT NULL,
                owner_id bigint NOT NULL,
                created_at timestamptz DEFAULT CURRENT_TIMESTAMP NOT NULL,
                source_id uuid NOT NULL,
                url varchar(2048) NOT NULL,
                title varchar(1000),
                published_at timestamptz,
                fetched_at timestamptz NOT NULL,
                content_hash varchar(64) NOT NULL,
                bounded_excerpt text,
                raw_expires_at timestamptz,
                fetch_status varchar(32) NOT NULL,
                http_metadata jsonb DEFAULT '{}'::jsonb NOT NULL,
                CONSTRAINT pk_source_documents PRIMARY KEY (id),
                CONSTRAINT uq_source_document_id_owner UNIQUE (id, owner_id),
                CONSTRAINT fk_source_document_source_owner
                    FOREIGN KEY (source_id, owner_id)
                    REFERENCES sources (id, owner_id) ON DELETE RESTRICT,
                CONSTRAINT ck_source_document_hash_sha256
                    CHECK (content_hash ~ '^[0-9a-f]{64}$'),
                CONSTRAINT ck_source_document_excerpt_bounded
                    CHECK (bounded_excerpt IS NULL OR char_length(bounded_excerpt) <= 65536),
                CONSTRAINT ck_source_http_metadata_bounded
                    CHECK (octet_length(http_metadata::text) <= 65536),
                CONSTRAINT ck_source_document_raw_expiry_24h
                    CHECK (raw_expires_at IS NULL
                           OR raw_expires_at <= fetched_at + interval '24 hours')
            )
            """,
            """
            CREATE TABLE source_payload_cache (
                id uuid DEFAULT gen_random_uuid() NOT NULL,
                owner_id bigint NOT NULL,
                created_at timestamptz DEFAULT CURRENT_TIMESTAMP NOT NULL,
                source_document_id uuid NOT NULL,
                payload bytea NOT NULL,
                fetched_at timestamptz NOT NULL,
                expires_at timestamptz NOT NULL,
                CONSTRAINT pk_source_payload_cache PRIMARY KEY (id),
                CONSTRAINT uq_source_payload_id_owner UNIQUE (id, owner_id),
                CONSTRAINT uq_source_payload_document UNIQUE (source_document_id),
                CONSTRAINT fk_source_payload_document_owner
                    FOREIGN KEY (source_document_id, owner_id)
                    REFERENCES source_documents (id, owner_id) ON DELETE CASCADE,
                CONSTRAINT ck_source_payload_expiry_24h
                    CHECK (expires_at <= fetched_at + interval '24 hours'),
                CONSTRAINT ck_source_payload_max_10mib
                    CHECK (octet_length(payload) <= 10485760)
            )
            """,
            """
            CREATE TABLE digest_items (
                id uuid DEFAULT gen_random_uuid() NOT NULL,
                owner_id bigint NOT NULL,
                created_at timestamptz DEFAULT CURRENT_TIMESTAMP NOT NULL,
                updated_at timestamptz DEFAULT CURRENT_TIMESTAMP NOT NULL,
                source_document_id uuid NOT NULL,
                topic_fingerprint varchar(128) NOT NULL,
                summary text NOT NULL,
                rubric varchar(128) NOT NULL,
                audience_reason text NOT NULL,
                selection_reason text NOT NULL,
                preliminary_risk varchar(32) NOT NULL,
                score_components jsonb NOT NULL,
                digest_date timestamptz NOT NULL,
                disposition varchar(32) NOT NULL,
                CONSTRAINT pk_digest_items PRIMARY KEY (id),
                CONSTRAINT uq_digest_item_id_owner UNIQUE (id, owner_id),
                CONSTRAINT fk_digest_document_owner
                    FOREIGN KEY (source_document_id, owner_id)
                    REFERENCES source_documents (id, owner_id) ON DELETE CASCADE,
                CONSTRAINT ck_digest_score_components_bounded
                    CHECK (octet_length(score_components::text) <= 32768)
            )
            """,
            """
            CREATE TABLE content_workflows (
                id uuid DEFAULT gen_random_uuid() NOT NULL,
                owner_id bigint NOT NULL,
                created_at timestamptz DEFAULT CURRENT_TIMESTAMP NOT NULL,
                updated_at timestamptz DEFAULT CURRENT_TIMESTAMP NOT NULL,
                origin_type varchar(32) NOT NULL,
                origin_id uuid,
                status workflow_status NOT NULL,
                selected_angle_id uuid,
                recommended_format varchar(16) NOT NULL,
                current_version_id uuid,
                version integer DEFAULT 1 NOT NULL,
                CONSTRAINT pk_content_workflows PRIMARY KEY (id),
                CONSTRAINT uq_workflow_id_owner UNIQUE (id, owner_id),
                CONSTRAINT ck_workflow_version_positive CHECK (version >= 1)
            )
            """,
            """
            CREATE TABLE angles (
                id uuid DEFAULT gen_random_uuid() NOT NULL,
                owner_id bigint NOT NULL,
                created_at timestamptz DEFAULT CURRENT_TIMESTAMP NOT NULL,
                workflow_id uuid NOT NULL,
                angle_type varchar(64) NOT NULL,
                name varchar(255) NOT NULL,
                hook text NOT NULL,
                promise text NOT NULL,
                tone_note text NOT NULL,
                selected_at timestamptz,
                CONSTRAINT pk_angles PRIMARY KEY (id),
                CONSTRAINT uq_angle_id_owner UNIQUE (id, owner_id),
                CONSTRAINT uq_angle_id_workflow_owner UNIQUE (id, workflow_id, owner_id),
                CONSTRAINT fk_angle_workflow_owner
                    FOREIGN KEY (workflow_id, owner_id)
                    REFERENCES content_workflows (id, owner_id) ON DELETE CASCADE
            )
            """,
            """
            CREATE TABLE provider_runs (
                id uuid DEFAULT gen_random_uuid() NOT NULL,
                owner_id bigint NOT NULL,
                created_at timestamptz DEFAULT CURRENT_TIMESTAMP NOT NULL,
                updated_at timestamptz DEFAULT CURRENT_TIMESTAMP NOT NULL,
                workflow_id uuid,
                operation varchar(64) NOT NULL,
                provider varchar(32) NOT NULL,
                model varchar(255) NOT NULL,
                status varchar(32) NOT NULL,
                prompt_version varchar(64) NOT NULL,
                schema_version varchar(64) NOT NULL,
                provider_request_id varchar(255),
                input_tokens integer,
                output_tokens integer,
                duration_ms integer,
                error_class varchar(128),
                CONSTRAINT pk_provider_runs PRIMARY KEY (id),
                CONSTRAINT uq_provider_run_id_owner UNIQUE (id, owner_id),
                CONSTRAINT fk_provider_run_workflow_owner
                    FOREIGN KEY (workflow_id, owner_id)
                    REFERENCES content_workflows (id, owner_id)
                    ON DELETE SET NULL (workflow_id),
                CONSTRAINT ck_provider_input_tokens_nonnegative
                    CHECK (input_tokens IS NULL OR input_tokens >= 0),
                CONSTRAINT ck_provider_output_tokens_nonnegative
                    CHECK (output_tokens IS NULL OR output_tokens >= 0)
            )
            """,
            """
            CREATE TABLE draft_versions (
                id uuid DEFAULT gen_random_uuid() NOT NULL,
                owner_id bigint NOT NULL,
                created_at timestamptz DEFAULT CURRENT_TIMESTAMP NOT NULL,
                workflow_id uuid NOT NULL,
                version_number integer NOT NULL,
                body text NOT NULL,
                body_hash varchar(64) NOT NULL,
                format varchar(16) NOT NULL,
                headlines text[] NOT NULL,
                public_sources text[] NOT NULL,
                style_profile_version integer NOT NULL,
                created_by_run_id uuid,
                supersedes_id uuid,
                CONSTRAINT pk_draft_versions PRIMARY KEY (id),
                CONSTRAINT uq_draft_id_owner UNIQUE (id, owner_id),
                CONSTRAINT uq_draft_id_workflow_owner UNIQUE (id, workflow_id, owner_id),
                CONSTRAINT uq_draft_workflow_version UNIQUE (workflow_id, version_number),
                CONSTRAINT fk_draft_workflow_owner
                    FOREIGN KEY (workflow_id, owner_id)
                    REFERENCES content_workflows (id, owner_id) ON DELETE CASCADE,
                CONSTRAINT fk_draft_provider_run_owner
                    FOREIGN KEY (created_by_run_id, owner_id)
                    REFERENCES provider_runs (id, owner_id) ON DELETE RESTRICT,
                CONSTRAINT fk_draft_supersedes_owner
                    FOREIGN KEY (supersedes_id, workflow_id, owner_id)
                    REFERENCES draft_versions (id, workflow_id, owner_id)
                    ON DELETE RESTRICT,
                CONSTRAINT ck_draft_version_positive CHECK (version_number >= 1),
                CONSTRAINT ck_draft_body_max_3800 CHECK (char_length(body) <= 3800),
                CONSTRAINT ck_draft_hash_sha256 CHECK (body_hash ~ '^[0-9a-f]{64}$'),
                CONSTRAINT ck_draft_headlines_max_three CHECK (cardinality(headlines) <= 3),
                CONSTRAINT ck_draft_public_sources_max_three
                    CHECK (cardinality(public_sources) <= 3)
            )
            """,
            """
            ALTER TABLE content_workflows
            ADD CONSTRAINT fk_workflow_selected_angle_owner
                FOREIGN KEY (selected_angle_id, id, owner_id)
                REFERENCES angles (id, workflow_id, owner_id) ON DELETE RESTRICT
            """,
            """
            ALTER TABLE content_workflows
            ADD CONSTRAINT fk_workflow_current_draft_owner
                FOREIGN KEY (current_version_id, id, owner_id)
                REFERENCES draft_versions (id, workflow_id, owner_id) ON DELETE RESTRICT
            """,
            """
            CREATE TABLE claims (
                id uuid DEFAULT gen_random_uuid() NOT NULL,
                owner_id bigint NOT NULL,
                created_at timestamptz DEFAULT CURRENT_TIMESTAMP NOT NULL,
                updated_at timestamptz DEFAULT CURRENT_TIMESTAMP NOT NULL,
                workflow_id uuid NOT NULL,
                draft_version_id uuid,
                exact_text text NOT NULL,
                claim_type varchar(32) NOT NULL,
                population text,
                context text,
                is_medical boolean NOT NULL,
                status varchar(32) NOT NULL,
                CONSTRAINT pk_claims PRIMARY KEY (id),
                CONSTRAINT uq_claim_id_owner UNIQUE (id, owner_id),
                CONSTRAINT uq_claim_id_workflow_owner UNIQUE (id, workflow_id, owner_id),
                CONSTRAINT fk_claim_workflow_owner
                    FOREIGN KEY (workflow_id, owner_id)
                    REFERENCES content_workflows (id, owner_id) ON DELETE CASCADE,
                CONSTRAINT fk_claim_draft_owner
                    FOREIGN KEY (draft_version_id, workflow_id, owner_id)
                    REFERENCES draft_versions (id, workflow_id, owner_id) ON DELETE CASCADE
            )
            """,
            """
            CREATE TABLE evidence (
                id uuid DEFAULT gen_random_uuid() NOT NULL,
                owner_id bigint NOT NULL,
                created_at timestamptz DEFAULT CURRENT_TIMESTAMP NOT NULL,
                claim_id uuid NOT NULL,
                source_document_id uuid NOT NULL,
                verdict varchar(32) NOT NULL,
                risk varchar(16) NOT NULL,
                exact_excerpt text NOT NULL,
                excerpt_hash varchar(64) NOT NULL,
                applicability text NOT NULL,
                limitations text NOT NULL,
                reviewed_at timestamptz NOT NULL,
                review_model_run_id uuid,
                CONSTRAINT pk_evidence PRIMARY KEY (id),
                CONSTRAINT uq_evidence_id_owner UNIQUE (id, owner_id),
                CONSTRAINT fk_evidence_claim_owner
                    FOREIGN KEY (claim_id, owner_id)
                    REFERENCES claims (id, owner_id) ON DELETE CASCADE,
                CONSTRAINT fk_evidence_document_owner
                    FOREIGN KEY (source_document_id, owner_id)
                    REFERENCES source_documents (id, owner_id) ON DELETE RESTRICT,
                CONSTRAINT fk_evidence_provider_run_owner
                    FOREIGN KEY (review_model_run_id, owner_id)
                    REFERENCES provider_runs (id, owner_id) ON DELETE RESTRICT,
                CONSTRAINT ck_evidence_hash_sha256
                    CHECK (excerpt_hash ~ '^[0-9a-f]{64}$'),
                CONSTRAINT ck_evidence_excerpt_bounded
                    CHECK (char_length(exact_excerpt) <= 65536)
            )
            """,
            """
            CREATE TABLE review_decisions (
                id uuid DEFAULT gen_random_uuid() NOT NULL,
                owner_id bigint NOT NULL,
                created_at timestamptz DEFAULT CURRENT_TIMESTAMP NOT NULL,
                draft_version_id uuid NOT NULL,
                status varchar(32) NOT NULL,
                blocking_reasons text[] NOT NULL,
                changed_claim_ids uuid[] NOT NULL,
                reviewed_at timestamptz NOT NULL,
                policy_version varchar(64) NOT NULL,
                CONSTRAINT pk_review_decisions PRIMARY KEY (id),
                CONSTRAINT uq_review_decision_id_owner UNIQUE (id, owner_id),
                CONSTRAINT fk_review_draft_owner
                    FOREIGN KEY (draft_version_id, owner_id)
                    REFERENCES draft_versions (id, owner_id) ON DELETE CASCADE,
                CONSTRAINT ck_review_blocking_reasons_bounded
                    CHECK (cardinality(blocking_reasons) <= 64),
                CONSTRAINT ck_review_changed_claims_bounded
                    CHECK (cardinality(changed_claim_ids) <= 256),
                CONSTRAINT ck_review_status_known
                    CHECK (status IN ('pending', 'passed', 'blocked', 'review_incomplete'))
            )
            """,
            """
            CREATE TABLE approvals (
                id uuid DEFAULT gen_random_uuid() NOT NULL,
                owner_id bigint NOT NULL,
                created_at timestamptz DEFAULT CURRENT_TIMESTAMP NOT NULL,
                updated_at timestamptz DEFAULT CURRENT_TIMESTAMP NOT NULL,
                workflow_id uuid NOT NULL,
                draft_version_id uuid NOT NULL,
                content_hash varchar(64) NOT NULL,
                approved_by actor NOT NULL,
                approved_at timestamptz NOT NULL,
                revoked_at timestamptz,
                revoke_reason text,
                CONSTRAINT pk_approvals PRIMARY KEY (id),
                CONSTRAINT uq_approval_id_owner UNIQUE (id, owner_id),
                CONSTRAINT uq_approval_id_draft_owner
                    UNIQUE (id, draft_version_id, owner_id),
                CONSTRAINT fk_approval_workflow_owner
                    FOREIGN KEY (workflow_id, owner_id)
                    REFERENCES content_workflows (id, owner_id) ON DELETE CASCADE,
                CONSTRAINT fk_approval_draft_workflow_owner
                    FOREIGN KEY (draft_version_id, workflow_id, owner_id)
                    REFERENCES draft_versions (id, workflow_id, owner_id) ON DELETE CASCADE,
                CONSTRAINT ck_approval_hash_sha256
                    CHECK (content_hash ~ '^[0-9a-f]{64}$')
            )
            """,
            """
            CREATE UNIQUE INDEX uq_active_approval
            ON approvals (workflow_id) WHERE revoked_at IS NULL
            """,
            """
            CREATE TABLE publication_jobs (
                id uuid DEFAULT gen_random_uuid() NOT NULL,
                owner_id bigint NOT NULL,
                created_at timestamptz DEFAULT CURRENT_TIMESTAMP NOT NULL,
                updated_at timestamptz DEFAULT CURRENT_TIMESTAMP NOT NULL,
                draft_version_id uuid NOT NULL,
                approval_id uuid NOT NULL,
                scheduled_at_utc timestamptz NOT NULL,
                status varchar(32) NOT NULL,
                idempotency_key varchar(255) NOT NULL,
                attempt_id uuid,
                lease_until timestamptz,
                telegram_message_id bigint,
                safe_error_code varchar(64),
                last_attempt_at timestamptz,
                CONSTRAINT pk_publication_jobs PRIMARY KEY (id),
                CONSTRAINT uq_publication_job_id_owner UNIQUE (id, owner_id),
                CONSTRAINT uq_publication_idempotency UNIQUE (idempotency_key),
                CONSTRAINT fk_publication_draft_owner
                    FOREIGN KEY (draft_version_id, owner_id)
                    REFERENCES draft_versions (id, owner_id) ON DELETE CASCADE,
                CONSTRAINT fk_publication_approval_draft_owner
                    FOREIGN KEY (approval_id, draft_version_id, owner_id)
                    REFERENCES approvals (id, draft_version_id, owner_id)
                    ON DELETE CASCADE
            )
            """,
            """
            CREATE UNIQUE INDEX uq_publication_message
            ON publication_jobs (telegram_message_id)
            WHERE telegram_message_id IS NOT NULL
            """,
            """
            CREATE TABLE style_profiles (
                id uuid DEFAULT gen_random_uuid() NOT NULL,
                owner_id bigint NOT NULL,
                created_at timestamptz DEFAULT CURRENT_TIMESTAMP NOT NULL,
                updated_at timestamptz DEFAULT CURRENT_TIMESTAMP NOT NULL,
                version integer NOT NULL,
                status varchar(32) NOT NULL,
                activated_at timestamptz,
                supersedes_id uuid,
                CONSTRAINT pk_style_profiles PRIMARY KEY (id),
                CONSTRAINT uq_style_profile_id_owner UNIQUE (id, owner_id),
                CONSTRAINT uq_style_profile_owner_version UNIQUE (owner_id, version),
                CONSTRAINT fk_style_profile_supersedes_owner
                    FOREIGN KEY (supersedes_id, owner_id)
                    REFERENCES style_profiles (id, owner_id) ON DELETE RESTRICT,
                CONSTRAINT ck_style_profile_version_positive CHECK (version >= 1)
            )
            """,
            """
            CREATE TABLE style_rules (
                id uuid DEFAULT gen_random_uuid() NOT NULL,
                owner_id bigint NOT NULL,
                created_at timestamptz DEFAULT CURRENT_TIMESTAMP NOT NULL,
                updated_at timestamptz DEFAULT CURRENT_TIMESTAMP NOT NULL,
                profile_id uuid NOT NULL,
                scope varchar(64) NOT NULL,
                rule_text text NOT NULL,
                positive_example text,
                negative_example text,
                origin varchar(32) NOT NULL,
                status varchar(32) NOT NULL,
                confirmed_at timestamptz,
                CONSTRAINT pk_style_rules PRIMARY KEY (id),
                CONSTRAINT uq_style_rule_id_owner UNIQUE (id, owner_id),
                CONSTRAINT fk_style_rule_profile_owner
                    FOREIGN KEY (profile_id, owner_id)
                    REFERENCES style_profiles (id, owner_id) ON DELETE CASCADE
            )
            """,
            """
            CREATE TABLE style_examples (
                id uuid DEFAULT gen_random_uuid() NOT NULL,
                owner_id bigint NOT NULL,
                created_at timestamptz DEFAULT CURRENT_TIMESTAMP NOT NULL,
                profile_id uuid NOT NULL,
                draft_version_id uuid,
                text text NOT NULL,
                rubric varchar(128) NOT NULL,
                format varchar(16) NOT NULL,
                tags varchar(64)[] NOT NULL,
                rating integer,
                is_holdout boolean DEFAULT false NOT NULL,
                CONSTRAINT pk_style_examples PRIMARY KEY (id),
                CONSTRAINT uq_style_example_id_owner UNIQUE (id, owner_id),
                CONSTRAINT fk_style_example_profile_owner
                    FOREIGN KEY (profile_id, owner_id)
                    REFERENCES style_profiles (id, owner_id) ON DELETE CASCADE,
                CONSTRAINT fk_style_example_draft_owner
                    FOREIGN KEY (draft_version_id, owner_id)
                    REFERENCES draft_versions (id, owner_id) ON DELETE CASCADE,
                CONSTRAINT ck_style_rating_range
                    CHECK (rating IS NULL OR rating BETWEEN 1 AND 5),
                CONSTRAINT ck_style_tags_bounded CHECK (cardinality(tags) <= 32)
            )
            """,
            """
            CREATE TABLE cost_events (
                id uuid DEFAULT gen_random_uuid() NOT NULL,
                owner_id bigint NOT NULL,
                created_at timestamptz DEFAULT CURRENT_TIMESTAMP NOT NULL,
                workflow_id uuid,
                provider_run_id uuid,
                operation varchar(64) NOT NULL,
                amount_rub numeric(12, 4) NOT NULL,
                currency varchar(3) DEFAULT 'RUB' NOT NULL,
                estimated boolean NOT NULL,
                occurred_at timestamptz NOT NULL,
                CONSTRAINT pk_cost_events PRIMARY KEY (id),
                CONSTRAINT uq_cost_event_id_owner UNIQUE (id, owner_id),
                CONSTRAINT fk_cost_workflow_owner
                    FOREIGN KEY (workflow_id, owner_id)
                    REFERENCES content_workflows (id, owner_id)
                    ON DELETE SET NULL (workflow_id),
                CONSTRAINT fk_cost_provider_run_owner
                    FOREIGN KEY (provider_run_id, owner_id)
                    REFERENCES provider_runs (id, owner_id)
                    ON DELETE SET NULL (provider_run_id),
                CONSTRAINT ck_cost_amount_nonnegative CHECK (amount_rub >= 0)
            )
            """,
            """
            CREATE TABLE audit_events (
                id uuid DEFAULT gen_random_uuid() NOT NULL,
                owner_id bigint NOT NULL,
                created_at timestamptz DEFAULT CURRENT_TIMESTAMP NOT NULL,
                workflow_id uuid,
                event_type varchar(128) NOT NULL,
                actor actor NOT NULL,
                object_type varchar(64) NOT NULL,
                object_id uuid,
                trace_id varchar(64),
                metadata_json jsonb DEFAULT '{}'::jsonb NOT NULL,
                CONSTRAINT pk_audit_events PRIMARY KEY (id),
                CONSTRAINT uq_audit_event_id_owner UNIQUE (id, owner_id),
                CONSTRAINT fk_audit_workflow_owner
                    FOREIGN KEY (workflow_id, owner_id)
                    REFERENCES content_workflows (id, owner_id)
                    ON DELETE SET NULL (workflow_id),
                CONSTRAINT ck_audit_metadata_bounded
                    CHECK (octet_length(metadata_json::text) <= 65536)
            )
            """,
            """
            CREATE TABLE library_items (
                id uuid DEFAULT gen_random_uuid() NOT NULL,
                owner_id bigint NOT NULL,
                created_at timestamptz DEFAULT CURRENT_TIMESTAMP NOT NULL,
                updated_at timestamptz DEFAULT CURRENT_TIMESTAMP NOT NULL,
                workflow_id uuid,
                draft_version_id uuid,
                source_document_id uuid,
                item_type varchar(32) NOT NULL,
                title varchar(1000) NOT NULL,
                note text,
                CONSTRAINT pk_library_items PRIMARY KEY (id),
                CONSTRAINT uq_library_item_id_owner UNIQUE (id, owner_id),
                CONSTRAINT fk_library_workflow_owner
                    FOREIGN KEY (workflow_id, owner_id)
                    REFERENCES content_workflows (id, owner_id) ON DELETE CASCADE,
                CONSTRAINT fk_library_draft_owner
                    FOREIGN KEY (draft_version_id, owner_id)
                    REFERENCES draft_versions (id, owner_id) ON DELETE CASCADE,
                CONSTRAINT fk_library_document_owner
                    FOREIGN KEY (source_document_id, owner_id)
                    REFERENCES source_documents (id, owner_id) ON DELETE CASCADE
            )
            """,
            """
            CREATE TABLE deletion_tombstones (
                id uuid DEFAULT gen_random_uuid() NOT NULL,
                owner_id bigint NOT NULL,
                created_at timestamptz DEFAULT CURRENT_TIMESTAMP NOT NULL,
                updated_at timestamptz DEFAULT CURRENT_TIMESTAMP NOT NULL,
                object_type varchar(64) NOT NULL,
                object_id uuid NOT NULL,
                requested_at timestamptz NOT NULL,
                applied_at timestamptz,
                expires_at timestamptz NOT NULL,
                status varchar(32) NOT NULL,
                CONSTRAINT pk_deletion_tombstones PRIMARY KEY (id),
                CONSTRAINT uq_deletion_tombstone_id_owner UNIQUE (id, owner_id),
                CONSTRAINT uq_deletion_target UNIQUE (owner_id, object_type, object_id),
                CONSTRAINT ck_deletion_expiry_after_request
                    CHECK (expires_at >= requested_at)
            )
            """,
            """
            CREATE TABLE backup_runs (
                id uuid DEFAULT gen_random_uuid() NOT NULL,
                owner_id bigint NOT NULL,
                created_at timestamptz DEFAULT CURRENT_TIMESTAMP NOT NULL,
                updated_at timestamptz DEFAULT CURRENT_TIMESTAMP NOT NULL,
                status varchar(32) NOT NULL,
                started_at timestamptz NOT NULL,
                completed_at timestamptz,
                checksum varchar(128),
                object_key varchar(1024),
                safe_error_code varchar(64),
                CONSTRAINT pk_backup_runs PRIMARY KEY (id),
                CONSTRAINT uq_backup_run_id_owner UNIQUE (id, owner_id),
                CONSTRAINT ck_backup_completion_after_start
                    CHECK (completed_at IS NULL OR completed_at >= started_at)
            )
            """,
            """
            CREATE TABLE source_health_events (
                id uuid DEFAULT gen_random_uuid() NOT NULL,
                owner_id bigint NOT NULL,
                created_at timestamptz DEFAULT CURRENT_TIMESTAMP NOT NULL,
                source_id uuid NOT NULL,
                status varchar(32) NOT NULL,
                safe_error_code varchar(64),
                occurred_at timestamptz NOT NULL,
                details_json jsonb DEFAULT '{}'::jsonb NOT NULL,
                CONSTRAINT pk_source_health_events PRIMARY KEY (id),
                CONSTRAINT uq_source_health_event_id_owner UNIQUE (id, owner_id),
                CONSTRAINT fk_source_health_source_owner
                    FOREIGN KEY (source_id, owner_id)
                    REFERENCES sources (id, owner_id) ON DELETE CASCADE,
                CONSTRAINT ck_source_health_details_bounded
                    CHECK (octet_length(details_json::text) <= 16384)
            )
            """,
        )
    )

    _execute_all(
        (
            """
            CREATE FUNCTION reject_draft_version_update()
            RETURNS trigger
            LANGUAGE plpgsql
            AS $$
            BEGIN
                RAISE EXCEPTION 'draft_versions are immutable'
                    USING ERRCODE = '23514',
                          CONSTRAINT = 'trg_draft_versions_immutable';
            END;
            $$
            """,
            """
            CREATE TRIGGER trg_draft_versions_immutable
            BEFORE UPDATE ON draft_versions
            FOR EACH ROW EXECUTE FUNCTION reject_draft_version_update()
            """,
            """
            CREATE FUNCTION enforce_approval_current_reviewed_hash()
            RETURNS trigger
            LANGUAGE plpgsql
            AS $$
            BEGIN
                IF NOT EXISTS (
                    SELECT 1
                    FROM draft_versions AS draft
                    JOIN content_workflows AS workflow
                      ON workflow.id = draft.workflow_id
                     AND workflow.owner_id = draft.owner_id
                    WHERE draft.id = NEW.draft_version_id
                      AND draft.workflow_id = NEW.workflow_id
                      AND draft.owner_id = NEW.owner_id
                      AND draft.body_hash = NEW.content_hash
                      AND workflow.current_version_id = draft.id
                      AND (
                          SELECT review.status
                          FROM review_decisions AS review
                          WHERE review.draft_version_id = draft.id
                            AND review.owner_id = draft.owner_id
                          ORDER BY review.reviewed_at DESC,
                                   review.created_at DESC,
                                   review.id DESC
                          LIMIT 1
                      ) = 'passed'
                ) THEN
                    RAISE EXCEPTION 'approval requires current reviewed hash'
                        USING ERRCODE = '23514',
                              CONSTRAINT = 'ck_approval_current_reviewed_hash';
                END IF;
                RETURN NEW;
            END;
            $$
            """,
            """
            CREATE TRIGGER trg_approval_current_reviewed_hash
            BEFORE INSERT OR UPDATE OF draft_version_id, workflow_id, owner_id, content_hash
            ON approvals
            FOR EACH ROW EXECUTE FUNCTION enforce_approval_current_reviewed_hash()
            """,
            """
            CREATE FUNCTION enforce_approved_workflow_has_current_approval()
            RETURNS trigger
            LANGUAGE plpgsql
            AS $$
            BEGIN
                IF NEW.status = 'approved' AND NOT EXISTS (
                    SELECT 1
                    FROM approvals AS approval
                    JOIN draft_versions AS draft
                      ON draft.id = approval.draft_version_id
                     AND draft.owner_id = approval.owner_id
                    WHERE approval.workflow_id = NEW.id
                      AND approval.owner_id = NEW.owner_id
                      AND approval.revoked_at IS NULL
                      AND approval.draft_version_id = NEW.current_version_id
                      AND approval.content_hash = draft.body_hash
                      AND (
                          SELECT review.status
                          FROM review_decisions AS review
                          WHERE review.draft_version_id = draft.id
                            AND review.owner_id = draft.owner_id
                          ORDER BY review.reviewed_at DESC,
                                   review.created_at DESC,
                                   review.id DESC
                          LIMIT 1
                      ) = 'passed'
                ) THEN
                    RAISE EXCEPTION 'approved workflow requires current approval'
                        USING ERRCODE = '23514',
                              CONSTRAINT = 'ck_workflow_current_approval';
                END IF;
                RETURN NEW;
            END;
            $$
            """,
            """
            CREATE TRIGGER trg_workflow_current_approval
            BEFORE INSERT OR UPDATE OF status, current_version_id, owner_id
            ON content_workflows
            FOR EACH ROW EXECUTE FUNCTION enforce_approved_workflow_has_current_approval()
            """,
            """
            CREATE FUNCTION prevent_non_passing_review_supersession()
            RETURNS trigger
            LANGUAGE plpgsql
            AS $$
            BEGIN
                IF NEW.status <> 'passed' AND EXISTS (
                    SELECT 1
                    FROM approvals AS approval
                    WHERE approval.draft_version_id = NEW.draft_version_id
                      AND approval.owner_id = NEW.owner_id
                      AND approval.revoked_at IS NULL
                ) THEN
                    RAISE EXCEPTION 'non-passing review cannot supersede active approval'
                        USING ERRCODE = '23514',
                              CONSTRAINT = 'ck_review_cannot_stale_approval';
                END IF;
                RETURN NEW;
            END;
            $$
            """,
            """
            CREATE TRIGGER trg_review_cannot_stale_approval
            BEFORE INSERT OR UPDATE OF status, draft_version_id, owner_id
            ON review_decisions
            FOR EACH ROW EXECUTE FUNCTION prevent_non_passing_review_supersession()
            """,
        )
    )


def downgrade() -> None:
    _execute_all(
        (
            "DROP TRIGGER IF EXISTS trg_review_cannot_stale_approval ON review_decisions",
            "DROP FUNCTION IF EXISTS prevent_non_passing_review_supersession()",
            "DROP TRIGGER IF EXISTS trg_workflow_current_approval ON content_workflows",
            "DROP FUNCTION IF EXISTS enforce_approved_workflow_has_current_approval()",
            "DROP TRIGGER IF EXISTS trg_approval_current_reviewed_hash ON approvals",
            "DROP FUNCTION IF EXISTS enforce_approval_current_reviewed_hash()",
            "DROP TRIGGER IF EXISTS trg_draft_versions_immutable ON draft_versions",
            "DROP FUNCTION IF EXISTS reject_draft_version_update()",
            """
            ALTER TABLE content_workflows
            DROP CONSTRAINT IF EXISTS fk_workflow_selected_angle_owner
            """,
            """
            ALTER TABLE content_workflows
            DROP CONSTRAINT IF EXISTS fk_workflow_current_draft_owner
            """,
            "DROP TABLE IF EXISTS source_health_events",
            "DROP TABLE IF EXISTS backup_runs",
            "DROP TABLE IF EXISTS deletion_tombstones",
            "DROP TABLE IF EXISTS library_items",
            "DROP TABLE IF EXISTS audit_events",
            "DROP TABLE IF EXISTS cost_events",
            "DROP TABLE IF EXISTS style_examples",
            "DROP TABLE IF EXISTS style_rules",
            "DROP TABLE IF EXISTS style_profiles",
            "DROP TABLE IF EXISTS publication_jobs",
            "DROP TABLE IF EXISTS approvals",
            "DROP TABLE IF EXISTS review_decisions",
            "DROP TABLE IF EXISTS evidence",
            "DROP TABLE IF EXISTS claims",
            "DROP TABLE IF EXISTS draft_versions",
            "DROP TABLE IF EXISTS provider_runs",
            "DROP TABLE IF EXISTS angles",
            "DROP TABLE IF EXISTS content_workflows",
            "DROP TABLE IF EXISTS digest_items",
            "DROP TABLE IF EXISTS source_payload_cache",
            "DROP TABLE IF EXISTS source_documents",
            "DROP TABLE IF EXISTS sources",
            "DROP TYPE IF EXISTS actor",
            "DROP TYPE IF EXISTS workflow_status",
        )
    )
