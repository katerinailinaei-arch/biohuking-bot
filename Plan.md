# «Бодрые люди» — пошаговый план реализации MVP

> **Для агентных исполнителей:** REQUIRED SUB-SKILL: использовать `superpowers:subagent-driven-development` (рекомендуется) или `superpowers:executing-plans` и выполнять задачи по одной. Checkbox и таблицы ниже — единый живой трекер.

**Цель:** создать приватного Telegram-агента, с которым Кети за 20–30 минут выбирает тему, проверяет claims, готовит и утверждает точную версию поста и ставит её в расписание.

**Архитектура:** модульный Python-монолит с доменным ядром, типизированными ports и сменными adapters. `bot` и `worker` координируются через PostgreSQL. LLM не имеет прямого доступа к публикации или постоянной памяти.

**Стек:** Python 3.12+, aiogram 3.x, PostgreSQL 16+, SQLAlchemy 2 async, asyncpg, Alembic, Pydantic 2, httpx, feedparser, bleach, structlog, Docker Compose, pytest, Hypothesis, Ruff, mypy strict.

**Спецификация:** `docs/superpowers/specs/2026-08-28-bodrye-lyudi-mvp-design.md`.

**Детальный task-plan:** `docs/superpowers/plans/2026-08-28-bodrye-lyudi-mvp.md`. Он содержит TDD-примеры и точные интерфейсы; этот `Plan.md` задаёт фазы, статусы, гейты и протокол обновления.

**Статус плана:** `READY` — готов к пошаговому исполнению.

---

## 1. Неизменяемые ограничения

- Один allowlisted-владелец; owner check выполняется до любого чтения объекта и повторно в callback.
- Генерация не равна approval; approval привязан к `draft_version_id + content_hash` и сам ничего не публикует.
- `red`, incomplete/manual review, refuted claim, missing provenance, stale review/model/policy или mismatched hash блокируют approval и publication.
- Любая смысловая правка создаёт новую версию и аннулирует stale review/approval.
- `DELIVERY_UNKNOWN` никогда не retry-ится автоматически.
- Groq Free — стартовый runtime. OpenAI выключен до eval, cost guard и явной смены конфигурации; платный fallback запрещён.
- LLM output недоверен: Pydantic `extra="forbid"`, domain validation, одна schema-repair попытка; medical uncertainty не «ремонтируется» в уверенный вывод.
- Пользователь видит только понятный русский текст ошибки и `trace_id`; stack trace, secret, полный prompt и чувствительный исходник в Telegram не попадают.
- Soft budget — 3 500 ₽/месяц, hard budget — 5 000 ₽/месяц с учётом VPS.
- В MVP нет voice, изображений, Stories/Reels, Instagram, афиши, рекламы, мемов/UGC, analytics, автономных агентов и публикации без Кети.
- Production deployment, migration, restore, live Telegram/Groq/Beget smoke, платный API и destructive operation выполняются только после проверки точной цели и нужного согласования.

## 2. Как исполнять каждую задачу

Каждая задача ниже проходит одинаковый малый TDD-цикл:

1. Прочитать указанные разделы spec и проверить фактическое наличие файлов через `rg --files`.
2. Написать узкий failing test для одного наблюдаемого поведения.
3. Запустить точный test node и зафиксировать ожидаемый FAIL.
4. Написать минимальную типизированную реализацию без побочного scope.
5. Повторить точечный тест, затем связанные regression tests.
6. Проверить safe Russian error, owner isolation, редакцию секретов и stale-review/approval там, где это применимо.
7. Обновить README/AGENTS/spec/evals, если изменились команды, структура, поведение, provider/prompt/schema/style gate.
8. Обновить checkbox задачи и строку прогресса только по свежему evidence; проверить `git diff` и `git status`.
9. Сделать узкий commit. Не коммитить чужие или transient-файлы.

Статусы: `⬜ NOT_STARTED`, `🟡 IN_PROGRESS`, `✅ DONE`, `🔴 BLOCKED`. `DONE` без команды, результата и даты evidence запрещён.

## 3. Фазы и задачи

### P0. Управляемый фундамент

**Готовое состояние:** проект воспроизводимо ставится, валидирует config, хранит workflow в PostgreSQL, не нарушает owner/state/audit-инварианты и автоматически валидирует живой план.

#### P0.T0. Валидация и обновление плана — ✅ DONE

- [x] **P0.T0 завершена и имеет evidence.**
- **Файлы:** create `scripts/check_plan.py`, `scripts/update_plan_status.py`, `tests/unit/scripts/test_check_plan.py`, `tests/unit/scripts/test_update_plan_status.py`; modify `README.md`, `.github/workflows/ci.yml`.
- **Тесты:** parser находит P0–P7/T0–T17, запрещает `DONE` без evidence, запрещенный перескок фазы и неизвестный status; updater атомарно меняет task/phase row и evidence journal.
- **Команды:** `python scripts/check_plan.py Plan.md`; пример task update: `python scripts/update_plan_status.py complete-task P0.T0 --evidence "pytest tests/unit/scripts -v; 12 passed; 2026-08-28"`; пример phase update: `python scripts/update_plan_status.py complete-phase P0 --evidence "ruff, mypy, pytest, migration round trip; all exit 0; 2026-08-28"`.
- **Зависит от:** нет.
- **Результат:** CI и локальная команда отвергают некорректный или бездоказательно «закрытый» план.

