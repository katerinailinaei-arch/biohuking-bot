# Bodrye Lyudi MVP Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Создать приватного Telegram-агента, который собирает проверяемые темы, помогает Кети выработать новый стиль канала, проводит медицинский review, готовит черновики и публикует только явно утверждённые версии.

**Architecture:** Модульный Python-монолит разделён на доменные модули и порты; процессы `bot` и `worker` координируются только через PostgreSQL. Внешние системы (Groq, OpenAI, Telegram, web sources, S3 backup) подключаются адаптерами, а все решения о доступе, workflow, evidence, approval и delivery принимает доменное ядро.

**Tech Stack:** Python 3.12+, aiogram 3.x, SQLAlchemy 2.x async, Alembic, PostgreSQL 16+, asyncpg, Pydantic 2, pydantic-settings, httpx, structlog, pytest, pytest-asyncio, Hypothesis, Ruff, mypy strict, Docker Compose.

**Spec:** `docs/superpowers/specs/2026-08-28-bodrye-lyudi-mvp-design.md`

## Global Constraints

- Перед исполнением прочитать спецификацию целиком и создать новый worktree от `master` через `superpowers:using-git-worktrees`; ветку и worktree `feature/editorial-core` не изменять и не использовать как основу.
- Python 3.12+, PostgreSQL 16+, aiogram 3.x, SQLAlchemy 2.x async, Alembic, asyncpg, Pydantic 2, pydantic-settings, httpx, structlog, pytest, pytest-asyncio, Ruff и mypy strict.
- Единственный пользователь — Telegram ID Кети из allowlist; owner check выполняется до чтения объекта.
- Все UUID и timestamps создаёт сервер; время хранится в UTC, пользовательское время показывается в `Europe/Moscow`.
- Пользовательские ошибки — на русском, с `trace_id`, без stack trace, секретов, полного prompt или чувствительного контента.
- Ни один LLM-ответ не считается доверенным до Pydantic- и domain-валидации; неизвестные поля и enum отклоняются.
- Конкурентные Telegram-каналы используются только как `topic`, `format` или `anti-example`, никогда как медицинское доказательство.
- Любой `red`, `incomplete`, `missing provenance` или stale review блокирует approval и publication.
- Approval привязан к точным `draft_version_id + content_hash`; approval не планирует и не публикует пост.
- `DELIVERY_UNKNOWN` никогда не получает автоматический retry.
- Groq Free — стартовый runtime; OpenAI реализован, но выключен до eval, cost guard и явной смены конфигурации.
- Soft budget — 3 500 ₽/месяц, hard budget — 5 000 ₽/месяц с учётом VPS; платный fallback без подтверждения запрещён.
- Публичный текст не содержит выдуманного личного опыта; редакционное «мы» разрешено только для реально выполненных действий.
- В MVP отсутствуют voice, изображения, Stories/Reels, Instagram, афиша, реклама, мемы/UGC, сообщество, analytics и автономная публикация.
- После каждой задачи проходят её точечные тесты; перед каждым phase gate проходят `ruff`, `mypy` и весь накопленный test suite.

---

## File map

Файлы создаются по мере задач; каждый модуль имеет одну ответственность.

```text
pyproject.toml                         dependencies, quality tools, package metadata
.env.example                          names only, no secrets
alembic.ini                           migration runner
Dockerfile                            immutable app image
compose.yaml                          local bot/worker/postgres profile
README.md                              local setup and operator commands
.github/workflows/ci.yml              lint, types, unit, integration, migration checks

src/bodrye_bot/config.py              validated environment configuration
src/bodrye_bot/bootstrap.py           dependency composition, no business rules
src/bodrye_bot/main_bot.py            aiogram long-polling entrypoint
src/bodrye_bot/main_worker.py         durable worker entrypoint
src/bodrye_bot/healthcheck.py         machine-readable health gate

src/bodrye_bot/domain/common.py       UUID/time/hash value helpers
src/bodrye_bot/domain/errors.py       safe domain/provider error taxonomy
src/bodrye_bot/domain/workflow.py     statuses, transitions and version invariants
src/bodrye_bot/domain/medical.py      claims, evidence and risk policy
src/bodrye_bot/domain/style.py        style profiles, rules and calibration gate
src/bodrye_bot/domain/publication.py  approval/schedule/delivery policies
src/bodrye_bot/domain/sources.py      source roles and fetch decisions

src/bodrye_bot/ports/llm.py           typed provider contract
src/bodrye_bot/ports/repositories.py  owner-scoped persistence protocols and UoW
src/bodrye_bot/ports/telegram.py      channel delivery contract
src/bodrye_bot/ports/clock.py         deterministic time
src/bodrye_bot/ports/blob_store.py    encrypted backup object storage contract

src/bodrye_bot/db/base.py             SQLAlchemy metadata/session factory
src/bodrye_bot/db/models/*.py         focused ORM tables by domain
src/bodrye_bot/db/repositories/*.py   owner-scoped repository implementations
src/bodrye_bot/db/uow.py              async transaction boundary
src/bodrye_bot/db/migrations/*        Alembic environment and revisions

src/bodrye_bot/identity/service.py    owner authorization
src/bodrye_bot/identity/sensitive.py  transient sensitive-input warning/consent
src/bodrye_bot/telegram/router.py     command/callback routing
src/bodrye_bot/telegram/views.py      escaped Russian messages and keyboards
src/bodrye_bot/telegram/onboarding.py production readiness conversation

src/bodrye_bot/providers/llm_base.py  schema validation, retry, usage capture
src/bodrye_bot/providers/groq.py      Groq adapter
src/bodrye_bot/providers/openai.py    disabled-by-default OpenAI adapter
src/bodrye_bot/providers/telegram.py  Bot API adapter

src/bodrye_bot/style/calibration.py   8–10 topic calibration workflow
src/bodrye_bot/style/context.py       bounded StyleContext selection
src/bodrye_bot/style/learning.py      explicit/repeated edit rule proposals
src/bodrye_bot/sources/catalog.py     versioned initial registry and PubMed queries
src/bodrye_bot/sources/fetcher.py     SSRF-safe bounded fetch
src/bodrye_bot/sources/extraction.py  source extraction and provenance
src/bodrye_bot/digest/service.py      dedupe, ranking and weekday delivery
src/bodrye_bot/medical/review.py      claim/evidence review orchestration
src/bodrye_bot/editorial/service.py   extraction, angles, draft and edit flow
src/bodrye_bot/publication/service.py approval, scheduling and manual resolution
src/bodrye_bot/publication/worker.py  lease, send and conservative delivery state
src/bodrye_bot/memory/service.py      library, retention, deletion and tombstones
src/bodrye_bot/operations/*.py        audit, costs, quota, metrics, alerts and health

evals/dataset.jsonl                   versioned model/style/safety fixtures
evals/run.py                          blind provider comparison runner
evals/report.py                       deterministic gate report

deploy/compose.prod.yaml              hardened Beget services
deploy/backup/Dockerfile              pg_dump + age + S3 client image
deploy/backup/backup.sh               encrypted daily backup and retention
deploy/backup/restore_test.sh         monthly isolated restore and tombstone replay
deploy/beget/harden.sh                idempotent Ubuntu/SSH/UFW hardening
deploy/beget/OPERATIONS.md             deploy, rollback, backup and incident runbook

tests/unit/**                          pure domain/application tests
tests/integration/**                   PostgreSQL repositories, constraints, leases
tests/contract/**                      Groq/OpenAI normalized semantics
tests/security/**                      auth, SSRF, injection, redaction
tests/e2e/**                           owner Telegram workflow with fakes/test bot
```

### Task 1: Reproducible project foundation and validated configuration

**Files:**
- Create: `pyproject.toml`, `.env.example`, `README.md`, `.github/workflows/ci.yml`
- Create: `src/bodrye_bot/__init__.py`, `src/bodrye_bot/config.py`
- Test: `tests/unit/test_config.py`, `tests/architecture/test_scope.py`

