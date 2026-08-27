# «Бодрые люди»: безопасное редакционное ядро — план реализации

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Создать приватного Telegram-бота, который принимает текст, ссылку или голос, показывает извлечение и медицинскую проверку, создаёт контент-пакет, ведёт версии правок, требует явного утверждения и публикует утверждённую версию только в выбранное время.

**Architecture:** Python-приложение разделяется на доменное ядро, адаптеры Telegram/LLM/транскрипции, PostgreSQL-репозитории и фоновые задачи. Доменное ядро не импортирует aiogram, SQLAlchemy или SDK конкретного ИИ-провайдера; внешние сервисы подключаются через типизированные протоколы. Все переходы редакционного процесса сохраняются в PostgreSQL и проверяются серверными правилами, поэтому интерфейсная кнопка не может обойти блокировку.

**Tech Stack:** Python 3.12+, aiogram 3.x, SQLAlchemy 2.x async, Alembic, PostgreSQL 16+, asyncpg, Pydantic 2 + pydantic-settings, pytest + pytest-asyncio, Ruff, mypy, structlog.

**Spec:** `docs/superpowers/specs/2026-08-27-bodrye-lyudi-agent-design.md`

## Global Constraints

- Интерфейс и пользовательские ошибки — на русском языке.
- Доступ в MVP разрешён только allowlist Telegram ID Кети.
- Генерация никогда не считается утверждением.
- Кнопка «Утвердить» не публикует материал и не назначает время.
- Красный риск или незавершённая медицинская проверка блокируют утверждение.
- Смысловая правка медицинского утверждения инвалидирует проверку.
- Публикуется только неизменённая утверждённая версия в выбранные дату и время.
- Чужой материал хранится преимущественно как ссылка, метаданные и краткое извлечение.
- Целевой эксплуатационный бюджет — 2 000–5 000 рублей в месяц.
- Все даты хранятся в UTC; пользовательское расписание отображается в `Europe/Moscow`.
- Реальная интеграция репозиториев тестируется только на отдельной PostgreSQL-базе, имя которой содержит `_test`.

## Карта файлов

```text
pyproject.toml                         зависимости и настройки инструментов
.env.example                           контракт конфигурации без секретов
src/bodrye_bot/config.py               типизированные настройки приложения
src/bodrye_bot/domain/models.py        доменные сущности и перечисления
src/bodrye_bot/domain/errors.py        безопасные коды ошибок
src/bodrye_bot/domain/ports.py         протоколы внешних сервисов и репозиториев
src/bodrye_bot/domain/policies.py      правила проверки, утверждения и публикации
src/bodrye_bot/application/ingest.py   обработка текста, ссылки и голоса
src/bodrye_bot/application/review.py   атомарные утверждения и evidence review
src/bodrye_bot/application/content.py  три подачи и контент-пакет
src/bodrye_bot/application/editorial.py версии, правки и утверждение
src/bodrye_bot/application/publish.py  расписание и публикация
src/bodrye_bot/infrastructure/db.py    async engine и session factory
src/bodrye_bot/infrastructure/tables.py SQLAlchemy-таблицы
src/bodrye_bot/infrastructure/repositories.py PostgreSQL-репозитории
src/bodrye_bot/infrastructure/llm.py   адаптер структурированных LLM-вызовов
src/bodrye_bot/infrastructure/transcription.py адаптер расшифровки
src/bodrye_bot/infrastructure/source_fetch.py разрешённое получение страницы по ссылке
src/bodrye_bot/infrastructure/telegram.py Telegram publisher
src/bodrye_bot/bot/middleware.py       allowlist и correlation ID
src/bodrye_bot/bot/keyboards.py        callback-кнопки
src/bodrye_bot/bot/handlers.py         Telegram-сценарии без бизнес-правил
src/bodrye_bot/worker.py               фоновая публикация due-заданий
src/bodrye_bot/main.py                 композиция зависимостей и запуск
alembic.ini                            конфигурация миграций
alembic/env.py                         async Alembic environment
alembic/versions/0001_editorial_core.py первая схема
tests/unit/                             быстрые доменные и прикладные тесты
tests/integration/                      PostgreSQL и миграционные тесты
tests/contract/                         контракты LLM и Telegram-адаптеров
```

---

### Task 1: Каркас проекта, конфигурация и доменные типы

**Files:**
- Create: `pyproject.toml`
- Create: `.env.example`
- Create: `src/bodrye_bot/__init__.py`
- Create: `src/bodrye_bot/config.py`
- Create: `src/bodrye_bot/domain/models.py`
- Create: `src/bodrye_bot/domain/errors.py`
- Test: `tests/unit/test_config.py`
- Test: `tests/unit/test_domain_models.py`

