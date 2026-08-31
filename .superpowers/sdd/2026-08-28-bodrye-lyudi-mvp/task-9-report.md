# Task 9 report — Versioned source registry and SSRF-safe bounded fetching

## Result

Implemented the Task 9 source boundary without live network access or schema changes. The implementation introduces a versioned initial catalog, a resolver/transport-injected bounded fetcher, and extraction orchestration that treats source text as quoted data.

## TDD evidence

1. Initial RED: `python -m pytest tests/unit/sources tests/security/test_ssrf.py tests/security/test_prompt_injection.py -v` collected no tests and failed with three expected import errors: `ModuleNotFoundError: No module named 'bodrye_bot.domain.sources'`.
2. The first test draft contained a test-only annotation syntax error. It was corrected before accepting RED; the rerun produced the intended missing-production-module failures above.
3. After the first minimal implementation, 10/12 tests passed. The two failures exposed real gaps: `bleach.clean` had made script text visible to the text parser, and a test expectation used a literal backslash-n instead of a newline. The implementation now extracts visible text while ignoring executable HTML elements; the test typo was corrected.
4. Additional RED: a malformed bracketed host (`https://[not-an-ip]/x`) raised `ValueError` from `urlsplit` rather than the typed `SOURCE_BLOCKED` boundary. `_validate_url` now converts malformed URL/port errors into `SafeErrorCode.SOURCE_BLOCKED`.
5. Final focused command: `14 passed in 1.87s`.

## Design and interfaces

- `domain/sources.py` is pure: strict `SourceRole`, `FetchStatus`, and repr-redacted `FetchResult`. Available results bind document identity, exact final URL, SHA-256 of bounded sanitized content, HTTP status, fetch time, and raw expiry exactly 24 hours later. Unavailable results carry only typed safe metadata and no raw payload.
- `sources/catalog.py` supplies `SourceCatalog.initial()` at `source-registry-v1`: Minzdrav manual evidence search; WHO Fact Sheets and News; USPSTF; NICE; Cochrane; three versioned PubMed RSS query records; and one manual Telegram record. Telegram is constrained to owner-forwarded or explicit links and cannot carry `EVIDENCE`; evidence records require explicit host allowlists.
- `sources/fetcher.py` injects `Resolver`, `Transport`, and `now`. It accepts only HTTP(S), rejects credentials/malformed targets/non-allowlisted hosts, validates every returned address with `ipaddress.is_global`, pins the selected address in `TransportRequest`, revalidates every redirect, caps responses at 10 MiB before decoding, and returns unavailable results for HTTP failure/oversize/transport failure. It sends no content to logs and uses 5-second connect, 20-second total, and three-redirect bounds.
- `sources/extraction.py` consumes the typed `LLMProvider.extract` contract. Source text is passed only inside fixed `SOURCE_DATA_BEGIN` / `SOURCE_DATA_END` delimiters with an explicit Russian statement that in-source instructions are data, never commands. `from_url` does not call the LLM when fetch status is unavailable (AC-04).

## Files

- Created `src/bodrye_bot/domain/sources.py`.
- Created `src/bodrye_bot/sources/__init__.py`, `catalog.py`, `fetcher.py`, and `extraction.py`.
- Created `tests/unit/sources/test_catalog.py`, `tests/security/test_ssrf.py`, and `tests/security/test_prompt_injection.py`.
- Created this report.
- Did not modify migrations, ORM shapes, specification, implementation plan, or `Plan.md`.

## Verification

| Command | Result |
|---|---|
| `python -m pytest tests/unit/sources tests/security/test_ssrf.py tests/security/test_prompt_injection.py -v` | 14 passed |
| `python -m pytest tests/unit/domain/test_errors.py tests/unit/providers/test_safe_errors.py tests/contract/test_llm_contract.py tests/architecture/test_scope.py -v` | 44 passed |
| `python -m ruff check .` | All checks passed |
| `python -m mypy src evals` | Success: no issues found in 58 source files |
| `git diff --check` | passed |
| `TEST_DATABASE_URL=postgresql+asyncpg://postgres@127.0.0.1:55432/bodrye_bot_test; python -m pytest -q` | 290 passed in 33.62s |

The full suite was run from a hidden local process solely because the interactive shell terminates foreground commands after 30 seconds. No network target was contacted; all fetch behavior used injected controlled resolver and transport doubles.