#### P0.T1. Скаффолд, config, CI и threat fixtures — ✅ DONE

- [x] **P0.T1 завершена и имеет evidence.**
- **Файлы:** create `pyproject.toml`, `.env.example`, `.gitignore`, `README.md`, `.github/workflows/ci.yml`, `src/bodrye_bot/__init__.py`, `src/bodrye_bot/config.py`, `evals/__init__.py`, `tests/unit/test_config.py`, `tests/architecture/test_scope.py`, `tests/security/fixtures/*`.
- **Тесты:** Python 3.12+, strict config, SecretStr, hard/soft budget, OpenAI disabled by default, banned MVP imports/scope, clean install and CI commands.
- **Зависит от:** P0.T0.
- **Результат:** `python -m pip install -e . --group dev`, Ruff, mypy и базовый pytest работают из чистого checkout.

#### P0.T2. Safe errors, hashes и workflow state machine — ✅ DONE

- [x] **P0.T2 завершена и имеет evidence.**
- **Файлы:** create `src/bodrye_bot/domain/common.py`, `domain/errors.py`, `domain/workflow.py`; tests `tests/unit/domain/test_errors.py`, `test_workflow.py`, `tests/property/test_workflow_transitions.py`.
- **Тесты:** все разрешённые/запрещённые переходы, stable hash, immutable state, stale approval, `DELIVERY_UNKNOWN` manual-only, SafeError с Russian fields и `trace_id`.
- **Зависит от:** P0.T1.
- **Результат:** чистое доменное ядро без aiogram/SQLAlchemy/provider SDK.

#### P0.T3. PostgreSQL schema, Alembic и constraints — ✅ DONE

- [x] **P0.T3 завершена и имеет evidence.**
- **Файлы:** create `alembic.ini`, `src/bodrye_bot/db/base.py`, `db/models/*.py`, `db/migrations/env.py`, initial revision; tests `tests/integration/test_migrations.py`, `test_constraints.py`.
- **Тесты:** upgrade → downgrade → upgrade на PostgreSQL 16; owner-qualified FKs, unique approval/hash, job idempotency, immutable versions, audit and operational tables.
- **Зависит от:** P0.T2.
- **Результат:** схема блокирует основные нарушения даже при ошибке application layer.

#### P0.T4. Owner-scoped repositories, UoW и audit — ✅ DONE

- [x] **P0.T4 завершена и имеет evidence.**
- **Файлы:** create `src/bodrye_bot/ports/repositories.py`, `db/repositories/*.py`, `db/uow.py`, `operations/audit.py`; tests `tests/integration/test_repository_ownership.py`, `test_uow.py`, `test_audit.py`.
- **Тесты:** cross-owner read/write = 0, optimistic lock, transaction rollback, append-only audit, secret/prompt/source redaction.
- **Зависит от:** P0.T3.
- **Результат:** все repositories требуют `owner_id`, каждая мутация аудируется.

**Gate P0 — ✅ PASS:** P0.T0–P0.T4 = DONE; независимая проверка = PASS; `python scripts/check_plan.py Plan.md`, `python -m ruff check .`, `python -m mypy src evals`, full existing pytest и PostgreSQL migration round trip вышли с code 0; owner/audit нарушений 0. Evidence записан в журнале ниже.

### P1. Безопасный Telegram shell и провайдер

**Готовое состояние:** только Кети видит Telegram shell; onboarding проверяет readiness; Groq/OpenAI следуют одному typed contract, а активация модели возможна только после eval.

#### P1.T5. Owner-only Telegram, callbacks, safe views, onboarding — ✅ DONE

- [x] **P1.T5 завершена и имеет evidence.**
- **Файлы:** create `identity/service.py`, `identity/sensitive.py`, `telegram/router.py`, `telegram/views.py`, `telegram/onboarding.py`, `main_bot.py`, `bootstrap.py`; tests `tests/unit/identity/*`, `tests/security/test_callback_ownership.py`, `tests/e2e/test_onboarding.py`.
- **Тесты:** guard-before-read, callback re-authorization, opaque signed callback, HTML escaping, transient sensitive input, readiness gates, все safe codes и Russian copy из раздела 5.
- **Зависит от:** Gate P0.
- **Результат:** AC-01, AC-02, AC-17 доказаны через tests.

#### P1.T6. Typed LLM contract, retries, Groq/OpenAI adapters, usage — ✅ DONE

