# Task 10 report — quality-gated morning digest

## Outcome

Implemented a pure, deterministic weekday digest service and an injected delivery worker. No live Telegram, source fetcher, LLM, or network call is used.

## TDD evidence

1. Created the three required test files before the digest package existed.
2. RED: `python -m pytest tests/unit/digest tests/e2e/test_digest_delivery.py -v` collected zero tests and failed with three expected `ModuleNotFoundError: No module named 'bodrye_bot.digest'` errors.
3. GREEN after the minimum service/worker/view implementation: `8 passed in 2.24s`.
4. A follow-up view contract test was added for a missing required card date and HTML safety. RED: it failed because `01.09.2026` was absent from the rendered card. GREEN: after adding the escaped view date, `9 passed in 2.08s`.

## Contracts

- `DigestService.build(candidates, *, digest_date, source_failures=()) -> Digest` is pure domain logic. It groups candidates transitively in this order of identity keys: normalized canonical URL, full SHA-256 content hash, then normalized topic fingerprint.
- Duplicate groups retain every unique normalized provenance URL and every source role. Grouped score dimensions use the best available component signal, while representative editorial fields are selected deterministically.
- `DigestWeightConfig` is frozen, inspectable, versioned (`digest-scoring-v1`) and validates the six literal components and a sum of one. Default weights are relevance 0.25, freshness 0.15, source authority 0.20, audience fit 0.15, novelty 0.15, preliminary risk 0.10.
- Only cards scoring at least 0.70 are selected, in deterministic descending-score order, capped at five. Empty digests are intentional. Card actions are the canonical `Развить`, `Сохранить`, `Не интересно`, `Источник`.
- `render_digest` uses Telegram-safe escaped HTML. It never renders error codes/details for unavailable sources; source URLs are rendered only when they are valid owner-facing `http`/`https` URLs and are escaped in attribute context.
- `DigestWorker.run_due(now)` converts aware timestamps to Moscow UTC+3, runs only Monday-Friday from 10:00, and exposes delivery time plus a `late` flag for attempts after 10:10. All loader, atomic claim, Telegram delivery, and delivery-record calls include `owner_id`.
- `DigestRunRepository.claim(owner_id, digest_date)` is the production idempotency boundary. The worker contains no in-process seen-date state: an already claimed date returns without loading or delivering, including for an empty or partial digest.

## Files

- `src/bodrye_bot/digest/__init__.py`
- `src/bodrye_bot/digest/service.py`
- `src/bodrye_bot/digest/views.py`
- `src/bodrye_bot/digest/worker.py`
- `tests/unit/digest/test_deduplication.py`
- `tests/unit/digest/test_ranking.py`
- `tests/e2e/test_digest_delivery.py`

## Verification

| Command | Fresh result |
| --- | --- |
| `python -m pytest tests/unit/digest tests/e2e/test_digest_delivery.py -v` | 9 passed |
| `python -m pytest tests/unit/digest tests/e2e/test_digest_delivery.py tests/unit/sources tests/integration/test_source_repository.py -v` | 17 passed, 4 skipped without `TEST_DATABASE_URL` |
| `TEST_DATABASE_URL=postgresql+asyncpg://postgres@127.0.0.1:55432/bodrye_bot_test; python -m pytest tests/integration/test_source_repository.py -v` | 4 passed |
| `TEST_DATABASE_URL=postgresql+asyncpg://postgres@127.0.0.1:55432/bodrye_bot_test; python -m pytest -q` | 321 passed in 53.09s |
| `python -m ruff check .` | All checks passed |
| `python -m mypy src evals` | Success: no issues found in 63 source files |
| `git diff --check` | passed; the only pre-existing unrelated modification is `Plan.md` |

## Commit

Commit message: `feat: deliver quality-gated morning digest` (final hash is recorded in the task handoff).

## Limitations and follow-up wiring

- The worker deliberately depends on a `DigestRunRepository` protocol. A later database adapter must implement its atomic `(owner_id, digest_date)` claim/record invariant; no migration was added because this task's required persistence seam is truthful and the existing schema has no digest-run table.
- Candidate extraction and source loading stay outside Task 10. The loader supplies already fetched, owner-scoped candidates and safe failure statuses; unavailable source content is not reconstructed.
- Moscow is represented with the standard-library fixed UTC+3 `Europe/Moscow` timezone value. This avoids a runtime dependency on IANA `tzdata`, which is absent from the project Windows Python environment; Moscow has had no DST transitions since 2014.

## Self-review

- Checked against the brief: dedupe order/transitivity, named score dimensions/configuration/threshold, max-five/no-padding, required actions, escaped views, weekday Moscow timing/observability, injected atomic owner/date idempotency, owner qualification, and no live integrations are covered.
- No Task 11 claim/evidence/medical policy behavior was added.
- `Plan.md`, canonical spec, and implementation plan were not edited or staged by this task.
- A separate reviewer subagent was intentionally not dispatched because the controller explicitly prohibited subagents for this task; the scoped diff and fresh full suite were reviewed locally instead.

## Review round 1 — durability and reproducibility repair

### RED/GREEN evidence