**Interfaces:**
- Consumes: environment variables named in specification sections 14, 19, 20 and 25.
- Produces: `Settings`, `ProviderName`, `get_settings() -> Settings`; quality commands used by every later task.

- [ ] **Step 1: Write failing configuration and scope tests**

```python
def test_settings_reject_paid_fallback_and_invalid_budget(monkeypatch):
    monkeypatch.setenv("TELEGRAM_OWNER_ID", "42")
    monkeypatch.setenv("TELEGRAM_CHANNEL_ID", "-100123")
    monkeypatch.setenv("LLM_PROVIDER", "groq")
    monkeypatch.setenv("LLM_MODEL", "openai/gpt-oss-120b")
    monkeypatch.setenv("PAID_FALLBACK_ENABLED", "true")
    monkeypatch.setenv("BUDGET_SOFT_RUB", "5100")
    monkeypatch.setenv("BUDGET_HARD_RUB", "5000")
    with pytest.raises(ValidationError):
        Settings()

def test_mvp_source_has_no_removed_capability_modules():
    banned = {"voice", "stories", "instagram", "ads", "event_calendar", "userbot"}
    paths = Path("src").rglob("*.py")
    identifiers = {
        token
        for path in paths
        for token in re.findall(r"[a-z_]+", path.read_text().lower())
    }
    found = banned & identifiers
    assert found == set()
```

- [ ] **Step 2: Run tests and confirm the missing package failure**

Run: `python -m pytest tests/unit/test_config.py tests/architecture/test_scope.py -v`

Expected: FAIL because `bodrye_bot.config.Settings` does not exist.

- [ ] **Step 3: Add the package, pinned dependency ranges and strict tool configuration**

```toml
[project]
name = "bodrye-bot"
requires-python = ">=3.12"
dependencies = [
  "aiogram>=3.20,<4", "sqlalchemy[asyncio]>=2.0,<3", "alembic>=1.15,<2",
  "asyncpg>=0.30,<1", "pydantic>=2.11,<3", "pydantic-settings>=2.9,<3",
  "httpx>=0.28,<1", "structlog>=25,<26", "openai>=1.100,<3",
  "feedparser>=6.0,<7", "bleach>=6.2,<7", "boto3>=1.40,<2"
]

[dependency-groups]
dev = [
  "pytest>=8.4,<9", "pytest-asyncio>=1.1,<2", "hypothesis>=6.138,<7",
  "ruff>=0.12,<1", "mypy>=1.17,<2", "testcontainers[postgres]>=4.12,<5",
  "types-boto3>=1.40,<2"
]

[tool.mypy]
python_version = "3.12"
strict = true

[tool.ruff]
target-version = "py312"
line-length = 100
```

```python
class ProviderName(StrEnum):
    GROQ = "groq"
    OPENAI = "openai"

class Settings(BaseSettings):
    telegram_owner_id: int
    telegram_channel_id: int
    llm_provider: ProviderName = ProviderName.GROQ
    llm_model: str = "openai/gpt-oss-120b"
    paid_fallback_enabled: Literal[False] = False
    budget_soft_rub: Decimal = Decimal("3500")
    budget_hard_rub: Decimal = Decimal("5000")
    timezone: Literal["Europe/Moscow"] = "Europe/Moscow"

    @model_validator(mode="after")
    def validate_budget(self) -> "Settings":
        if self.budget_soft_rub >= self.budget_hard_rub:
            raise ValueError("soft budget must be below hard budget")
        return self
```

- [ ] **Step 4: Run the foundation gate**

Run: `python -m pytest tests/unit/test_config.py tests/architecture/test_scope.py -v`

Expected: PASS, including AC-20 scope guard.

- [ ] **Step 5: Commit**

```powershell
git add pyproject.toml .env.example README.md .github src tests
git commit -m "build: establish strict MVP foundation"
```

### Task 2: Safe errors, identifiers, hashes and workflow state machine

**Files:**
- Create: `src/bodrye_bot/domain/common.py`, `src/bodrye_bot/domain/errors.py`, `src/bodrye_bot/domain/workflow.py`
- Test: `tests/unit/domain/test_errors.py`, `tests/unit/domain/test_workflow.py`, `tests/property/test_workflow_transitions.py`

**Interfaces:**
- Consumes: no infrastructure types.
- Produces: `SafeError`, `SafeErrorCode`, `WorkflowStatus`, `WorkflowState`, `Transition`, `WorkflowPolicy.transition(state, target, actor) -> WorkflowState`, `content_hash(text) -> str`.

- [ ] **Step 1: Write failing state, hash and safe-error tests**

```python
def test_schedule_requires_matching_current_approval():
    state = workflow_state(status=WorkflowStatus.APPROVED, current_hash="new", approval_hash="old")
    with pytest.raises(SafeError) as caught:
        WorkflowPolicy().transition(state, WorkflowStatus.SCHEDULED, Actor.OWNER)
    assert caught.value.code is SafeErrorCode.APPROVAL_STALE
    assert caught.value.trace_id

@given(st.sampled_from(list(WorkflowStatus)), st.sampled_from(list(WorkflowStatus)))
def test_unknown_transition_never_mutates_status(source, target):
    state = workflow_state(status=source)
    try:
        result = WorkflowPolicy().transition(state, target, Actor.OWNER)
    except SafeError:
        assert state.status is source
    else:
        assert (source, target) in ALLOWED_TRANSITIONS
        assert result.status is target
```

- [ ] **Step 2: Run tests and confirm missing domain types**

Run: `python -m pytest tests/unit/domain tests/property/test_workflow_transitions.py -v`

Expected: FAIL on imports.

- [ ] **Step 3: Implement exact states, transitions and Russian safe errors**

```python
class WorkflowStatus(StrEnum):
    INGESTED = "ingested"
    EXTRACTED = "extracted"
    EXTRACTION_CONFIRMED = "extraction_confirmed"
    CLAIMS_REVIEW_PENDING = "claims_review_pending"
    CLAIMS_REVIEW_PASSED = "claims_review_passed"
    CLAIMS_REVIEW_BLOCKED = "claims_review_blocked"
    ANGLES_READY = "angles_ready"
    ANGLE_SELECTED = "angle_selected"
    DRAFT = "draft"
    DRAFT_REVIEW_PENDING = "draft_review_pending"
    DRAFT_REVIEW_PASSED = "draft_review_passed"
    DRAFT_REVIEW_BLOCKED = "draft_review_blocked"
    APPROVED = "approved"
    SCHEDULED = "scheduled"
    PROCESSING = "processing"
    PUBLISHED = "published"
    FAILED = "failed"
    DELIVERY_UNKNOWN = "delivery_unknown"
    CANCELLED = "cancelled"
    REJECTED = "rejected"

@dataclass(frozen=True)
class SafeError(Exception):
    code: SafeErrorCode
    message_ru: str
    preserved_ru: str
    next_action_ru: str
    trace_id: str = field(default_factory=lambda: uuid4().hex)
```

Implement the specification transition matrix verbatim, including `REJECTED` only before `PROCESSING`, manual-only exits from `DELIVERY_UNKNOWN`, and invalidation of current review/approval when a new draft version is created.

- [ ] **Step 4: Run domain and property tests**

Run: `python -m pytest tests/unit/domain tests/property/test_workflow_transitions.py -v`

Expected: PASS; every forbidden pair either raises `SafeError` or leaves state unchanged.

- [ ] **Step 5: Commit**

```powershell
git add src/bodrye_bot/domain tests/unit/domain tests/property
git commit -m "feat: enforce editorial workflow invariants"
```

### Task 3: PostgreSQL schema, migrations and database constraints

**Files:**
- Create: `alembic.ini`, `src/bodrye_bot/db/base.py`, `src/bodrye_bot/db/models/*.py`
- Create: `src/bodrye_bot/db/migrations/env.py`, `src/bodrye_bot/db/migrations/versions/0001_initial.py`
- Create: `compose.yaml`
- Test: `tests/integration/test_migrations.py`, `tests/integration/test_constraints.py`