- [x] **P1.T6 завершена и имеет evidence.**
- **Файлы:** create `ports/llm.py`, `providers/llm_base.py`, `providers/groq.py`, `providers/openai.py`, `operations/usage.py`; tests `tests/contract/test_llm_contract.py`, `tests/unit/providers/test_retry.py`, `test_schema_validation.py`, `test_safe_errors.py`.
- **Тесты:** same normalized semantics, malformed JSON, extra field, wrong enum, refusal, 429, quota exhausted, 5xx, timeout, retry count, single repair, no paid fallback, usage/redaction.
- **Зависит от:** P1.T5.
- **Результат:** provider failure выходит только как typed SafeError; сохранённый workflow не теряется.

#### P1.T7. Versioned model/style/safety eval gate — ✅ DONE

- [x] **P1.T7 завершена и имеет evidence.**
- **Файлы:** create `evals/dataset.jsonl`, `evals/run.py`, `evals/report.py`, `src/bodrye_bot/operations/model_activation.py`; tests `tests/unit/evals/test_report.py`, `tests/e2e/test_model_activation.py`.
- **Тесты:** 100% hard safety fixtures, schema validity, blind style scoring, latency/tokens, model/prompt/schema versions, activation rollback.
- **Зависит от:** P1.T6.
- **Результат:** непрошедшая модель не активируется; AC-13 и провайдерная часть AC-14/AC-18 доказаны.

**Gate P1 — ✅ PASS:** P1.T5–P1.T7 = DONE; независимые проверки = PASS; unauthorized reads/writes = 0; оба adapter contract suites зелёны; malformed/refusal/timeout/quota fixtures дают безопасный Russian UX; hard eval fixtures = 100%; OpenAI не активен по умолчанию. Full pytest, focused owner/provider/eval gate, offline fake eval, Ruff, strict mypy и `check_plan.py` вышли с code 0; evidence записан в журнале ниже.

### P2. Воспроизводимый стиль канала

**Готовое состояние:** Кети проходит калибровку с нуля; только явно подтверждённые правила попадают в bounded StyleContext.

#### P2.T8. Calibration, StyleContext и approval-based learning — 🟡 IN_PROGRESS

- [ ] **P2.T8 завершена и имеет evidence.**
- **Файлы:** create `domain/style.py`, `style/calibration.py`, `style/context.py`, `style/learning.py`; tests `tests/unit/style/test_calibration.py`, `test_context.py`, `tests/e2e/test_style_learning.py`.
- **Тесты:** 8–10 topics, exactly 3 variants per topic, proposed-only inferred rules, explicit activation, bounded token/context selection, weekly repeated-edit proposal, holdouts.
- **Зависит от:** Gate P1.
- **Результат:** AC-12; 2/3 holdouts приняты без крупной правки, медиана рейтинга не ниже 4/5.
- **Факт:** код и PostgreSQL persistence реализованы в `2f4758a..fa00816`; независимое ревью после четырёх fix-rounds = PASS; full pytest 256 passed, Alembic check/Ruff/strict mypy/Plan validator = PASS. Реальные calibration selections и три holdout-оценки Кети ещё не записаны, поэтому task checkbox и Gate P2 не закрыты.

**Gate P2 — ⏸ PENDING OWNER CALIBRATION:** калибровка должна быть завершена Кети; active profile ссылается только на confirmed rules/examples; holdout gate пройден и сохранён в versioned eval report. Автоматические fixtures подтвердили реализацию gate, но не заменяют решения владельца.

### P3. Безопасные источники и качественный дайджест

**Готовое состояние:** worker безопасно читает только allowlisted-источники, сохраняет provenance и в 10:00 MSK даёт до 3–5 сильных недублирующихся карточек, не додумывая недоступное.

#### P3.T9. Source registry, SSRF-safe fetch, extraction/provenance — ⬜ NOT_STARTED

- [ ] **P3.T9 завершена и имеет evidence.**
- **Файлы:** create `domain/sources.py`, `sources/catalog.py`, `sources/fetcher.py`, `sources/extraction.py`; tests `tests/unit/sources/test_catalog.py`, `tests/security/test_ssrf.py`, `test_prompt_injection.py`.
- **Тесты:** HTTP(S)-only, DNS/IP/redirect revalidation, private/metadata denylist, 10 MiB cap, 5s connect/20s total, max 3 redirects, HTML sanitizing, 24h raw TTL, injection delimiters, unavailable source never reaches LLM.
- **Зависит от:** Gate P2.
- **Результат:** AC-04; registry version/role/access/license/check date и bounded evidence provenance хранятся.

#### P3.T10. Weekday digest, deduplication и quality threshold — ⬜ NOT_STARTED