1. New review tests first produced two expected collection errors: missing `PreliminaryRisk`/`ScoringSnapshot` and missing `SqlAlchemyDigestRunRepository`.
2. After the initial implementation, focused digest tests reached `20 passed, 2 skipped` without PostgreSQL configuration.
3. With `TEST_DATABASE_URL`, the new PostgreSQL tests first exposed a fixed test owner reused from a prior local run; using randomized owners made the owner-isolation test independent. Green result: `22 passed in 3.36s` for digest unit/e2e plus digest-run integration tests.
4. Full suite first found three migration-contract failures because `tests/integration/test_migrations.py` did not list the new required `digest_runs` table. The migration table contract was extended; final full suite is `334 passed in 54.68s`.

### Changes

- Added immutable `ScoringSnapshot`; its ID is a SHA-256 fingerprint of canonical version, weights, threshold, maximum-card count, and aggregation strategy. Cards retain the full snapshot, raw score, display score, and components.
- Enforced a minimum selection threshold of 0.70 and at most five cards. Threshold comparison uses raw score before rounding.
- Replaced caller-supplied risk score with validated `PreliminaryRisk` (`green`, `yellow`, `red`) and a deterministic safety component (1.0, 0.5, 0.0). Candidate source content/URLs are excluded from dataclass repr.
- Normalized query order, deterministic representative selection, date conversion, and required 2–3 sentence/required-field validation.
- Added `DigestRun` ORM model and Alembic `0009_digest_runs` migration with unique `(owner_id, digest_date)` and allowed-state check. Added repository/UoW wiring and an atomic PostgreSQL `INSERT .. ON CONFLICT` claim.
- Processing leases expire conservatively to `delivery_unknown`; known pre-send outcomes are releaseable; known successful delivery records actual injected-clock time and lateness. Generic delivery exceptions are `delivery_unknown` and never silently retried.
- View now lists escaped source names with Russian safe statuses, source roles, score components, and scoring version.

### New verification

| Command | Fresh result |
| --- | --- |
| `DATABASE_URL=... python -m alembic downgrade 0008_style_profile_binding; python -m alembic upgrade head` | 0009 downgrade and re-upgrade passed |
| `python -m ruff check .` | All checks passed |
| `python -m mypy src evals` | Success: no issues found in 65 source files |
| `TEST_DATABASE_URL=... python -m pytest tests/integration/test_digest_runs.py tests/unit/digest tests/e2e/test_digest_delivery.py -q` | 22 passed in 3.36s |
| `TEST_DATABASE_URL=... python -m pytest -q` | 334 passed in 54.68s |

### Remaining boundary

The production composition root has not yet been created in the scaffold, so `SqlAlchemyDigestRunRepository` is exposed through `SqlAlchemyUnitOfWork.digest_runs` for the future worker composition. No live Telegram/source/LLM behavior was introduced.

## Review round 2 — durable attempt fencing

### RED/GREEN evidence

- RED: new focused tests failed collection with the expected missing `SqlAlchemyDigestRunStore` import. They specified committed claim recovery, expired lease → `delivery_unknown`, retry token rotation, and stale-token rejection.
- GREEN: `TEST_DATABASE_URL=... python -m pytest tests/integration/test_digest_run_store.py tests/unit/digest tests/e2e/test_digest_delivery.py -q` returned `24 passed in 2.43s`.
- Migration: `DATABASE_URL=... python -m alembic downgrade 0009_digest_runs; python -m alembic upgrade head` completed the `0010 → 0009 → 0010` round-trip.
- Full PostgreSQL suite: `338 passed in 40.45s`.

### Changes

- Added immutable `attempt_id` to `DigestRun` in new migration `0010_digest_run_attempt`; applied `0009` was not edited.
- Added `SqlAlchemyDigestRunStore`, whose `claim`, lease sweep and terminal lifecycle methods each own `session_factory.begin()` and therefore commit before or after Telegram I/O. `claim` rotates the token for a durable retry, while every terminal state write is fenced by owner/date/processing/attempt_id.
- Lease sweeps operate on every overdue `processing` row, including previous dates, and turn it into durable `delivery_unknown`; it cannot silently retry.
- Worker now sweeps before due evaluation and passes the durable attempt token to retryable/unknown/delivered writes. Actual injected-clock completion time remains the source of late status.
- Merged-card safety uses the minimum risk-safety component across all provenance while showing the worst risk label. Snapshot weights and card snapshots are recursively immutable at the exposed weights/components boundary. Representative tie-breaking covers all owner-visible candidate fields.

### Verification

| Command | Fresh result |
| --- | --- |
| `python -m ruff check .` | All checks passed |
| `python -m mypy src evals` | Success: no issues found in 66 source files |
| `git diff --check` | passed |
| PostgreSQL focused digest | 24 passed in 2.43s |
| PostgreSQL full suite | 338 passed in 40.45s |

## Review round 3 — conservative terminal fencing

- RED: `tests/unit/digest/test_review_round_three.py` failed 2/2 as expected: the old aggregation ID did not describe risk-min behavior, and a false delivered fence still returned success.
- GREEN: focused digest unit/e2e/store suite passed `28 passed in 2.88s`; full PostgreSQL suite passed `340 passed in 41.57s`; Ruff and mypy were clean.
- Scoring now identifies `component_max_risk_min_v2`, including it in the immutable snapshot fingerprint. Ordinary components aggregate by max; provenance risk safety aggregates by min.
- A rejected `mark_delivered` fence now raises redacted `SafeErrorCode.DELIVERY_UNKNOWN`; rejected retry/unknown fences receive the same conservative treatment.
- Added independent-store PostgreSQL race coverage (exactly one durable claim) and prior-day lease-sweep coverage. Removed the UoW `digest_runs` path and public legacy repository export so lifecycle users must use the independently committing store.