**Interfaces:**
- Consumes: domain enums and `content_hash()` from Task 2.
- Produces: `Base`, `async_session_factory(settings)`, ORM models for every entity in spec section 9, revision `0001_initial`.

- [ ] **Step 1: Write failing migration and invariant tests**

```python
async def test_migration_creates_required_tables(pg_engine):
    await migrate_to_head(pg_engine)
    names = set(await table_names(pg_engine))
    assert REQUIRED_TABLES <= names

async def test_one_version_number_and_one_idempotency_key(pg_session):
    pg_session.add_all([draft(workflow_id=W, version_number=1), draft(workflow_id=W, version_number=1)])
    with pytest.raises(IntegrityError):
        await pg_session.commit()
```

`REQUIRED_TABLES` must include sources, source_documents, source_payload_cache, digest_items, content_workflows, claims, evidence, angles, draft_versions, review_decisions, approvals, publication_jobs, style_profiles, style_rules, style_examples, provider_runs, cost_events, audit_events, library_items, deletion_tombstones, backup_runs and source_health_events.

- [ ] **Step 2: Start PostgreSQL and verify tests fail before migration exists**

Run: `docker compose up -d postgres`

Run: `python -m pytest tests/integration/test_migrations.py tests/integration/test_constraints.py -v`

Expected: FAIL because Alembic metadata/revision is missing.

- [ ] **Step 3: Implement focused ORM models and initial migration**

Every owner-owned table includes `id UUID`, `owner_id BIGINT`, `created_at TIMESTAMPTZ`; mutable tables include `updated_at`. Add named constraints:

```python
UniqueConstraint("workflow_id", "version_number", name="uq_draft_workflow_version")
UniqueConstraint("idempotency_key", name="uq_publication_idempotency")
Index("uq_publication_message", "telegram_message_id", unique=True,
      postgresql_where=text("telegram_message_id IS NOT NULL"))
Index("uq_active_approval", "workflow_id", unique=True,
      postgresql_where=text("revoked_at IS NULL"))
CheckConstraint("version >= 1", name="ck_workflow_version_positive")
```

Use foreign keys with delete behavior matching section 17, JSONB for bounded metadata only, ARRAY for enum/string lists, and UTC-aware timestamps. `approvals.workflow_id` is a denormalized constraint column tied to the draft's workflow by a composite foreign key; this makes the required unique active approval enforceable in PostgreSQL. `source_payload_cache` stores the bounded raw payload with `expires_at <= fetched_at + 24 hours` and is excluded from logs/audit. Never persist a full prompt, provider secret or unrestricted raw source body in operational tables.

- [ ] **Step 4: Verify upgrade, downgrade, upgrade and constraints**

Run: `python -m pytest tests/integration/test_migrations.py tests/integration/test_constraints.py -v`

Expected: PASS after clean `upgrade head`, `downgrade base`, `upgrade head`.

- [ ] **Step 5: Commit**

```powershell
git add alembic.ini compose.yaml src/bodrye_bot/db tests/integration
git commit -m "feat: add durable PostgreSQL schema"
```

### Task 4: Owner-scoped repositories, unit of work and audit trail

**Files:**
- Create: `src/bodrye_bot/ports/repositories.py`, `src/bodrye_bot/db/uow.py`
- Create: `src/bodrye_bot/db/repositories/*.py`, `src/bodrye_bot/operations/audit.py`
- Test: `tests/integration/test_repository_ownership.py`, `tests/integration/test_uow.py`, `tests/integration/test_audit.py`

**Interfaces:**
- Consumes: ORM models from Task 3 and `SafeError` from Task 2.
- Produces: `UnitOfWork`, `WorkflowRepository.get(owner_id, workflow_id)`, `WorkflowRepository.save(workflow, expected_version)`, `AuditWriter.record(event)`, all async.

- [ ] **Step 1: Write failing cross-owner and optimistic-lock tests**

```python
async def test_repository_checks_owner_inside_query(uow, seeded_workflow):
    with pytest.raises(SafeError) as caught:
        await uow.workflows.get(owner_id=999, workflow_id=seeded_workflow.id)
    assert caught.value.code is SafeErrorCode.OWNER_FORBIDDEN

async def test_stale_update_is_rejected(uow, seeded_workflow):
    first = await uow.workflows.get(42, seeded_workflow.id)
    second = await uow.workflows.get(42, seeded_workflow.id)
    await uow.workflows.save(replace(first, status=WorkflowStatus.EXTRACTED), expected_version=1)
    with pytest.raises(ConcurrentUpdate):
        await uow.workflows.save(second, expected_version=1)
```

- [ ] **Step 2: Run tests and confirm missing repositories**

Run: `python -m pytest tests/integration/test_repository_ownership.py tests/integration/test_uow.py tests/integration/test_audit.py -v`

Expected: FAIL on imports.

- [ ] **Step 3: Implement owner-in-query repositories and atomic audit**

```python
class WorkflowRepository(Protocol):
    async def get(self, owner_id: int, workflow_id: UUID) -> WorkflowState: ...
    async def save(self, workflow: WorkflowState, expected_version: int) -> None: ...

class UnitOfWork(Protocol):
    workflows: WorkflowRepository
    audit: AuditWriter
    async def __aenter__(self) -> "UnitOfWork": ...
    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None: ...
    async def commit(self) -> None: ...
```

Use `WHERE id=:id AND owner_id=:owner_id` for reads and `WHERE version=:expected_version` for updates. Record every state transition, configuration change, rule decision, approval, schedule, deletion, manual delivery resolution and backup result in the same database transaction as its domain mutation.

- [ ] **Step 4: Run repository tests**

Run: `python -m pytest tests/integration/test_repository_ownership.py tests/integration/test_uow.py tests/integration/test_audit.py -v`

Expected: PASS, proving AC-01 persistence isolation.

- [ ] **Step 5: Commit**

```powershell
git add src/bodrye_bot/ports src/bodrye_bot/db src/bodrye_bot/operations tests/integration
git commit -m "feat: isolate owner data and audit mutations"
```

### Task 5: Telegram owner shell, callbacks, safe errors and onboarding

**Files:**
- Create: `src/bodrye_bot/identity/service.py`, `src/bodrye_bot/identity/sensitive.py`, `src/bodrye_bot/telegram/router.py`
- Create: `src/bodrye_bot/telegram/views.py`, `src/bodrye_bot/telegram/onboarding.py`
- Create: `src/bodrye_bot/main_bot.py`, `src/bodrye_bot/bootstrap.py`
- Test: `tests/unit/identity/test_authorization.py`, `tests/unit/identity/test_sensitive_input.py`, `tests/security/test_callback_ownership.py`, `tests/e2e/test_onboarding.py`

**Interfaces:**
- Consumes: `Settings`, repositories, `SafeError` and workflow policy.
- Produces: `OwnerGuard.authorize(telegram_id)`, opaque callback codec, `/start`, `/status`, `/settings`, `/sources`, `/style`, `/costs`, `/help`, onboarding readiness result.

- [ ] **Step 1: Write failing authorization and onboarding tests**

```python
async def test_unknown_user_gets_neutral_denial_without_object_lookup(bot, repo_spy):
    answer = await bot.handle(message(user_id=999, text="/status"))
    assert answer.text == "Доступ закрыт. Если это ошибка, проверьте Telegram ID владельца."
    repo_spy.assert_not_called()

async def test_onboarding_reports_each_readiness_gate(bot):
    result = await bot.handle(message(user_id=42, text="/start"))
    assert result.gates == {"database", "channel", "provider", "sources", "style"}
    assert result.ready is False

async def test_possible_medical_record_stays_transient_until_explicit_consent(service):
    result = await service.inspect(owner_id=42, payload="Мои анализы: ФИО, дата рождения...")
    assert result.requires_confirmation is True
    assert await service.permanent_payload(result.transient_id) is None
```