- [ ] **P3.T10 завершена и имеет evidence.**
- **Файлы:** create `digest/service.py`, `digest/views.py`, `digest/worker.py`; tests `tests/unit/digest/test_deduplication.py`, `test_ranking.py`, `tests/e2e/test_digest_delivery.py`.
- **Тесты:** 10:00 Europe/Moscow weekdays, max 5, threshold prevents padding, URL/content/semantic dedupe, deterministic weights, partial digest with source failures listed by 10:10.
- **Зависит от:** P3.T9.
- **Результат:** AC-03; карточки имеют provenance, risk preview, reason and owner-safe actions.

**Gate P3:** SSRF/injection suite зелёный; source registry утверждён; unavailable и blocked sources не попадают в LLM; digest не заполняется слабыми карточками.

### P4. Evidence-first редакционный цикл

**Готовое состояние:** после подтверждённого extraction бот показывает claims/evidence/ограничения, три разные подачи, создаёт валидный Telegram-пост; unsafe/stale версия не может быть утверждена.

#### P4.T11. Claims, evidence и hard medical gate — ⬜ NOT_STARTED

- [ ] **P4.T11 завершена и имеет evidence.**
- **Файлы:** create `domain/medical.py`, `medical/review.py`, `medical/policy.py`; tests `tests/unit/medical/test_policy.py`, `tests/e2e/test_claim_review.py`.
- **Тесты:** extraction confirmation precondition; atomic claims; supported/refuted/insufficient/manual; green/yellow/red; exact excerpt/hash/provenance; numeric, causal, population, modality and stale-review traps.
- **Зависит от:** Gate P3.
- **Результат:** AC-05, AC-07; любой red/incomplete/refuted/manual/missing/stale блокирует downstream approval.

#### P4.T12. Три подачи, adaptive draft и semantic edit review — ⬜ NOT_STARTED

- [ ] **P4.T12 завершена и имеет evidence.**
- **Файлы:** create `editorial/service.py`, `editorial/validators.py`, `editorial/views.py`; tests `tests/unit/editorial/test_angles.py`, `test_draft.py`, `tests/e2e/test_semantic_edit.py`.
- **Тесты:** exactly 3 materially distinct angles; short/medium/long ranges; max 3,800 Unicode characters; valid Telegram HTML; evidence links; no invented experience; sentence-to-claim map; changed number/modality/causality/population/action/limitation revokes review/approval.
- **Зависит от:** P4.T11, Gate P2.
- **Результат:** AC-06, AC-08, AC-19; новая смысловая версия возвращается в medical review.

**Gate P4:** 100% red/incomplete/missing/stale fixtures блокируются; claims/evidence видны до draft; три подачи структурно разные; semantic edits аннулируют stale decision.

### P5. Точное approval, schedule и консервативная публикация

**Готовое состояние:** Кети явно утверждает точную версию, видит final preview и планирует MSK-время; worker не публикует иную версию и не создаёт дубль при неизвестном исходе.

#### P5.T13. Approval, scheduling, leases и Telegram delivery states — ⬜ NOT_STARTED

- [ ] **P5.T13 завершена и имеет evidence.**
- **Файлы:** create `domain/publication.py`, `ports/telegram.py`, `providers/telegram.py`, `publication/service.py`, `publication/worker.py`, `main_worker.py`; tests `tests/unit/publication/test_policy.py`, `tests/integration/test_publication_lease.py`, `tests/e2e/test_delivery_states.py`.
- **Тесты:** approval sends nothing; hash/version match; strict MSK → UTC; past/ambiguous time blocked; `FOR UPDATE SKIP LOCKED`; success, definite failure, timeout, cancellation, crash; no auto-retry from `DELIVERY_UNKNOWN`; manual mark/retry audit.
- **Зависит от:** Gate P4.
- **Результат:** AC-09, AC-10, AC-11; scheduled jobs выполняются без доступности LLM.

**Gate P5:** 0 sends без matching approval; unique Telegram message ID после success; `DELIVERY_UNKNOWN` имеет только manual exits; lease/restart tests зелёные.

### P6. Операционно готовый и восстанавливаемый MVP

**Готовое состояние:** данные можно сохранять и удалять, квоты/затраты ограничены, health/alerts наблюдаемы, backup зашифрован и доказан restore-тестом, Beget profile hardened.

#### P6.T14. Library, retention, deletion cascade, tombstones — ⬜ NOT_STARTED

- [ ] **P6.T14 завершена и имеет evidence.**
- **Файлы:** create `memory/service.py`, `memory/retention.py`, `memory/tombstones.py`; tests `tests/unit/memory/test_retention.py`, `tests/integration/test_deletion.py`, `test_tombstone_replay.py`.
- **Тесты:** library отдельна от publication state; exact retention; live derivative cascade; shared evidence preserved; tombstone first, idempotent owner-scoped replay, restore replay, 31-day tombstone retention.
- **Зависит от:** Gate P5, P0.T3/P0.T4.
- **Результат:** AC-15; удаление live derivatives завершается не позднее 24 часов.

#### P6.T15. Cost guard, quota isolation, metrics, alerts, health — ⬜ NOT_STARTED