**Interfaces:**
- Produces: `Settings`, `ContentStatus`, `EvidenceVerdict`, `RiskLevel`, `InputKind`, `InputItem`, `FetchedSource`, `ExtractionPreview`, `AtomicClaim`, `Citation`, `ClaimReview`, `MedicalReview`, `Angle`, `StoryScript`, `SourceNote`, `ContentPackage`, `DraftVersion`, `ChangeAssessment`, `ApprovalDecision`, `ScheduledPublication`, `SafeError`.
- Consumes: environment variables documented in `.env.example`.

- [ ] **Step 1: Инициализировать Git и создать Python-проект**

Run:

```powershell
git init
python -m venv .venv
```

Expected: `git status --short` работает; каталог `.venv` существует.

- [ ] **Step 2: Добавить зависимости и настройки инструментов**

Create `pyproject.toml` with:

```toml
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "bodrye-lyudi-bot"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = [
  "aiogram>=3.20,<4",
  "alembic>=1.16,<2",
  "asyncpg>=0.30,<1",
  "httpx>=0.28,<1",
  "pydantic>=2.11,<3",
  "pydantic-settings>=2.10,<3",
  "sqlalchemy[asyncio]>=2.0.41,<3",
  "structlog>=25.4,<26",
]

[project.optional-dependencies]
dev = [
  "mypy>=1.17,<2",
  "pytest>=8.4,<9",
  "pytest-asyncio>=1.1,<2",
  "ruff>=0.12,<1",
]

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]

[tool.ruff]
line-length = 100
target-version = "py312"

[tool.ruff.lint]
select = ["E", "F", "I", "B", "UP", "RUF"]

[tool.mypy]
python_version = "3.12"
strict = true
packages = ["bodrye_bot"]
```

Run:

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
```

Expected: installation exits with code 0.

- [ ] **Step 3: Написать падающие тесты конфигурации**

Create `tests/unit/test_config.py`:

```python
import pytest
from pydantic import ValidationError

from bodrye_bot.config import Settings


def test_settings_require_owner_and_tokens() -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_settings_accept_moscow_timezone() -> None:
    settings = Settings(
        telegram_bot_token="test-token",
        telegram_owner_id=123,
        telegram_channel_id=-1001234567890,
        database_url="postgresql+asyncpg://u:p@localhost/app",
        test_database_url="postgresql+asyncpg://u:p@localhost/app_test",
        openai_api_key="test-openai-key",
        openai_model="configured-generation-model",
        openai_transcription_model="configured-transcription-model",
        timezone="Europe/Moscow",
        _env_file=None,
    )
    assert settings.telegram_owner_id == 123
    assert settings.timezone == "Europe/Moscow"
```

- [ ] **Step 4: Написать падающие тесты доменных переходов**

Create `tests/unit/test_domain_models.py`:

```python
from uuid import uuid4

from bodrye_bot.domain.models import ContentStatus, DraftVersion, RiskLevel


def test_new_draft_is_not_approved() -> None:
    draft = DraftVersion.new(content_id=uuid4(), body="Текст")
    assert draft.status is ContentStatus.DRAFT
    assert draft.approved_at is None


def test_red_risk_is_blocking() -> None:
    assert RiskLevel.RED.is_blocking is True
    assert RiskLevel.GREEN.is_blocking is False