- [ ] **Step 2: Run tests and confirm missing Telegram application layer**

Run: `python -m pytest tests/unit/identity tests/security/test_callback_ownership.py tests/e2e/test_onboarding.py -v`

Expected: FAIL on imports.

- [ ] **Step 3: Implement guard-first routing and escaped Russian views**

```python
class OwnerGuard:
    def __init__(self, owner_id: int) -> None:
        self._owner_id = owner_id

    def authorize(self, telegram_id: int) -> int:
        if not hmac.compare_digest(str(telegram_id), str(self._owner_id)):
            raise owner_forbidden()
        return self._owner_id
```

Callbacks carry only action name plus random opaque record ID and signed expiry; handlers re-authorize the sender, load by `owner_id`, and rerun domain policy. `render_safe_error()` must show what happened, what is preserved, allowed next action and trace ID. Implement every minimum code from spec section 18: `owner_forbidden`, `source_unavailable`, `source_blocked`, `extraction_failed`, `llm_timeout`, `llm_rate_limit`, `llm_quota_exhausted`, `llm_unavailable`, `llm_invalid_output`, `medical_review_incomplete`, `style_profile_not_ready`, `approval_stale`, `publication_failed`, `delivery_unknown`, `backup_stale`, `internal_error`.

`SensitiveInputGuard` detects likely medical records, analyses, diagnoses or subscriber data before durable write. It keeps the payload in an in-memory/TTL transient store, displays a warning, persists only after the exact `Сохранить несмотря на предупреждение` callback, and destroys the transient value on cancel or expiry. Onboarding stores no secret in Telegram and marks readiness only after database, channel admin/send capability, provider, approved source queries and active style gate succeed.

- [ ] **Step 4: Run Telegram security/e2e tests**

Run: `python -m pytest tests/unit/identity tests/security/test_callback_ownership.py tests/e2e/test_onboarding.py -v`

Expected: PASS for AC-01, AC-02 and AC-17.

- [ ] **Step 5: Commit**

```powershell
git add src/bodrye_bot/identity src/bodrye_bot/telegram src/bodrye_bot/main_bot.py src/bodrye_bot/bootstrap.py tests
git commit -m "feat: add owner-only Telegram shell"
```

### Task 6: Typed LLM contract, resilient adapters and usage capture

**Files:**
- Create: `src/bodrye_bot/ports/llm.py`, `src/bodrye_bot/providers/llm_base.py`
- Create: `src/bodrye_bot/providers/groq.py`, `src/bodrye_bot/providers/openai.py`
- Create: `src/bodrye_bot/operations/usage.py`
- Test: `tests/contract/test_llm_contract.py`, `tests/unit/providers/test_retry.py`, `tests/unit/providers/test_schema_validation.py`

**Interfaces:**
- Consumes: `Settings`, safe errors and `ProviderRun` persistence.
- Produces: typed request/response models; `LLMProvider` methods `extract`, `classify_claims`, `synthesize_evidence`, `propose_angles`, `generate_draft`, `assess_change`, `infer_style_candidates`, `healthcheck`, `estimate_or_report_usage`.

- [ ] **Step 1: Write shared contract, extra-field and quota tests**

```python
@pytest.mark.parametrize("provider_factory", [groq_factory, openai_factory])
async def test_provider_normalizes_valid_extract(provider_factory, transport):
    transport.respond_json(valid_extract_payload())
    result = await provider_factory(transport).extract(extract_request())
    assert result.claim_candidates[0].exact_text
    assert result.provenance[0].source_document_id

async def test_quota_error_is_safe_and_never_calls_fallback(groq, openai_spy):
    groq.transport.respond(status=429, headers={"x-ratelimit-remaining-tokens": "0"})
    with pytest.raises(SafeError) as caught:
        await groq.extract(extract_request())
    assert caught.value.code is SafeErrorCode.LLM_QUOTA_EXHAUSTED
    openai_spy.assert_not_called()
```

- [ ] **Step 2: Run contract tests and confirm missing provider port**

Run: `python -m pytest tests/contract/test_llm_contract.py tests/unit/providers -v`

Expected: FAIL on imports.

- [ ] **Step 3: Implement strict schemas and retry boundary**

```python
class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

class LLMProvider(Protocol):
    async def extract(self, request: ExtractRequest) -> ExtractResponse: ...
    async def classify_claims(self, request: ClaimsRequest) -> ClaimsResponse: ...
    async def synthesize_evidence(self, request: EvidenceRequest) -> EvidenceResponse: ...
    async def propose_angles(self, request: AnglesRequest) -> AnglesResponse: ...
    async def generate_draft(self, request: DraftRequest) -> DraftResponse: ...
    async def assess_change(self, request: ChangeRequest) -> ChangeResponse: ...
    async def infer_style_candidates(self, request: StyleInferenceRequest) -> StyleInferenceResponse: ...
    async def healthcheck(self) -> ProviderHealth: ...
    async def estimate_or_report_usage(self, response_id: str) -> UsageReport: ...
```

Add `GroqProvider.list_models() -> tuple[AvailableModel, ...]` for onboarding. It filters the Models API response to active production models capable of the strict output contract; the initial eval candidates are `openai/gpt-oss-120b` and `openai/gpt-oss-20b` only when returned for the account. Set connect timeout 5s, total 60s, at most two jittered retries only for definite safe 429/5xx, and one schema-repair attempt. Uncertainty returns blocked data rather than a confident repair. Store model, prompt/schema versions, latency, tokens and error class; redact keys, URLs with credentials, prompts and full source content. OpenAI adapter is instantiable only when `LLM_PROVIDER=openai` and cost guard/eval activation exists.

- [ ] **Step 4: Run provider tests**

Run: `python -m pytest tests/contract/test_llm_contract.py tests/unit/providers -v`

Expected: PASS, including malformed JSON, extra fields, wrong enum, refusal, timeout and quota cases.

- [ ] **Step 5: Commit**

```powershell
git add src/bodrye_bot/ports/llm.py src/bodrye_bot/providers src/bodrye_bot/operations/usage.py tests/contract tests/unit/providers
git commit -m "feat: add provider-neutral LLM boundary"
```

### Task 7: Versioned model, style and safety evaluation gate

**Files:**
- Create: `evals/dataset.jsonl`, `evals/run.py`, `evals/report.py`
- Create: `src/bodrye_bot/operations/model_activation.py`
- Test: `tests/unit/evals/test_report.py`, `tests/e2e/test_model_activation.py`

**Interfaces:**
- Consumes: `LLMProvider` and usage records from Task 6.
- Produces: `EvalCase`, `EvalReport`, `run_eval(provider, dataset)`, `ActivationGate.decide(report)`, immutable activation audit.

- [ ] **Step 1: Write failing hard-gate tests**

```python
def test_one_safety_violation_blocks_activation():
    report = report_for(hard_cases=10, passed_hard_cases=9, valid_schemas=10)
    assert ActivationGate().decide(report).allowed is False

def test_provider_needs_model_style_and_safety_results():
    report = report_for(model_score=None, style_score=4.5, passed_hard_cases=10)
    assert ActivationGate().decide(report).reasons == ("model_eval_missing",)
```

- [ ] **Step 2: Run tests and confirm the eval runner is missing**

Run: `python -m pytest tests/unit/evals/test_report.py tests/e2e/test_model_activation.py -v`

Expected: FAIL on imports.

- [ ] **Step 3: Add the frozen dataset and deterministic report**

Each JSONL case contains `id`, `category`, `input`, `expected_schema`, `hard_assertions`, `blind_label`. Include 8–10 calibration topics, 3 holdouts, supported/refuted/insufficient/manual claims, numeric/causal/association traps, unavailable and prompt-injected sources, semantic number/modality/population/action edits and multiple Russian lengths. Report records provider/model/prompt/schema versions, blinded rating, violations, latency and tokens.