## Limitations and self-review

- This task supplies the pure catalog and fetch/extraction boundaries. Durable source repository seeding and audit persistence are intentionally not wired into bootstrap because no source repository implementation exists yet; existing ORM `Source`, `SourceDocument`, and `SourcePayloadCache` shapes were reused rather than changed.
- The fetcher uses a transport contract that receives the validated `pinned_ip`; a future concrete HTTP transport must connect to that IP while preserving the original host/TLS authority. This requirement is now explicit in the typed request and tested at the boundary.
- Raw response bytes are retained only in `FetchResult` with `repr=False`; persistence must use the existing cache expiry constraints when a source repository is added.
- Self-review checked domain import purity, typed safe-error boundaries, credential-bearing URL rejection, all-address DNS validation, redirect revalidation, max-size handling, and LLM isolation for unavailable content.

## Commit

Commit message: `feat: ingest only allowlisted bounded sources`. The final commit object contains this report.

## Fix round 1/5 — reviewer findings

### Fresh RED evidence

1. Added all reviewer-specified covering tests before changing implementation. The first focused run collected 3 tests and stopped at two expected missing interface imports: `SourceCatalogUpdater` and `BodyChunk`.
2. After the first streaming-boundary implementation, the focused run exposed 14 behavior failures: legacy resolver fakes lacked the new deadline argument; byte-buffer fixtures no longer conformed to the body protocol; the original delimiter assertions expected insecure plain framing. These were converted into compliant stream/deadline/encoding test doubles rather than restoring the unsafe API.

### Fixes

- `SafeFetcher` now reads through the `ResponseBody.read_chunk(maximum_bytes)` protocol, requesting at most 65,536 bytes per operation and rejecting as soon as byte 10 MiB + 1 is observed. `HttpResponse.body`, `TransportRequest.url`, `BodyChunk.data`, and URL-bearing `FetchResult` fields are repr-redacted.
- One monotonic deadline spans DNS resolution, every request, every redirect, and streamed reads. The resolver and transport receive remaining budget; connect timeout remains 5 seconds. The deterministic test exhausts DNS plus redirect budget at 22 seconds.
- Every DNS answer must pass explicit checks for global, non-private, non-loopback, non-link-local, non-multicast, non-reserved, and non-unspecified destination status. The tests cover mixed public/private answers, IPv4/IPv6 multicast, and a reserved destination.
- Three redirects remain the maximum; a fourth redirect returns typed unavailable without following it.
- Sanitization preserves the full sanitized document for SHA-256 while deriving the 65,536-character excerpt separately. Two responses with identical excerpts but different suffixes now prove distinct hashes.
- Source LLM framing now base64-encodes the source payload inside fixed `SOURCE_DATA_BASE64_*` markers. A source supplied `SOURCE_DATA_END` cannot terminate the region or inject plain instructions.
- `SourceDefinition.config` is a copied `MappingProxyType`; PubMed updates require a distinct registry version and exactly three non-empty queries. `SourceCatalogUpdater` consumes owner-scoped catalog repository/audit UoW protocols, persists the immutable updated version, writes only version/count metadata with the existing configuration audit event/object, and commits only after both writes. The failure test demonstrates rollback when audit append fails.

### GREEN verification

| Command | Result |
|---|---|
| `python -m pytest tests/unit/sources tests/security/test_ssrf.py tests/security/test_prompt_injection.py -v` | 27 passed in 2.57s |
| `python -m pytest tests/unit/domain/test_errors.py tests/unit/providers/test_safe_errors.py tests/contract/test_llm_contract.py tests/architecture/test_scope.py -q` | 44 passed in 1.55s |
| `python -m ruff check .` | All checks passed |
| `python -m mypy src evals` | Success: no issues found in 58 source files |
| `git diff --check` | passed |
| `TEST_DATABASE_URL=postgresql+asyncpg://postgres@127.0.0.1:55432/bodrye_bot_test; python -m pytest -q` | 303 passed in 34.39s |

### Self-review

The report remains evidence only; no application module imports or reads `.superpowers`. The transport boundary now makes pinning and bounded streaming explicit, while a future concrete transport must honour `pinned_ip`, original authority/TLS host, and passed remaining deadline. No migration, Plan.md, specification, implementation plan, digest behavior, live network access, or automatic publication was changed.