- [ ] **P6.T15 завершена и имеет evidence.**
- **Файлы:** create `operations/costs.py`, `quota.py`, `metrics.py`, `alerts.py`, `health.py`, `healthcheck.py`; tests `tests/unit/operations/test_cost_guard.py`, `tests/e2e/test_quota_isolation.py`, `test_health_gate.py`.
- **Тесты:** unknown cost ≠ zero; 80% warning; hard limit blocks paid AI/search only; Groq quota stops new LLM but not scheduled publication; health fields; deduplicated alerts and secret redaction.
- **Зависит от:** P1.T6, P5.T13, P6.T14.
- **Результат:** AC-14, AC-18; bot/worker/DB/queue/provider/source/backup/resources/release видны без секретов.

#### P6.T16. Encrypted backup, restore proof и hardened Beget deploy — ⬜ NOT_STARTED

- [ ] **P6.T16 завершена и имеет evidence.**
- **Файлы:** create `Dockerfile`, `deploy/compose.prod.yaml`, `deploy/backup/Dockerfile`, `backup.sh`, `restore_test.sh`, `deploy/beget/harden.sh`, `deploy/beget/OPERATIONS.md`; tests `tests/integration/test_backup_restore.py`, `tests/security/test_compose_hardening.py`.
- **Тесты:** daily `pg_dump` → zstd → age → S3 checksum; 30-day retention; exact validated `_restore_test` DB; migrate/tombstone/invariant proof; no public app/DB ports, non-root, no privileged, read-only where compatible, resource/log limits.
- **Зависит от:** P6.T14, P6.T15.
- **Результат:** AC-16; RPO 24h/RTO 4h и immutable rollback описаны и подтверждены журналом.

**Gate P6:** deletion/tombstone suite, budget/quota isolation, health gate, encrypted backup round trip и compose-hardening зелёные; restore proof и runbook сохранены.

### P7. Release candidate и двухнедельный пилот

**Готовое состояние:** весь owner workflow проходит от источника до публикации с restart recovery; AC-01…AC-20 имеют свежее evidence; пилот имеет go/no-go и rollback-процедуру.

#### P7.T17. Acceptance suite, pilot readiness и operator docs — ⬜ NOT_STARTED

- [ ] **P7.T17 завершена и имеет evidence.**
- **Файлы:** create `tests/e2e/test_acceptance.py`, `test_restart_recovery.py`, `docs/pilot/acceptance-matrix.md`, `docs/pilot/two-week-runbook.md`; modify `src/bodrye_bot/bootstrap.py`, `README.md`.
- **Тесты:** complete owner workflow; unauthorized denial; claims gate; three angles; exact approval/hash; schedule/delivery; restart; all 20 AC rows; production smoke with owner-supplied env secrets.
- **Зависит от:** Gate P6 и все предыдущие gates.
- **Результат:** release candidate с full evidence matrix; pilot измеряет card acceptance, agent-originated publish rate, active time, medical incidents, duplicates, costs и health.

**Gate P7:** `python scripts/check_plan.py Plan.md`, `python -m ruff check .`, `python -m mypy src evals`, `python -m pytest -v`, Docker build, restart recovery и 20/20 acceptance rows проходят; live Telegram/Groq/Beget smoke выполнен по разрешению с секретами из env.

## 4. Карта зависимостей

```text
P0.T0 план/CI
  └─ P0.T1 scaffold/config
      └─ P0.T2 domain/errors/state
          └─ P0.T3 PostgreSQL/migrations
              └─ P0.T4 repositories/UoW/audit
                  └─ P1.T5 Telegram/owner/onboarding
                      └─ P1.T6 LLM contract/adapters/usage
                          ├─ P1.T7 eval gate
                          │   └─ P2.T8 style calibration/context
                          │       └─ P3.T9 sources/fetch/provenance
                          │           └─ P3.T10 digest
                          │               └─ P4.T11 medical review
                          │                   └─ P4.T12 editorial flow ← P2.T8
                          │                       └─ P5.T13 publication
                          └─ P6.T15 cost/quota/health ← P5.T13, P6.T14
P0.T3/P0.T4 + P5.T13 ─────────└─ P6.T14 memory/deletion
P6.T14 + P6.T15 ────────────────└─ P6.T16 backup/deploy
all gates ─────────────────────────└─ P7.T17 acceptance/pilot
```

Критический путь: `P0.T0 → T1 → T2 → T3 → T4 → P1.T5 → T6 → T7 → P2.T8 → P3.T9 → T10 → P4.T11 → T12 → P5.T13 → P6.T14 → T15 → T16 → P7.T17`.

## 5. LLM-ошибки: русский UX и технические логи

### 5.1. Общий контракт

`SafeError` хранит `code`, `message_ru`, `preserved_ru`, `next_action_ru`, `trace_id`. Telegram renderer выводит только эти поля. Техническая причина и stack trace пишутся только в developer log с тем же `trace_id`.