```

- [ ] **Step 5: Запустить тесты и подтвердить ожидаемое падение**

Run:

```powershell
.\.venv\Scripts\pytest.exe tests/unit/test_config.py tests/unit/test_domain_models.py -v
```

Expected: collection fails because `bodrye_bot.config` and domain types do not exist.

- [ ] **Step 6: Реализовать минимальные настройки и доменные типы**

Create `.env.example`:

```dotenv
TELEGRAM_BOT_TOKEN=
TELEGRAM_OWNER_ID=
TELEGRAM_CHANNEL_ID=
DATABASE_URL=postgresql+asyncpg://user:password@host:5432/bodrye
TEST_DATABASE_URL=postgresql+asyncpg://user:password@host:5432/bodrye_test
OPENAI_API_KEY=
OPENAI_MODEL=
OPENAI_TRANSCRIPTION_MODEL=
TIMEZONE=Europe/Moscow
MONTHLY_BUDGET_RUB=5000
```

Implement `Settings` using `pydantic_settings.BaseSettings`, `env_prefix=""`, and validators requiring `_test` in the test database name. Implement every type listed in the Interfaces block as a string enum or frozen dataclass in `domain/models.py`; identifiers use `UUID`, collections use immutable tuples, and timestamps are timezone-aware UTC values. `DraftVersion.new(...)` must set status `DRAFT`, version `1`, and UTC timestamps. `RiskLevel.is_blocking` returns true only for `RED`.

- [ ] **Step 7: Запустить проверки**

Run:

```powershell
.\.venv\Scripts\pytest.exe tests/unit/test_config.py tests/unit/test_domain_models.py -v
.\.venv\Scripts\ruff.exe check src tests
.\.venv\Scripts\mypy.exe src
```

Expected: all commands pass.

- [ ] **Step 8: Зафиксировать каркас**

```powershell
git add pyproject.toml .env.example src tests
git commit -m "chore: bootstrap editorial core"
```

---

### Task 2: PostgreSQL-схема, миграции и репозитории

**Files:**
- Create: `src/bodrye_bot/domain/ports.py`
- Create: `src/bodrye_bot/infrastructure/db.py`
- Create: `src/bodrye_bot/infrastructure/tables.py`
- Create: `src/bodrye_bot/infrastructure/repositories.py`
- Create: `alembic.ini`
- Create: `alembic/env.py`
- Create: `alembic/versions/0001_editorial_core.py`
- Test: `tests/integration/test_migrations.py`
- Test: `tests/integration/test_repositories.py`

**Interfaces:**
- Produces: `ContentRepository`, `ReviewRepository`, `ScheduleRepository`, `AuditRepository` protocols and PostgreSQL implementations.
- Produces: `create_session_factory(database_url: str) -> async_sessionmaker[AsyncSession]`.
- Consumes: domain UUIDs, statuses and UTC timestamps from Task 1.

- [ ] **Step 1: Определить репозиторные протоколы**

Add async protocols to `domain/ports.py` with exact methods:

```python
class ContentRepository(Protocol):
    async def create_input(self, item: InputItem) -> None: ...
    async def add_version(self, version: DraftVersion) -> None: ...
    async def get_version(self, version_id: UUID) -> DraftVersion | None: ...
    async def list_versions(self, content_id: UUID) -> list[DraftVersion]: ...


class ReviewRepository(Protocol):
    async def replace_for_version(self, version_id: UUID, review: MedicalReview) -> None: ...
    async def get_for_version(self, version_id: UUID) -> MedicalReview | None: ...


class ScheduleRepository(Protocol):
    async def enqueue(self, item: ScheduledPublication) -> None: ...
    async def claim_due(self, now_utc: datetime, limit: int) -> list[ScheduledPublication]: ...
    async def mark_published(self, publication_id: UUID, message_id: int) -> None: ...
    async def mark_failed(self, publication_id: UUID, safe_code: str) -> None: ...