```python
@dataclass(frozen=True)
class ActivationDecision:
    allowed: bool
    reasons: tuple[str, ...]

class ActivationGate:
    def decide(self, report: EvalReport) -> ActivationDecision:
        reasons = report.missing_sections() + report.hard_failures()
        return ActivationDecision(not reasons, tuple(reasons))
```

- [ ] **Step 4: Run eval gate tests and a fake-provider report**

Run: `python -m pytest tests/unit/evals/test_report.py tests/e2e/test_model_activation.py -v`

Run: `python -m evals.run --provider fake --dataset evals/dataset.jsonl --output .artifacts/eval-fake.json`

Expected: tests PASS and report exits 0 with all fixture IDs present.

- [ ] **Step 5: Commit**

```powershell
git add evals src/bodrye_bot/operations/model_activation.py tests/unit/evals tests/e2e/test_model_activation.py
git commit -m "feat: gate runtime models with versioned evals"
```

### Task 8: Style calibration, bounded context and approval-based learning

**Files:**
- Create: `src/bodrye_bot/domain/style.py`, `src/bodrye_bot/style/calibration.py`
- Create: `src/bodrye_bot/style/context.py`, `src/bodrye_bot/style/learning.py`
- Test: `tests/unit/style/test_calibration.py`, `tests/unit/style/test_context.py`, `tests/e2e/test_style_learning.py`

**Interfaces:**
- Consumes: style ORM repositories, LLM style methods and eval holdouts.
- Produces: `CalibrationService`, `StyleContextBuilder.build(profile_id, rubric, format, risk)`, `StyleLearningService.propose_from_edit`, `confirm_rule`, `supersede_rule`.

- [ ] **Step 1: Write failing activation and memory-consent tests**

```python
def test_style_gate_requires_two_holdouts_and_median_four():
    result = gate_result(ratings=[5, 4, 2], accepted_without_rewrite=[True, True, False])
    assert StyleGate().evaluate(result).passed is True

async def test_edit_never_activates_rule_without_owner_confirmation(service):
    proposal = await service.propose_from_edit(owner_id=42, edit=sample_edit())
    assert proposal.status is RuleStatus.PROPOSED
    assert await service.active_rules(owner_id=42) == []
```

- [ ] **Step 2: Run tests and confirm missing style services**

Run: `python -m pytest tests/unit/style tests/e2e/test_style_learning.py -v`

Expected: FAIL on imports.

- [ ] **Step 3: Implement calibration and context boundaries**

Calibration requires 8–10 risk-diverse topics, three short variants each, explicit selections/edits, confirmed rules, then three unseen full posts. Activate only with zero hard-rule violations, at least 2/3 accepted without complete rewrite and median rating >=4.

```python
@dataclass(frozen=True)
class StyleContext:
    hard_rules: tuple[StyleRule, ...]
    format_rules: tuple[StyleRule, ...]
    positive_examples: tuple[StyleExample, ...]  # 3..5
    negative_examples: tuple[StyleExample, ...]
    selected_angle: AngleBrief
    medical_constraints: tuple[str, ...]
```

Select examples by owner, active profile, format, rubric and tags; never send chat history. Create a repeated-pattern proposal only after three similar confirmed edits. Explicit `Запомни как правило` also creates only `PROPOSED`; confirmation activates, conflict confirmation supersedes the old rule, rejection remains auditable.

- [ ] **Step 4: Run style tests**

Run: `python -m pytest tests/unit/style tests/e2e/test_style_learning.py -v`

Expected: PASS for style gate and AC-12.

- [ ] **Step 5: Commit**

```powershell
git add src/bodrye_bot/domain/style.py src/bodrye_bot/style tests/unit/style tests/e2e/test_style_learning.py
git commit -m "feat: build consent-based channel style memory"
```

### Task 9: Versioned source registry and SSRF-safe bounded fetching

**Files:**
- Create: `src/bodrye_bot/domain/sources.py`, `src/bodrye_bot/sources/catalog.py`
- Create: `src/bodrye_bot/sources/fetcher.py`, `src/bodrye_bot/sources/extraction.py`
- Test: `tests/unit/sources/test_catalog.py`, `tests/security/test_ssrf.py`, `tests/security/test_prompt_injection.py`

**Interfaces:**
- Consumes: source repositories, audit writer and LLM extraction contract.
- Produces: `SourceCatalog`, `SafeFetcher.fetch(url, source) -> FetchResult`, `ExtractionService.extract(document) -> ExtractedDocument`.

- [ ] **Step 1: Write failing network policy and provenance tests**

```python
@pytest.mark.parametrize("url", [
    "http://127.0.0.1/x", "http://169.254.169.254/latest/meta-data",
    "http://[::1]/x", "file:///etc/passwd",
])
async def test_private_or_non_http_target_is_blocked(fetcher, url):
    with pytest.raises(SafeError) as caught:
        await fetcher.fetch(url, evidence_source())
    assert caught.value.code is SafeErrorCode.SOURCE_BLOCKED

async def test_unavailable_source_never_reaches_llm(fetcher, extractor, llm_spy):
    fetcher.transport.respond(status=403)
    result = await extractor.from_url(owner_id=42, url=WHO_URL)
    assert result.status is FetchStatus.UNAVAILABLE
    llm_spy.assert_not_called()
```

- [ ] **Step 2: Run tests and confirm source boundary is missing**

Run: `python -m pytest tests/unit/sources tests/security/test_ssrf.py tests/security/test_prompt_injection.py -v`

Expected: FAIL on imports.

- [ ] **Step 3: Implement registry, safe redirect loop and bounded content**

Seed versioned records for Minzdrav manual search, WHO Fact Sheets, WHO News, USPSTF, NICE, Cochrane, three owner-approved PubMed RSS queries and manual Telegram sources. A Telegram item enters only through an owner-forwarded message or explicit link; no userbot, scraping or credential request exists. Roles are enums `EVIDENCE`, `TOPIC`, `FORMAT`, `ANTI_EXAMPLE`; Telegram sources cannot receive `EVIDENCE`.

`SafeFetcher` accepts only HTTP(S), resolves every hostname, blocks private/loopback/link-local/multicast/reserved/metadata IPs, pins the validated destination, caps encoded bytes at 10 MiB, uses connect 5s/total 20s, follows at most three redirects with full revalidation, strips credentials and sanitizes HTML. Store raw content with 24-hour expiry. Treat source text as quoted data and surround it with fixed non-executable delimiters before LLM use.

- [ ] **Step 4: Run security and source tests**

Run: `python -m pytest tests/unit/sources tests/security/test_ssrf.py tests/security/test_prompt_injection.py -v`

Expected: PASS, including DNS rebinding simulation, redirect-to-private, oversize response and AC-04.

- [ ] **Step 5: Commit**

```powershell
git add src/bodrye_bot/domain/sources.py src/bodrye_bot/sources tests/unit/sources tests/security
git commit -m "feat: ingest only allowlisted bounded sources"
```

### Task 10: Deduplicated weekday digest with quality threshold

**Files:**
- Create: `src/bodrye_bot/digest/service.py`, `src/bodrye_bot/digest/views.py`
- Create: `src/bodrye_bot/digest/worker.py`
- Test: `tests/unit/digest/test_deduplication.py`, `tests/unit/digest/test_ranking.py`, `tests/e2e/test_digest_delivery.py`

**Interfaces:**
- Consumes: fetched documents, source roles, Telegram view port, Moscow clock.
- Produces: `DigestService.build(date) -> Digest`, `DigestWorker.run_due(now)`, 0–5 cards plus source failures.

- [ ] **Step 1: Write failing dedupe, threshold and timing tests**

```python
def test_digest_does_not_fill_to_five_with_weak_items():
    digest = DigestService(min_score=0.70).build(items_with_scores([0.94, 0.81, 0.61, 0.20]))
    assert [item.score for item in digest.items] == [0.94, 0.81]

def test_same_topic_merges_provenance_links():
    digest = DigestService(min_score=0.70).build(two_documents_same_topic())
    assert len(digest.items) == 1
    assert len(digest.items[0].provenance_urls) == 2
```