| Safe code | Сообщение в Telegram | Что сохранено / что делать | Retry и поведение |
|---|---|---|---|
| `llm_timeout` | «Модель не успела ответить. Технические детали скрыты. Код: `{trace_id}`.» | «Исходник и текущий шаг сохранены. Повторите через минуту.» | Connect 5s, total 60s; retry только если операция доказанно безопасна; не более 2 попыток с jitter. |
| `llm_rate_limit` | «Сервис генерации временно ограничил частоту запросов. Код: `{trace_id}`.» | «Всё введённое и выбранное сохранено. Повторите после указанного времени.» | Respect `Retry-After`; max 2 safe jittered retries. Платный fallback не включать. |
| `llm_quota_exhausted` | «Бесплатная квота модели исчерпана. Новая генерация временно недоступна. Код: `{trace_id}`.» | «Данные сохранены, уже запланированные посты будут отправлены. Проверьте `/status` позже.» | Не retry до сброса/возврата квоты; circuit блокирует только новые LLM calls. |
| `llm_unavailable` | «Сервис генерации сейчас недоступен. Код: `{trace_id}`.» | «Текущий workflow и все подтверждённые данные сохранены. Попробуйте ещё раз позже.» | Max 2 safe retries для 5xx; затем pause и health alert. |
| `llm_invalid_output` | «Модель вернула ответ, который не прошёл проверку. Он не будет использован. Код: `{trace_id}`.» | «Предыдущая проверенная версия сохранена. Можно повторить генерацию.» | Одна repair-попытка для schema-only; refusal и unknown enum/extra field остаются blocked. |
| `medical_review_incomplete` | «Проверка медицинских тезисов не завершена. Утверждение и публикация заблокированы. Код: `{trace_id}`.» | «Черновик, claims и найденные источники сохранены. Откройте причины блокировки и исправьте тезисы.» | Не auto-repair и не auto-approval; только новый evidence/review. |
| `internal_error` | «Не удалось завершить действие. Технические детали скрыты. Код: `{trace_id}`.» | «Уже сохранённые данные не изменены. Повторите или сообщите код разработчику.» | Не делать blind retry мутации; alert по `trace_id`. |

Все остальные safe codes из spec section 18 (`owner_forbidden`, `source_unavailable`, `source_blocked`, `extraction_failed`, `style_profile_not_ready`, `approval_stale`, `publication_failed`, `delivery_unknown`, `backup_stale`) реализуют тот же контракт и тестируются в P1.T5. Provider refusal не создаёт новый public code без изменения spec: он нормализуется в `llm_invalid_output`, а developer log содержит `provider_error_class="refusal"`.

### 5.2. Обязательные поля developer log

Одно structured event на завершившийся LLM call:

```json
{
  "event": "llm_call_finished",
  "level": "warning",
  "safe_error_code": "llm_timeout",
  "trace_id": "same-as-user-message",
  "owner_id_hash": "non-reversible-operational-id",
  "workflow_id": "uuid-or-null",
  "operation": "generate_draft",
  "provider": "groq",
  "model": "configured-model-id",
  "provider_request_id": "redacted-or-null",
  "attempt": 2,
  "duration_ms": 60000,
  "http_status": null,
  "retryable": false,
  "outcome": "failed",
  "prompt_version": "version-id",
  "schema_version": "version-id",
  "input_tokens": null,
  "output_tokens": null,
  "provider_error_class": "timeout",
  "exception_class": "TimeoutError"
}
```

Запрещено логировать API keys/tokens, authorization headers, URL credentials, Telegram payload целиком, полный prompt, полный source text, медицинские данные и raw LLM response. Stack trace разрешён только в защищённом developer sink и не должен дублировать payload.

### 5.3. Тесты error boundary

- Каждый safe code имеет непустой `message_ru`, `preserved_ru`, `next_action_ru`, `trace_id`.
- Telegram response не содержит exception class, stack trace, provider body, API key, prompt и raw source.
- Developer event и Telegram response имеют один `trace_id`.
- Timeout/429/5xx не превышают retry budget; quota не retry-ится; OpenAI fallback не вызывается.
- Invalid schema имеет ровно одну repair-попытку; extra field, wrong enum, refusal и повторный invalid output блокируют результат.
- После LLM failure workflow остаётся на последнем durable state; уже scheduled publication не зависит от provider health.

## 6. Таблица прогресса