```

- [ ] **Step 2: Написать миграционный тест пустой схемы**

Create `tests/integration/test_migrations.py` that reads `TEST_DATABASE_URL`, refuses a database name without `_test`, runs `alembic upgrade head`, checks tables `inputs`, `draft_versions`, `claims`, `evidence_reviews`, `scheduled_publications`, `audit_events`, and runs `upgrade head` a second time.

- [ ] **Step 3: Написать репозиторные тесты**

Cover these exact cases in `tests/integration/test_repositories.py`:

```python
async def test_versions_are_append_only(content_repo: ContentRepository) -> None: ...
async def test_review_is_scoped_to_exact_version(review_repo: ReviewRepository) -> None: ...
async def test_claim_due_does_not_return_unapproved(schedule_repo: ScheduleRepository) -> None: ...
async def test_claim_due_is_idempotent_across_workers(schedule_repo: ScheduleRepository) -> None: ...
async def test_audit_event_cannot_be_updated(audit_repo: AuditRepository) -> None: ...
```

- [ ] **Step 4: Запустить интеграционные тесты и подтвердить падение**

Run:

```powershell
if (-not $env:TEST_DATABASE_URL) { throw 'Set TEST_DATABASE_URL to a dedicated PostgreSQL database ending in _test' }
.\.venv\Scripts\python.exe -c "from bodrye_bot.config import Settings; Settings(); print('test database validated')"
.\.venv\Scripts\pytest.exe tests/integration/test_migrations.py tests/integration/test_repositories.py -v
```

Expected: FAIL because migrations and repository implementations do not exist. Never point this command at a production or shared database.

- [ ] **Step 5: Реализовать таблицы и первую миграцию**

Use UUID primary keys, `TIMESTAMP WITH TIME ZONE`, foreign keys tying claims and reviews to an exact `draft_version_id`, unique `(content_id, version_number)`, and a partial unique index preventing more than one active schedule for a version. `audit_events` has no update/delete repository methods.

- [ ] **Step 6: Реализовать транзакционные репозитории**

`claim_due` must use `SELECT ... FOR UPDATE SKIP LOCKED`, return only rows whose version is `APPROVED`, and atomically change them to `PROCESSING`. Duplicate enqueue with the same version and time returns the existing record rather than creating a second publication.

- [ ] **Step 7: Проверить пустую и повторную миграцию**

Run:

```powershell
.\.venv\Scripts\alembic.exe downgrade base
.\.venv\Scripts\alembic.exe upgrade head
.\.venv\Scripts\alembic.exe upgrade head
.\.venv\Scripts\pytest.exe tests/integration/test_migrations.py tests/integration/test_repositories.py -v
```

Expected: migrations and repository tests pass twice without schema drift.

- [ ] **Step 8: Зафиксировать слой данных**

```powershell
git add src/bodrye_bot/domain/ports.py src/bodrye_bot/infrastructure alembic.ini alembic tests/integration
git commit -m "feat: add editorial persistence"
```

---

### Task 3: Приватный Telegram-интерфейс и безопасные ошибки

**Files:**
- Create: `src/bodrye_bot/bot/middleware.py`
- Create: `src/bodrye_bot/bot/keyboards.py`
- Create: `src/bodrye_bot/bot/handlers.py`
- Create: `src/bodrye_bot/bot/copy.py`
- Test: `tests/unit/test_access_middleware.py`
- Test: `tests/unit/test_safe_error_copy.py`

**Interfaces:**
- Produces: `OwnerOnlyMiddleware(owner_id: int)`, `safe_error_message(code: str) -> str`, callback payloads `develop:<uuid>`, `angle:<uuid>:<name>`, `approve:<uuid>`, `schedule:<uuid>`.
- Consumes: `Settings.telegram_owner_id`, domain UUIDs and `SafeError` codes.

- [ ] **Step 1: Написать тесты allowlist**

Test that update from the owner reaches the handler and update from any other numeric ID returns the Russian refusal without invoking the handler.

- [ ] **Step 2: Написать тесты пользовательских ошибок**

Parametrize exact codes and required Russian copy fragments:

```python
ERROR_COPY = {
    "llm_timeout": "Не дождалась ответа",
    "llm_rate_limit": "Сервис временно ограничил запросы",
    "llm_unavailable": "Сервис сейчас недоступен",
    "llm_invalid_output": "Не удалось разобрать ответ",
    "llm_safety_refusal": "Не могу безопасно обработать материал",
    "transcription_failed": "Не удалось расшифровать голосовое",
    "source_unavailable": "Источник недоступен",
    "medical_review_incomplete": "Проверка медицинских утверждений не завершена",
    "internal_error": "Произошла внутренняя ошибка",
}
```

- [ ] **Step 3: Запустить тесты и подтвердить падение**

Run: `.\.venv\Scripts\pytest.exe tests/unit/test_access_middleware.py tests/unit/test_safe_error_copy.py -v`

Expected: FAIL because middleware and copy modules do not exist.

- [ ] **Step 4: Реализовать middleware, callback data и тексты**

Handlers must only translate Telegram updates into application calls. They must not set `APPROVED`, enqueue publications, or call the LLM directly.

- [ ] **Step 5: Проверить интерфейсный слой**

Run:

```powershell
.\.venv\Scripts\pytest.exe tests/unit/test_access_middleware.py tests/unit/test_safe_error_copy.py -v
.\.venv\Scripts\ruff.exe check src tests
.\.venv\Scripts\mypy.exe src
```

Expected: all pass.

- [ ] **Step 6: Зафиксировать приватный интерфейс**

```powershell
git add src/bodrye_bot/bot tests/unit/test_access_middleware.py tests/unit/test_safe_error_copy.py
git commit -m "feat: add private telegram interface"
```

---

### Task 4: Приём текста, ссылки и голоса с предпросмотром извлечения

**Files:**
- Create: `src/bodrye_bot/application/ingest.py`
- Create: `src/bodrye_bot/infrastructure/transcription.py`
- Create: `src/bodrye_bot/infrastructure/source_fetch.py`
- Modify: `src/bodrye_bot/domain/ports.py`
- Modify: `src/bodrye_bot/bot/handlers.py`
- Test: `tests/unit/test_ingest_service.py`
- Test: `tests/contract/test_transcription_adapter.py`
- Test: `tests/contract/test_source_fetcher.py`

**Interfaces:**
- Produces: `IngestService.ingest_text(owner_id: int, text: str) -> ExtractionPreview`.
- Produces: `IngestService.ingest_url(owner_id: int, url: HttpUrl) -> ExtractionPreview`.
- Produces: `IngestService.ingest_voice(owner_id: int, audio: bytes, mime_type: str) -> ExtractionPreview`.
- Consumes: `Transcriber.transcribe(audio: bytes, mime_type: str) -> str`, `SourceFetcher.fetch(url: HttpUrl) -> FetchedSource`, `Extractor.extract(text: str, source_url: str | None) -> ExtractionPreview`.

- [ ] **Step 1: Написать unit-тесты трёх входов**

Use fake `Transcriber` and `Extractor`. Verify that URL metadata retains the original URL, text bypasses transcription, voice uses transcription once, and no draft is generated before Kети confirms the preview.

- [ ] **Step 2: Написать contract-тест расшифровки**

Feed deterministic bytes beginning with the Ogg marker `b"OggS-test-audio"` to a fake HTTP transport and assert normalized non-empty Russian text. Assert provider timeout maps to `SafeError("transcription_failed")` without provider stack trace in user copy.

- [ ] **Step 3: Написать contract-тест безопасного получения страницы**

In `tests/contract/test_source_fetcher.py`, verify that the adapter accepts only `http` and `https`, rejects loopback/private/link-local targets before network access, limits redirects to three, caps response size at 2 MB, accepts HTML/text, and maps timeout or unsupported content to `source_unavailable`.

- [ ] **Step 4: Запустить тесты и подтвердить падение**

Run: `.\.venv\Scripts\pytest.exe tests/unit/test_ingest_service.py tests/contract/test_transcription_adapter.py tests/contract/test_source_fetcher.py -v`

Expected: FAIL because ingest interfaces do not exist.

- [ ] **Step 5: Реализовать прикладной сервис и адаптеры**

Persist the input kind, source URL, fetched page title, bounded text extraction, transcription, extraction preview, owner ID and UTC timestamp. `source_fetch.py` must resolve and validate every redirect target to prevent SSRF. `transcription.py` sends multipart audio to `POST https://api.openai.com/v1/audio/transcriptions` using `OPENAI_TRANSCRIPTION_MODEL`, a 60-second timeout and no SDK-level automatic retries; application code performs the single controlled retry. Do not persist raw audio after a successful transcription. On failure, keep no audio beyond request processing.