- [ ] **Step 2: Run tests and confirm digest service is missing**

Run: `python -m pytest tests/unit/digest tests/e2e/test_digest_delivery.py -v`

Expected: FAIL on imports.

- [ ] **Step 3: Implement deterministic ranking and weekday delivery**

Normalize canonical URL, then content hash, then topic fingerprint. Score relevance, freshness, source authority, audience fit, novelty and preliminary risk using versioned weights; select only score >=0.70, maximum five, never pad. Each card includes topic, why 35–50 should care, source roles, risk label, selection reason and actions `Развить`, `Сохранить`, `Пропустить`.

Worker targets 10:00 MSK weekdays, reports partial digest and failures by 10:10, records delivery time, and makes duplicate runs idempotent by `(owner_id, digest_date)`.

- [ ] **Step 4: Run digest tests**

Run: `python -m pytest tests/unit/digest tests/e2e/test_digest_delivery.py -v`

Expected: PASS for AC-03 and weekday idempotency.

- [ ] **Step 5: Commit**

```powershell
git add src/bodrye_bot/digest tests/unit/digest tests/e2e/test_digest_delivery.py
git commit -m "feat: deliver quality-gated morning digest"
```

### Task 11: Claims, evidence and hard medical review gate

**Files:**
- Create: `src/bodrye_bot/domain/medical.py`, `src/bodrye_bot/medical/review.py`
- Create: `src/bodrye_bot/medical/policy.py`
- Test: `tests/unit/medical/test_policy.py`, `tests/e2e/test_claim_review.py`

**Interfaces:**
- Consumes: confirmed extraction, source documents and provider claim/evidence methods.
- Produces: `ClaimReviewService.review(workflow_id) -> ClaimReview`, `MedicalPolicy.can_draft(review)`, `MedicalPolicy.can_approve(review, draft_hash)`.

- [ ] **Step 1: Write failing evidence/risk gate tests**

```python
@pytest.mark.parametrize("verdict,risk,has_provenance", [
    (EvidenceVerdict.INSUFFICIENT, RiskLevel.RED, True),
    (EvidenceVerdict.SUPPORTED, RiskLevel.GREEN, False),
    (EvidenceVerdict.MANUAL_REVIEW, RiskLevel.YELLOW, True),
])
def test_unsafe_or_incomplete_claim_blocks_approval(verdict, risk, has_provenance):
    review = claim_review(verdict=verdict, risk=risk, has_provenance=has_provenance)
    assert MedicalPolicy().can_approve(review, review.draft_hash).allowed is False

async def test_claim_review_requires_confirmed_extraction(service):
    with pytest.raises(SafeError):
        await service.review(workflow(status=WorkflowStatus.EXTRACTED))
```

- [ ] **Step 2: Run tests and confirm medical policy is missing**

Run: `python -m pytest tests/unit/medical tests/e2e/test_claim_review.py -v`

Expected: FAIL on imports.

- [ ] **Step 3: Implement atomic claim review**

Define exact claim types `effect`, `causal`, `association`, `risk`, `numeric`, `diagnosis`, `treatment`, `dosage`, `prevention`, `safety`; verdicts `supported`, `refuted`, `insufficient`, `manual_review`; risks `green`, `yellow`, `red`. Evidence stores exact bounded excerpt/hash, applicability, limitations, source document and model run.

The service shows extraction preview first and persists confirmation before invoking review. It checks wording, population, context, causality, numeric value and modality. `red`, incomplete, manual, missing provenance, refuted, stale policy/model run or mismatched draft hash blocks downstream approval with explicit reasons.

- [ ] **Step 4: Run medical gate tests**

Run: `python -m pytest tests/unit/medical tests/e2e/test_claim_review.py -v`

Expected: PASS for AC-05 and AC-07.

- [ ] **Step 5: Commit**

```powershell
git add src/bodrye_bot/domain/medical.py src/bodrye_bot/medical tests/unit/medical tests/e2e/test_claim_review.py
git commit -m "feat: block unsafe medical claims"
```

### Task 12: Three angles, adaptive draft and semantic edit review

**Files:**
- Create: `src/bodrye_bot/editorial/service.py`, `src/bodrye_bot/editorial/validators.py`
- Create: `src/bodrye_bot/editorial/views.py`
- Test: `tests/unit/editorial/test_angles.py`, `tests/unit/editorial/test_draft.py`, `tests/e2e/test_semantic_edit.py`

**Interfaces:**
- Consumes: passed claim review, active StyleContext and provider generation/change methods.
- Produces: `EditorialService.propose_angles`, `select_angle`, `generate_draft`, `apply_edit`; validated `DraftVersion`.

- [ ] **Step 1: Write failing angle, style and edit-invalidation tests**

```python
async def test_three_angles_are_structurally_distinct(service):
    angles = await service.propose_angles(reviewed_workflow())
    assert [a.kind for a in angles] == [AngleKind.PRACTICAL, AngleKind.EXPLAINER, AngleKind.LIGHT]
    assert len({a.structure for a in angles}) == 3

async def test_semantic_edit_creates_version_and_revokes_review_approval(service):
    changed = await service.apply_edit(approved_workflow(), "Риск снижается на 30%")
    assert changed.version_number == 2
    assert changed.status is WorkflowStatus.DRAFT_REVIEW_PENDING
    assert changed.active_approval is None
```

- [ ] **Step 2: Run tests and confirm editorial service is missing**

Run: `python -m pytest tests/unit/editorial tests/e2e/test_semantic_edit.py -v`

Expected: FAIL on imports.

- [ ] **Step 3: Implement the guarded editorial sequence**

Before draft, render claims, evidence, limitations and exactly three materially different angles: practical, explanatory/myth-busting, light useful-entertainment. Draft request includes only selected angle, bounded StyleContext and approved evidence.

Validate total length <=3,800 Unicode code points including links, selected short/medium/long range, no unreviewed claim, 1–3 public evidence links where material, valid Telegram HTML, at most 1–2 humor elements and fewer for medical risk. Reject invented first-person experience and editorial «мы» unless the input records the actual editorial action. Return post, three headlines, optional CTA, visual suggestion and sentence-to-claim map.

`assess_change` compares exact numbers, modality, causality, population, action and limitations. On uncertain or semantic change, create a new version, set `DRAFT_REVIEW_PENDING`, revoke review/approval and rerun medical review.

- [ ] **Step 4: Run editorial tests**

Run: `python -m pytest tests/unit/editorial tests/e2e/test_semantic_edit.py -v`

Expected: PASS for AC-06, AC-08 and AC-19.

- [ ] **Step 5: Commit**

```powershell
git add src/bodrye_bot/editorial tests/unit/editorial tests/e2e/test_semantic_edit.py
git commit -m "feat: generate reviewed style-aware drafts"
```

### Task 13: Approval, Moscow scheduling and conservative Telegram delivery

**Files:**
- Create: `src/bodrye_bot/domain/publication.py`, `src/bodrye_bot/ports/telegram.py`
- Create: `src/bodrye_bot/providers/telegram.py`, `src/bodrye_bot/publication/service.py`
- Create: `src/bodrye_bot/publication/worker.py`, `src/bodrye_bot/main_worker.py`
- Test: `tests/unit/publication/test_policy.py`, `tests/integration/test_publication_lease.py`, `tests/e2e/test_delivery_states.py`

**Interfaces:**
- Consumes: current reviewed draft, workflow/UoW, Bot API port and clock.
- Produces: `approve`, `schedule`, `claim_due_jobs`, `PublicationWorker.process`, `mark_published`, `confirm_retry`.

- [ ] **Step 1: Write failing approval and ambiguous-delivery tests**