| Фаза | План | Факт | Статус | Блокеры |
|---|---|---|---|---|
| P0 | T0–T4: plan tooling, scaffold, domain, DB, repositories/audit | Independent P0.T4 review PASS; full pytest 105 passed; Ruff clean; strict mypy clean (24 files); Plan validator OK; PostgreSQL 0002→0004 downgrade-upgrade and Alembic check passed; current head 0004; owner/audit violations 0 | ✅ DONE | Нет |
| P1 | T5–T7: owner Telegram, LLM adapters, eval | P1.T5-P1.T7 DONE with independent PASS reviews; full pytest 217 passed in 26.42s; focused owner/provider/eval gate 111 passed; fake eval 12/12 with schemas 100%, hard violations 0, safety 1.0; OpenAI remains fail-closed; Ruff clean; strict mypy clean (42 files). | ✅ DONE | Нет |
| P2 | T8: style calibration and learning | Implementation `2f4758a..fa00816`; independent review PASS; full pytest 256 passed; Alembic drift 0; Ruff/mypy clean. Owner calibration evidence ещё не создано | 🟡 IN_PROGRESS | Кети должна пройти 8–10 calibration topics и оценить 3 unseen holdouts; затем сохранить versioned eval report |
| P3 | T9–T10: sources and digest | Код не создан | ⬜ NOT_STARTED | Gate P2; утверждённый source registry |
| P4 | T11–T12: medical and editorial | Код не создан | ⬜ NOT_STARTED | Gate P3 |
| P5 | T13: approval/schedule/publication | Код не создан | ⬜ NOT_STARTED | Gate P4; test Telegram channel для live gate |
| P6 | T14–T16: deletion, costs, health, backup, deploy | Код не создан | ⬜ NOT_STARTED | Gate P5; Beget/S3/age inputs для live gate |
| P7 | T17: acceptance and pilot | Код не создан | ⬜ NOT_STARTED | Gate P6; owner secrets/permissions для production smoke |

## 7. Автообновление после задачи и фазы

### 7.1. После каждой задачи

1. Исполнитель запускает точечные и regression tests.
2. Исполнитель выполняет `complete-task`; updater меняет task status, «Факт» и evidence journal.
3. `check_plan.py` проверяет синтаксис, зависимости, evidence и неперескакивание gate.
4. Изменение `Plan.md` входит в тот же узкий commit, что и реализация.

### 7.2. После каждой фазы

1. Пройти `python -m ruff check .`, `python -m mypy src evals`, весь накопленный `python -m pytest -v` и специфичный gate фазы.
2. Записать evidence: UTC дату, git commit, команды, exit codes, pass/fail counts, отчёт/artefact path, известные ограничения.
3. Выполнить `complete-phase`; updater меняет phase status, «Факт», blockers и journal.
4. Повторно запустить `python scripts/check_plan.py Plan.md`.
5. Не начинать следующую фазу, если gate красный или не записан evidence.

### 7.3. Формат evidence journal