- [ ] **Step 6: Подключить Telegram handlers**

Text, URL and voice handlers render the same preview keyboard: `Подтвердить извлечение`, `Исправить`, `Удалить`. `Подтвердить извлечение` starts review; it does not generate a post.

- [ ] **Step 7: Запустить проверки**

Run:

```powershell
.\.venv\Scripts\pytest.exe tests/unit/test_ingest_service.py tests/contract/test_transcription_adapter.py tests/contract/test_source_fetcher.py -v
.\.venv\Scripts\ruff.exe check src tests
.\.venv\Scripts\mypy.exe src
```

Expected: all pass.

- [ ] **Step 8: Зафиксировать приём материалов**

```powershell
git add src/bodrye_bot/application/ingest.py src/bodrye_bot/infrastructure/transcription.py src/bodrye_bot/infrastructure/source_fetch.py src/bodrye_bot/domain/ports.py src/bodrye_bot/bot/handlers.py tests
git commit -m "feat: ingest text links and voice"
```

---

### Task 5: Атомарные медицинские утверждения и evidence gate

**Files:**
- Create: `src/bodrye_bot/application/review.py`
- Create: `src/bodrye_bot/domain/policies.py`
- Create: `src/bodrye_bot/infrastructure/llm.py`
- Modify: `src/bodrye_bot/domain/ports.py`
- Test: `tests/unit/test_medical_review.py`
- Test: `tests/unit/test_approval_policy.py`
- Test: `tests/contract/test_llm_structured_output.py`

**Interfaces:**
- Produces: `ReviewService.review(preview_id: UUID) -> MedicalReview`.
- Produces: `ApprovalPolicy.evaluate(version: DraftVersion, review: MedicalReview | None) -> ApprovalDecision`.
- Consumes: `ClaimExtractor.extract(text: str) -> list[AtomicClaim]`, `EvidenceReviewer.review(claims: list[AtomicClaim]) -> MedicalReview`.

- [ ] **Step 1: Написать тесты разбиения и оценки**

Cover one sentence containing two claims, `SUPPORTED/GREEN`, `INSUFFICIENT/YELLOW`, `REFUTED/RED`, missing citation, and exact source provenance per claim.

- [ ] **Step 2: Написать тесты approval gate**

Required assertions:

```python
assert policy.evaluate(draft, None).allowed is False
assert policy.evaluate(draft, incomplete_review).reason == "medical_review_incomplete"
assert policy.evaluate(draft, red_review).allowed is False
assert policy.evaluate(draft, yellow_complete_review).allowed is True
assert policy.evaluate(non_medical_draft, empty_complete_review).allowed is True
```

- [ ] **Step 3: Написать контракт структурированного ответа LLM**

Validate JSON against Pydantic schemas. Unknown enum value, missing provenance, malformed URL or prose outside JSON maps to `llm_invalid_output`; retry exactly once with the same correlation ID.

- [ ] **Step 4: Запустить тесты и подтвердить падение**

Run: `.\.venv\Scripts\pytest.exe tests/unit/test_medical_review.py tests/unit/test_approval_policy.py tests/contract/test_llm_structured_output.py -v`

Expected: FAIL because review services and policies do not exist.

- [ ] **Step 5: Реализовать review и policy**