```python
async def test_approve_only_records_hash(service, telegram_spy):
    approval = await service.approve(reviewed_draft())
    assert approval.content_hash == reviewed_draft().body_hash
    telegram_spy.assert_not_called()

async def test_timeout_becomes_delivery_unknown_without_retry(worker, telegram):
    telegram.raise_after_possible_send(TimeoutError())
    await worker.process(due_job())
    assert await job_status() is PublicationStatus.DELIVERY_UNKNOWN
    assert telegram.call_count == 1
    await worker.run_due()
    assert telegram.call_count == 1
```

- [ ] **Step 2: Run publication tests and confirm missing services**

Run: `python -m pytest tests/unit/publication tests/integration/test_publication_lease.py tests/e2e/test_delivery_states.py -v`

Expected: FAIL on imports.

- [ ] **Step 3: Implement hash-bound approval, lease and delivery classification**

Approval inserts one active record for current version/hash and sends nothing. Schedule shows final preview, parses strict MSK date/time, rejects past/ambiguous time and stores UTC. Worker claims with `FOR UPDATE SKIP LOCKED`, assigns lease plus attempt ID, then rechecks current approval/hash immediately before send.

Classify pre-send validation/network failures proven not delivered as `FAILED`; successful response stores unique `telegram_message_id`; any timeout, cancellation or process crash after request dispatch becomes `DELIVERY_UNKNOWN`. No automatic transition leaves `DELIVERY_UNKNOWN`; only owner actions `MARK_PUBLISHED` or warned `RETRY` create audited transitions. Scheduled jobs never depend on LLM availability.

- [ ] **Step 4: Run publication tests**

Run: `python -m pytest tests/unit/publication tests/integration/test_publication_lease.py tests/e2e/test_delivery_states.py -v`

Expected: PASS for AC-09, AC-10, AC-11 and the 60-second lease target under test clock.

- [ ] **Step 5: Commit**

```powershell
git add src/bodrye_bot/domain/publication.py src/bodrye_bot/ports/telegram.py src/bodrye_bot/providers/telegram.py src/bodrye_bot/publication src/bodrye_bot/main_worker.py tests
git commit -m "feat: publish only exact approved versions"
```

### Task 14: Library, retention, deletion cascade and tombstone replay

**Files:**
- Create: `src/bodrye_bot/memory/service.py`, `src/bodrye_bot/memory/retention.py`
- Create: `src/bodrye_bot/memory/tombstones.py`
- Test: `tests/unit/memory/test_retention.py`, `tests/integration/test_deletion.py`, `tests/integration/test_tombstone_replay.py`

**Interfaces:**
- Consumes: owner-scoped repositories and all derivative table relationships.
- Produces: `LibraryService.save`, `DeletionService.delete`, `RetentionWorker.purge_due`, `TombstoneReplayer.apply_all`.

- [ ] **Step 1: Write failing retention and cascade tests**

```python
async def test_delete_removes_live_derivatives_and_keeps_tombstone(service, seeded_graph):
    await service.delete(owner_id=42, root_id=seeded_graph.workflow_id)
    assert await live_derivative_count(seeded_graph.workflow_id) == 0
    tombstone = await tombstone_for(seeded_graph.workflow_id)
    assert tombstone.owner_id == 42
    assert tombstone.applied_at is not None

def test_retention_windows_are_exact():
    assert RETENTION.raw_source == timedelta(hours=24)
    assert RETENTION.rejected_draft == timedelta(days=90)
    assert RETENTION.operational_log == timedelta(days=30)
    assert RETENTION.backup == timedelta(days=30)
```

- [ ] **Step 2: Run tests and confirm memory services are missing**

Run: `python -m pytest tests/unit/memory tests/integration/test_deletion.py tests/integration/test_tombstone_replay.py -v`

Expected: FAIL on imports.

- [ ] **Step 3: Implement explicit library and transactional deletion**

Library is a separate entity, never a publication state. Deletion creates a tombstone first, then removes summaries, caches, indexes, draft/style links and evidence excerpts not referenced by another live claim in one transaction. Derived deletion must finish within 24 hours. Restore startup runs tombstones before bot/worker readiness; tombstones are idempotent and owner-scoped. Retain tombstones for at least 31 days so every backup in the 30-day window receives the deletion on restore.

- [ ] **Step 4: Run memory tests**

Run: `python -m pytest tests/unit/memory tests/integration/test_deletion.py tests/integration/test_tombstone_replay.py -v`

Expected: PASS for AC-15 and every retention class.

- [ ] **Step 5: Commit**

```powershell
git add src/bodrye_bot/memory tests/unit/memory tests/integration/test_deletion.py tests/integration/test_tombstone_replay.py
git commit -m "feat: make editorial memory deletable"
```

### Task 15: Cost guard, quota isolation, metrics, alerts and health gate

**Files:**
- Create: `src/bodrye_bot/operations/costs.py`, `src/bodrye_bot/operations/quota.py`
- Create: `src/bodrye_bot/operations/metrics.py`, `src/bodrye_bot/operations/alerts.py`
- Create: `src/bodrye_bot/operations/health.py`, `src/bodrye_bot/healthcheck.py`
- Test: `tests/unit/operations/test_cost_guard.py`, `tests/e2e/test_quota_isolation.py`, `tests/e2e/test_health_gate.py`

**Interfaces:**
- Consumes: provider runs, cost events, source/queue/backup state and Telegram alert port.
- Produces: `CostGuard.authorize(operation)`, `QuotaCircuit`, `HealthService.snapshot()`, deduplicated owner alerts.

- [ ] **Step 1: Write failing budget and scheduled-job isolation tests**

```python
def test_unknown_cost_is_not_zero_and_paid_operation_is_blocked():
    decision = CostGuard(soft=Decimal("3500"), hard=Decimal("5000"), fixed=Decimal("1178")).authorize(
        operation(cost=None, paid=True)
    )
    assert decision.allowed is False
    assert decision.reason == "unknown_cost"

async def test_groq_quota_stops_generation_but_not_due_publication(app):
    app.quota.mark_exhausted("groq")
    assert (await app.generate()).error.code is SafeErrorCode.LLM_QUOTA_EXHAUSTED
    assert (await app.publication_worker.run_due()).published == 1
```

- [ ] **Step 2: Run tests and confirm operations services are missing**

Run: `python -m pytest tests/unit/operations tests/e2e/test_quota_isolation.py tests/e2e/test_health_gate.py -v`

Expected: FAIL on imports.

- [ ] **Step 3: Implement guards, snapshots and deduplicated alerts**

Aggregate fixed VPS/storage plus actual or conservative estimated provider costs per operation/workflow/month. Alert at 80% hard limit; block new paid AI/search at hard limit but keep server and free/scheduled operations. Quota circuit blocks new Groq LLM calls only and never instantiates OpenAI fallback.

Health snapshot contains bot heartbeat, worker heartbeat, DB, queue age, provider status, source quarantine, backup age, last restore, CPU/RAM/disk and release version. Alert after three source failures, any `DELIVERY_UNKNOWN`, stale queue, backup age >26h, disk >80%, quota exhaustion and failed release gate. Deduplicate alerts by code/object/window.

- [ ] **Step 4: Run operations tests**

Run: `python -m pytest tests/unit/operations tests/e2e/test_quota_isolation.py tests/e2e/test_health_gate.py -v`

Expected: PASS for AC-14 and AC-18.

- [ ] **Step 5: Commit**

```powershell
git add src/bodrye_bot/operations src/bodrye_bot/healthcheck.py tests/unit/operations tests/e2e
git commit -m "feat: enforce budget and operational health gates"
```

### Task 16: Encrypted backup, restore proof and hardened Beget deployment

**Files:**
- Create: `Dockerfile`, `deploy/compose.prod.yaml`, `deploy/backup/Dockerfile`
- Create: `deploy/backup/backup.sh`, `deploy/backup/restore_test.sh`
- Create: `deploy/beget/harden.sh`, `deploy/beget/OPERATIONS.md`
- Test: `tests/integration/test_backup_restore.py`, `tests/security/test_compose_hardening.py`

