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