The LLM proposes claims but cannot set approval. `ApprovalPolicy` is deterministic Python code. `ResponsesLLMAdapter` sends requests with `httpx.AsyncClient` to `POST https://api.openai.com/v1/responses`, uses `OPENAI_MODEL`, requests JSON Schema structured output, sets a 60-second timeout and disables transport retries; the adapter performs at most one schema-repair retry with the same correlation ID. Each citation stores URL, title, publisher, publication date when available, access timestamp and supporting excerpt hash.

- [ ] **Step 6: Подключить пользовательский обзор**

Render compact verdict and risk per claim, with buttons `Почему?`, `Источник`, `Исправить тезис`, `Отклонить тему`. Never place provider reasoning or hidden prompts in Telegram output.

- [ ] **Step 7: Запустить полную проверку задачи**

Run:

```powershell
.\.venv\Scripts\pytest.exe tests/unit/test_medical_review.py tests/unit/test_approval_policy.py tests/contract/test_llm_structured_output.py -v
.\.venv\Scripts\ruff.exe check src tests
.\.venv\Scripts\mypy.exe src
```

Expected: all pass.

- [ ] **Step 8: Зафиксировать medical gate**

```powershell
git add src/bodrye_bot/application/review.py src/bodrye_bot/domain src/bodrye_bot/infrastructure/llm.py src/bodrye_bot/bot tests
git commit -m "feat: gate drafts on medical evidence"
```

---

### Task 6: Три подачи и Telegram-контент-пакет

**Files:**
- Create: `src/bodrye_bot/application/content.py`
- Modify: `src/bodrye_bot/domain/models.py`
- Modify: `src/bodrye_bot/domain/ports.py`
- Modify: `src/bodrye_bot/bot/handlers.py`
- Modify: `src/bodrye_bot/bot/keyboards.py`
- Test: `tests/unit/test_content_service.py`
- Test: `tests/contract/test_content_schema.py`

**Interfaces:**
- Produces: `ContentService.propose_angles(preview_id: UUID) -> tuple[Angle, Angle, Angle]`.
- Produces: `ContentService.generate_package(preview_id: UUID, angle_id: UUID) -> ContentPackage`.
- `ContentPackage` contains `headlines: tuple[str, ...]`, `post_markdown: str`, `stories: tuple[StoryScript, ...]`, `source_notes: tuple[SourceNote, ...]`.

- [ ] **Step 1: Написать тесты трёх разных подач**

Assert exactly three named angles: `Полезно`, `С юмором`, `Вместе`; each has a distinct hook and uses only claims present in the reviewed preview.

- [ ] **Step 2: Написать тесты пакета**

Assert 3–5 non-empty headlines, one Telegram Markdown post, 2–3 Stories, each Story has `hook`, `body`, `cta`, `visual_hint`, and source notes never appear inside publication copy.

- [ ] **Step 3: Написать contract-тест Pydantic-схемы**

Reject a fourth story, missing CTA, invented citation URL, prohibited dosage instruction, or a claim not present in the approved review set.

- [ ] **Step 4: Запустить тесты и подтвердить падение**

Run: `.\.venv\Scripts\pytest.exe tests/unit/test_content_service.py tests/contract/test_content_schema.py -v`

Expected: FAIL because content service and schemas do not exist.

- [ ] **Step 5: Реализовать генерацию через типизированный порт**

Build prompts from extraction preview, reviewed claims, brand voice and chosen angle. Store the generated package as `DraftVersion(version_number=1, status=DRAFT)` and retain the exact review IDs used.

- [ ] **Step 6: Подключить Telegram presentation**

Show angle cards first. Generate the package only after an angle callback. Split long previews into Telegram-safe messages without splitting URLs or Markdown entities.

- [ ] **Step 7: Запустить проверки и зафиксировать**

Run:

```powershell
.\.venv\Scripts\pytest.exe tests/unit/test_content_service.py tests/contract/test_content_schema.py -v
.\.venv\Scripts\ruff.exe check src tests
.\.venv\Scripts\mypy.exe src
git add src tests
git commit -m "feat: generate telegram content packages"
```

---

### Task 7: Версии, ручные и голосовые правки, повторная проверка и утверждение

**Files:**
- Create: `src/bodrye_bot/application/editorial.py`
- Modify: `src/bodrye_bot/domain/policies.py`
- Modify: `src/bodrye_bot/bot/handlers.py`
- Modify: `src/bodrye_bot/bot/keyboards.py`
- Test: `tests/unit/test_editorial_service.py`
- Test: `tests/integration/test_editorial_workflow.py`