| UTC date | Task/Phase | Commit | Commands and result | Evidence artifact | Limitations |
|---|---|---|---|---|---|
| 2026-08-30T07:24:48Z | P2.T8 implementation | fa00816+worktree | Commits 2f4758a..fa00816; TDD RED missing style modules/repository/invariants; 4 fix rounds; independent review PASS; controller full pytest 256 passed in 45.33s; Alembic check no drift; Ruff clean; strict mypy 50 files; Plan validator OK. | Plan.md | Task/Gate P2 остаются IN_PROGRESS до реальных calibration selections и 3 holdout ratings Кети; fixtures не заменяют owner decision |
| 2026-08-29T23:22:10Z | P2.T8 start | 1cc8d90+worktree | Gate P1 подтверждён; plan validator: PLAN_OK; baseline full pytest: 217 passed in 43.65s; начата TDD-реализация утверждённого style contract. | Plan.md | Реальная calibration/holdout требует решений Кети и не считается пройденной по unit/e2e fixtures |
| 2026-08-29T20:19:39Z | P1 | 9afd740+worktree | P1.T5-P1.T7 DONE with independent PASS reviews; full pytest 217 passed in 26.42s; focused owner/provider/eval gate 111 passed; fake eval 12/12 with schemas 100%, hard violations 0, safety 1.0; OpenAI remains fail-closed; Ruff clean; strict mypy clean (42 files). | Plan.md | Нет |
| 2026-08-29T20:19:39Z | P1.T7 | 9afd740+worktree | Commits ff72cc4..9afd740; independent review PASS; strict 12-case versioned dataset, deterministic offline report, typed LLMProvider adapter, fail-closed exact-fixture activation and immutable rollback audit; focused eval 35 passed; fake CLI 12/12, schemas 100%, hard violations 0, safety 1.0, deterministic SHA 86839D...37D04. | Plan.md | Нет |
| 2026-08-29T19:27:50Z | P1.T6 | cee3df1+worktree | Commits 41f8a6a..cee3df1; independent review PASS; strict provider-neutral Groq/OpenAI contract, definite-safe retries, quota circuit, one safe schema repair, Russian SafeError UX, redacted aggregate usage, OpenAI fail-closed; full pytest 182 passed in 36.83s; Ruff clean; strict mypy clean (39 files). | Plan.md | Нет |
| 2026-08-29T14:21:18Z | P1.T5 | d6077fb+worktree | Commits 025ecef..d6077fb; independent review PASS; owner-first Telegram shell, signed opaque callbacks, Russian safe errors, owner-bound sensitive input with autonomous TTL, five onboarding gates; full pytest 128 passed in 26.13s; Ruff clean; strict mypy clean (33 files). | Plan.md | Нет |
| 2026-08-29T12:44:39Z | P0 | 38556bd+worktree | Independent P0.T4 review PASS; full pytest 105 passed; Ruff clean; strict mypy clean (24 files); Plan validator OK; PostgreSQL 0002→0004 downgrade-upgrade and Alembic check passed; current head 0004; owner/audit violations 0 | Plan.md | Нет |
| 2026-08-29T12:43:27Z | P0.T4 | 38556bd+worktree | Independent review PASS; P0.T4 28 passed; full pytest 105 passed; Ruff, strict mypy, Alembic check and migration round-trip passed; app+DB audit envelope, append-only linkage, owner-scoped hydration/save verified | Plan.md | Нет |
| 2026-08-29T07:58:00Z | P0.T4 | 236b28d+worktree | TDD RED: 3 import errors, then nested mutation/TRUNCATE/unknown-event constraints failed; focused P0 PostgreSQL: 48 passed; full pytest: 98 passed; Ruff and strict mypy: clean; Alembic 0001→0002 downgrade-upgrade and check: passed; append-only audit and persisted recursive redaction verified | Plan.md | PostgreSQL 17.11 локально; PostgreSQL 16 проверяется CI после push |
| 2026-08-28T18:52:28Z | P0.T4 start | 236b28d+worktree | baseline full pytest: 77 passed; branch synced with origin/feature/p0-foundation; PostgreSQL test database available | Plan.md | Реализация repositories/UoW/audit ещё не начата |
| 2026-08-28T18:37:10Z | P0.T3 | 4a9e429+worktree | Fix artifact 4a9e429: focused PostgreSQL 26 passed; Alembic downgrade-upgrade; full pytest 77 passed; Ruff and strict mypy clean; CI fails closed without TEST_DATABASE_URL | Plan.md | Нет |
| 2026-08-28T15:46:47Z | P0.T3 start | b2fd65e+worktree | baseline pytest: 50 passed; pg_isready 127.0.0.1:55432: accepting connections; PostgreSQL 17.11; public tables: 0 | Plan.md | Статус исправлен на IN_PROGRESS; schema/migrations ещё не реализованы |
| 2026-08-28T13:22:35Z | P0.T3 | 634104f | docker --version: команда отсутствует; PostgreSQL 17 service: running; port 5433: listening; psql без пароля: authentication rejected | Plan.md | Нужен локальный TEST_DATABASE_URL к отдельной тестовой БД; секрет не передавать в чат |
| 2026-08-28T13:14:28Z | P0.T2 | f157e1c+worktree | pytest: 50 passed including 32 domain/property tests; ruff: passed; mypy: passed; plan validation and diff check: passed | Plan.md | Нет |
| 2026-08-28T13:07:44Z | P0.T1 | 28b4684+worktree | pytest: 18 passed; ruff: passed; mypy: passed; clean install: passed; plan validation and diff check: passed | Plan.md | Нет |
| 2026-08-28T12:49:53Z | P0.T0 | 327cfc5+worktree | python -m unittest discover -s tests/unit/scripts -p test_*.py -v: 8 passed; python scripts/check_plan.py Plan.md: PLAN_OK phases=8 tasks=18; git diff --check: exit 0 | Plan.md | Нет |

## 8. Definition of Done фазы

Фаза считается готовой, только если одновременно:

- все её task checkbox и task rows = `DONE`;
- точечные, regression, Ruff, strict mypy и полный накопленный pytest прошли по свежему выводу;
- специфичный gate фазы прошёл;
- покрыты applicable AC, owner isolation, safe Russian error, secret redaction и stale review/approval;
- README/AGENTS/spec/evals/operations docs соответствуют фактическому коду;
- в таблице прогресса и evidence journal есть свежие доказательства;
- `python scripts/check_plan.py Plan.md` завершился с exit code 0;
- blockers пусты или явно перенесены в следующую фазу без нарушения spec;
- выполнена независимая ревизия изменений для security/medical/publication/deployment фаз.

## 9. Критерии release и пилота

- 20/20 acceptance criteria имеют PASS и ссылку на тест/артефакт.
- 100% hard medical/safety fixtures проходят.
- Unauthorized reads/writes = 0; publication without matching approval = 0; automatic retries from `DELIVERY_UNKNOWN` = 0.
- Restore test и production smoke проходят; секреты не попадают в repo/log/Telegram.
- Двухнедельный пилот измеряет, а не предполагает: `card_acceptance_rate >= 60%`, `agent_originated_publish_rate >= 70%`, медиана активного времени на пост `<= 30 минут`, medical incidents = 0, duplicates = 0.
- Go разрешён только по зелёному pilot report; no-go сохраняет данные, откатывает immutable image/config и не ослабляет gates.