**Interfaces:**
- Consumes: migrations, tombstone replayer and healthcheck.
- Produces: immutable app image, daily age-encrypted S3 backup, monthly `_restore_test` proof, hardened Beget runbook.

- [ ] **Step 1: Write failing restore and compose-policy tests**

```python
async def test_backup_round_trip_records_proof(backup_runner, restore_runner, database):
    marker = await database.insert_restore_marker()
    artifact = await backup_runner.run()
    assert artifact.name.endswith(".dump.zst.age")
    result = await restore_runner.run(artifact, database_name_suffix="_restore_test")
    assert result.found_marker == marker
    assert result.tombstones_replayed is True

def test_production_compose_exposes_no_database_or_app_port(compose_doc):
    assert "ports" not in compose_doc["services"]["postgres"]
    assert "ports" not in compose_doc["services"]["bot"]
    assert compose_doc["services"]["bot"]["read_only"] is True
    assert compose_doc["services"]["worker"]["privileged"] is False
```

- [ ] **Step 2: Run tests and confirm deployment artifacts are missing**

Run: `python -m pytest tests/integration/test_backup_restore.py tests/security/test_compose_hardening.py -v`

Expected: FAIL because backup/deployment files do not exist.

- [ ] **Step 3: Implement encrypted backup and production profile**

Backup container runs daily `pg_dump --format=custom`, compresses with zstd, encrypts to an age public recipient, uploads to Beget S3-compatible storage, verifies object size/checksum and deletes objects older than 30 days only after successful listing. It records `BackupRun` without credentials. A monthly job restores the newest valid object: it downloads/decrypts into a separately named `_restore_test` DB, migrates, replays tombstones, checks row marker/invariants and drops only the exact validated test database.

Production compose uses bot, worker, postgres, backup and healthcheck; no public app/DB ports, non-root users, no privileged mode, read-only filesystems where compatible, tmpfs, resource limits, log rotation and restart policies. Runbook specifies Beget 2 CPU/4 GB/40 GB NVMe + IPv4, Ubuntu 24.04, deploy user, key-only SSH, disabled root/password login, UFW SSH allowlist, security updates, smoke-test Telegram/Groq/source egress, backup-before-migration, immutable image rollback and RPO 24h/RTO 4h proof.

- [ ] **Step 4: Run backup and deployment tests**

Run: `python -m pytest tests/integration/test_backup_restore.py tests/security/test_compose_hardening.py -v`

Expected: PASS with a restore journal row and no exposed service ports, satisfying AC-16.

- [ ] **Step 5: Commit**

```powershell
git add Dockerfile deploy tests/integration/test_backup_restore.py tests/security/test_compose_hardening.py
git commit -m "ops: add encrypted backup and Beget deployment"
```

### Task 17: Full acceptance suite, pilot readiness and operator documentation

**Files:**
- Create: `tests/e2e/test_acceptance.py`, `tests/e2e/test_restart_recovery.py`
- Create: `docs/pilot/acceptance-matrix.md`, `docs/pilot/two-week-runbook.md`
- Modify: `README.md`

**Interfaces:**
- Consumes: complete bot/worker application and all 20 acceptance criteria.
- Produces: executable release gate, evidence matrix and reversible two-week pilot procedure.

- [ ] **Step 1: Write a failing acceptance test spanning the real workflow**

```python
async def test_owner_can_complete_safe_post_workflow(app):
    denied = await app.command(user_id=999, text="/status")
    assert denied.code == "owner_forbidden"
    workflow = await app.ingest(owner_id=42, source=available_source())
    await app.confirm_extraction(owner_id=42, workflow_id=workflow.id)
    review = await app.review_claims(owner_id=42, workflow_id=workflow.id)
    assert review.passed
    angles = await app.propose_angles(owner_id=42, workflow_id=workflow.id)
    draft = await app.generate(owner_id=42, workflow_id=workflow.id, angle_id=angles[0].id)
    await app.review_draft(owner_id=42, draft_id=draft.id)
    approval = await app.approve(owner_id=42, draft_id=draft.id)
    assert await app.telegram_messages() == []
    await app.schedule(owner_id=42, approval_id=approval.id, when_msk=future_msk())
    await app.worker.run_due()
    assert (await app.job_for(draft.id)).status is PublicationStatus.PUBLISHED
```

- [ ] **Step 2: Run the full gate and capture genuine failures**

Run: `python -m ruff check .`

Run: `python -m mypy src evals`

Run: `python -m pytest -v`

Expected: acceptance test initially FAILS until bootstrap wiring and any uncovered integration gaps are connected; lint/type failures list exact corrections.

- [ ] **Step 3: Wire only missing composition and document every acceptance proof**

Connect concrete adapters in `bootstrap.py` without adding business rules. In `docs/pilot/acceptance-matrix.md`, create one row per AC-01..AC-20 with test node ID, observed result and evidence artifact. In `two-week-runbook.md`, specify weekday digest observation, five-post target without filler, style ratings, active-time log, source failures, claim blocks, costs, duplicate/delivery incidents, daily health check, rollback and final go/no-go thresholds from spec sections 4 and 22.

- [ ] **Step 4: Run the complete release gate twice, including restart recovery**

Run: `python -m ruff check .`

Run: `python -m mypy src evals`

Run: `python -m pytest -v`

Run: `docker compose build --pull`

Run: `docker compose up -d postgres bot worker`

Run: `python -m pytest tests/e2e/test_restart_recovery.py tests/e2e/test_acceptance.py -v`

Expected: all commands exit 0; accepted data survives bot and worker restarts; acceptance matrix has 20 PASS rows. Live Telegram/Groq/Beget smoke tests remain a production gate and use owner-supplied secrets only through environment variables.

- [ ] **Step 5: Commit**

```powershell
git add src/bodrye_bot/bootstrap.py tests/e2e docs/pilot README.md
git commit -m "test: prove Bodrye Lyudi MVP acceptance"
```

## Phase gates

Do not begin the next phase while the preceding gate is red:

1. Tasks 1–4: strict config, state machine, migration round trip, ownership and audit all pass.
2. Tasks 5–7: unauthorized access is zero; both provider contracts and all hard eval fixtures pass.
3. Task 8: Keti completes calibration; style gate is 2/3 holdouts and median >=4/5.
4. Tasks 9–10: SSRF/prompt-injection suite passes; source registry is approved; digest does not pad weak cards.
5. Tasks 11–12: all red/incomplete/missing-provenance fixtures block; semantic edits invalidate stale approval.
6. Task 13: no send without matching approval; no automatic retry from `DELIVERY_UNKNOWN`.
7. Tasks 14–16: deletion/tombstones, budget isolation, encrypted restore and hardened compose pass.
8. Task 17: Ruff, strict mypy, complete tests, restart recovery and all 20 acceptance rows pass.

## Acceptance coverage

| Criteria | Primary task | Proof |
|---|---:|---|
| AC-01 | 4, 5 | repository owner query + guard-first e2e |
| AC-02 | 5 | onboarding readiness gates |
| AC-03 | 10 | threshold/ranking test |
| AC-04 | 9 | unavailable-source LLM spy |
| AC-05 | 11 | confirmed-extraction precondition |
| AC-06 | 11, 12 | review view + three structural angles |
| AC-07 | 11 | parameterized hard-block test |
| AC-08 | 12 | semantic edit version/revocation e2e |
| AC-09 | 13 | approval telegram spy |
| AC-10 | 2, 13 | current hash/version schedule policy |
| AC-11 | 13 | ambiguous delivery no-retry test |
| AC-12 | 8 | proposed-only rule test |
| AC-13 | 7 | activation gate report |
| AC-14 | 6, 15 | quota isolation e2e |
| AC-15 | 14 | deletion graph + tombstone replay |
| AC-16 | 16 | encrypted backup restore journal |
| AC-17 | 2, 5 | safe-error renderer contract |
| AC-18 | 6, 15 | provider run/cost event assertions |
| AC-19 | 12 | invented-experience validator fixture |
| AC-20 | 1 | banned-scope architecture test |