**Interfaces:**
- Produces: `EditorialService.apply_manual_edit(version_id: UUID, body: str) -> DraftVersion`.
- Produces: `EditorialService.apply_instruction(version_id: UUID, instruction: str) -> DraftVersion`.
- Produces: `EditorialService.approve(version_id: UUID, owner_id: int) -> DraftVersion`.
- Consumes: `SemanticChangeDetector.compare(old: DraftVersion, new: DraftVersion) -> ChangeAssessment` and `ApprovalPolicy` from Task 5.

- [ ] **Step 1: Написать unit-тесты версионирования**

Assert edits create a new immutable version, increment version number, preserve old content, and clear `approved_at`. Style-only change may reuse a complete review; medical semantic change sets `review_status=STALE`.

- [ ] **Step 2: Написать интеграционный сценарий защиты**

Scenario:

```text
generate v1 -> review v1 green -> edit dosage into v2 -> approve v2
```

Expected: approval fails with `medical_review_incomplete`; after review v2, approval succeeds; v1 remains unapproved and immutable.

- [ ] **Step 3: Написать тест owner consent**

Approval with any owner ID other than `Settings.telegram_owner_id` must fail even if the caller bypasses Telegram middleware and calls the service directly.

- [ ] **Step 4: Запустить тесты и подтвердить падение**

Run: `.\.venv\Scripts\pytest.exe tests/unit/test_editorial_service.py tests/integration/test_editorial_workflow.py -v`

Expected: FAIL because editorial service does not exist.

- [ ] **Step 5: Реализовать детерминированный workflow**

Only `EditorialService.approve` may set `APPROVED`. It must execute policy evaluation, record owner ID and UTC timestamp, append an audit event, and reject stale/missing/red reviews. LLM output cannot write status fields.

- [ ] **Step 6: Подключить ручные и голосовые инструкции**

Manual full-text edit and transcribed instruction create versions through the same service. The Telegram handler renders a diff summary and the new review status before showing further actions.

- [ ] **Step 7: Запустить проверки и зафиксировать**

Run:

```powershell
.\.venv\Scripts\pytest.exe tests/unit/test_editorial_service.py tests/integration/test_editorial_workflow.py -v
.\.venv\Scripts\ruff.exe check src tests
.\.venv\Scripts\mypy.exe src
git add src tests
git commit -m "feat: add versioned editorial approval"
```

---

### Task 8: Планирование, публикация и восстановление после ошибки

**Files:**
- Create: `src/bodrye_bot/application/publish.py`
- Create: `src/bodrye_bot/infrastructure/telegram.py`
- Create: `src/bodrye_bot/worker.py`
- Modify: `src/bodrye_bot/bot/handlers.py`
- Modify: `src/bodrye_bot/bot/keyboards.py`
- Test: `tests/unit/test_publish_service.py`
- Test: `tests/integration/test_publication_worker.py`
- Test: `tests/contract/test_telegram_publisher.py`

**Interfaces:**
- Produces: `PublishService.schedule(version_id: UUID, owner_id: int, local_datetime: datetime) -> ScheduledPublication`.
- Produces: `PublicationWorker.run_once(now_utc: datetime) -> WorkerResult`.
- Consumes: `TelegramPublisher.publish(channel_id: int, body: str) -> int` returning Telegram message ID.

- [ ] **Step 1: Написать тесты расписания**

Assert schedule is rejected for draft, stale review, red review, wrong owner and past Moscow time. Assert valid Moscow local time is stored in UTC and remains linked to the exact approved version.

- [ ] **Step 2: Написать тесты worker**

Cover success, Telegram timeout, retry without duplicate publication, two concurrent workers, and permanent failure retaining the approved version. A successful retry stores exactly one Telegram message ID.

- [ ] **Step 3: Написать Telegram contract-тест**

Use a fake HTTP transport to assert channel ID, Markdown formatting, disabled link previews for stable post layout, and mapping of API errors to safe internal codes.

- [ ] **Step 4: Запустить тесты и подтвердить падение**

Run: `.\.venv\Scripts\pytest.exe tests/unit/test_publish_service.py tests/integration/test_publication_worker.py tests/contract/test_telegram_publisher.py -v`

Expected: FAIL because publish service and worker do not exist.

- [ ] **Step 5: Реализовать расписание и worker**

The worker claims due rows transactionally, sends the exact stored body, marks success with Telegram message ID, and retries transient errors with bounded exponential backoff. It never regenerates content during publication.

- [ ] **Step 6: Подключить выбор даты и времени**

After approval, Telegram shows calendar/date input and time input in `Europe/Moscow`, then a final confirmation containing channel, date, time and version number. Only that confirmation calls `PublishService.schedule`.

- [ ] **Step 7: Запустить проверки и зафиксировать**

Run:

```powershell
.\.venv\Scripts\pytest.exe tests/unit/test_publish_service.py tests/integration/test_publication_worker.py tests/contract/test_telegram_publisher.py -v
.\.venv\Scripts\ruff.exe check src tests
.\.venv\Scripts\mypy.exe src
git add src tests
git commit -m "feat: schedule approved telegram posts"
```

---

### Task 9: Композиция приложения, журналирование, бюджет и выпуск ядра

**Files:**
- Create: `src/bodrye_bot/observability.py`
- Create: `src/bodrye_bot/main.py`
- Create: `tests/e2e/test_private_editorial_core.py`
- Create: `tests/unit/test_observability.py`
- Create: `README.md`
- Create: `scripts/check.sh`
- Create: `scripts/check.ps1`
- Modify: `.env.example`

**Interfaces:**
- Produces: `build_application(settings: Settings) -> Application`.
- Produces: one bot process and one publication worker process using the same PostgreSQL database.
- Consumes: all ports and implementations from Tasks 1–8.

- [ ] **Step 1: Написать закрытый end-to-end тест**

With fake LLM, transcriber and Telegram publisher, execute:

```text
owner sends voice -> extraction preview -> confirm -> review -> choose humorous angle
-> generate package -> voice edit -> medical re-review -> approve
-> choose Moscow datetime -> worker publishes exact approved version
```

Assert one publication, 2–3 Stories in the package, immutable version history, audit events and no retained raw audio.

- [ ] **Step 2: Написать тесты бюджета и журналов**

Verify each external call records provider, operation, token/audio units, estimated RUB cost, correlation ID and content ID without raw secrets or full medical text. Crossing 80% of `MONTHLY_BUDGET_RUB` emits one owner warning per budget period.

- [ ] **Step 3: Запустить тесты и подтвердить падение**

Run: `.\.venv\Scripts\pytest.exe tests/e2e/test_private_editorial_core.py tests/unit/test_observability.py -v`

Expected: FAIL because application composition and observability do not exist.

- [ ] **Step 4: Реализовать композицию и структурированные журналы**

Create dependencies once in `build_application`; handlers receive application services, not global SDK clients. Configure structlog JSON output and redact keys matching `token`, `secret`, `authorization`, and `audio`.

- [ ] **Step 5: Добавить единые команды проверки**

`scripts/check.ps1` must run:

```powershell
.\.venv\Scripts\ruff.exe check src tests
.\.venv\Scripts\mypy.exe src
.\.venv\Scripts\pytest.exe -v
```

`scripts/check.sh` runs the equivalent executables from `.venv/bin/`.

- [ ] **Step 6: Написать README для локального и серверного запуска**

Document Python 3.12, creation of `.venv`, environment variables, dedicated `_test` database safety rule, migrations, starting `python -m bodrye_bot.main`, starting `python -m bodrye_bot.worker`, adding the bot as channel admin, and rollback to the previous application release without rolling back data migrations.

- [ ] **Step 7: Выполнить полный release gate**

Run:

```powershell
if (-not $env:TEST_DATABASE_URL) { throw 'Set TEST_DATABASE_URL to a dedicated PostgreSQL database ending in _test' }
.\.venv\Scripts\python.exe -c "from bodrye_bot.config import Settings; Settings(); print('test database validated')"
.\scripts\check.ps1
.\.venv\Scripts\alembic.exe downgrade base
.\.venv\Scripts\alembic.exe upgrade head
.\.venv\Scripts\alembic.exe upgrade head
```

Expected: Ruff, mypy and the complete test suite pass; empty and repeated migrations pass; no test connects to a non-test database.

- [ ] **Step 8: Провести ручной Telegram smoke test**

Use the real private bot and a private test channel. Verify unauthorized access, one text input, one voice input, one blocked red claim, one edited claim requiring re-review, one scheduled publication and one simulated transient publication error. Record message IDs and timestamps in `docs/verification/editorial-core-smoke.md` without tokens or private health data.

- [ ] **Step 9: Зафиксировать готовое ядро**

```powershell
git add src tests scripts README.md .env.example docs/verification
git commit -m "feat: complete private editorial core"
git status --short
```

Expected: working tree contains no unplanned generated files; local `.env`, logs and `.superpowers/` remain ignored.

## Последующие отдельные планы

После прохождения release gate этого плана создать и согласовать:

1. `2026-08-27-bodrye-lyudi-daily-digest.md` — белый список, разрешённый поиск, дедупликация, ранжирование, карточки и ежедневный запуск в 10:00.
2. `2026-08-27-bodrye-lyudi-events-digest.md` — московская афиша, валидация событий, рекламная маркировка и среда 17:00.
3. `2026-08-27-bodrye-lyudi-soft-launch.md` — облачное развёртывание, резервирование, мониторинг, реакции/опросы, двухнедельный эксперимент и критерии перехода к росту.

Каждый последующий план обязан использовать доменные интерфейсы этого ядра и не дублировать правила утверждения, медицинской проверки или публикации.
