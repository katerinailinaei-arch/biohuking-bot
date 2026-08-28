# AGENTS.md — «Бодрые люди»

Этот файл — стартовая карта проекта для каждой новой агентной сессии. Он задаёт порядок чтения, архитектурные границы и рабочие команды, но не заменяет утверждённую спецификацию.

## 1. О проекте

«Бодрые люди» — полезно-развлекательный Telegram-канал о здоровой жизни после 35 лет и приватный редакционный ИИ-агент Кети. Агент сокращает путь от проверяемой темы до утверждённого поста: собирает дайджест, извлекает claims, показывает evidence и риски, предлагает три подачи, помогает выработать новый стиль и планирует публикацию. Публикация разрешена только после явного approval конкретной версии Кети.

Главный результат MVP: за 20–30 минут безопасно выбрать тему, подготовить один качественный пост, утвердить точную версию и поставить её в расписание.

## 2. Структура проекта

Код создаётся по плану; перед командой или правкой всегда проверяй реальное наличие пути через `rg --files`.

```text
biohuking-bot/
├── AGENTS.md                 # навигация и правила новых сессий
├── idea.md                   # краткая продуктовая идея и границы MVP
├── research.md               # проверенные факты, vendor claims и рекомендации
├── assets/                   # утверждённые визуальные материалы
├── docs/
│   ├── discovery/            # решения интервью до спецификации
│   ├── superpowers/specs/    # каноническая спецификация продукта
│   ├── superpowers/plans/    # исполняемый план разработки
│   ├── pilot/                # acceptance matrix и runbook пилота (после Task 17)
│   └── archive/              # история; не источник текущих требований
├── src/bodrye_bot/           # приложение (создаётся по implementation plan)
│   ├── domain/               # чистые сущности, policies, state machine
│   ├── ports/                # интерфейсы repositories/providers/clock
│   ├── db/                   # SQLAlchemy, repositories, Alembic
│   ├── identity/             # owner allowlist и sensitive input
│   ├── telegram/             # команды, callbacks, русские views
│   ├── sources/              # allowlist, fetch, provenance
│   ├── digest/               # ranking, deduplication, delivery
│   ├── medical/              # claims, evidence, risk gate
│   ├── editorial/            # extraction, angles, drafts, edits
│   ├── style/                # calibration, context, approved learning
│   ├── publication/          # approval, schedule, conservative delivery
│   ├── memory/               # library, retention, deletion, tombstones
│   ├── providers/            # Groq, OpenAI, Telegram, backup adapters
│   └── operations/           # audit, costs, health, alerts
├── tests/                    # unit, property, integration, contract, security, e2e
├── evals/                    # versioned model/style/safety fixtures and reports
└── deploy/                   # Docker/Beget/backup/restore operations
```

## 3. Tech Stack

| Область | Технологии |
|---|---|
| Runtime | Python 3.12+ |
| Telegram | aiogram 3.x, long polling |
| Data | PostgreSQL 16+, SQLAlchemy 2 async, asyncpg, Alembic |
| Schemas/config | Pydantic 2, pydantic-settings |
| HTTP/feeds | httpx, feedparser, bleach |
| LLM | provider-neutral port; Groq Free сначала, OpenAI выключен до eval |
| Operations | structlog, Docker Compose, Beget VPS, encrypted S3 backup |
| Quality | pytest, pytest-asyncio, Hypothesis, Ruff, mypy strict |

## 4. Архитектура

```text
User: Кети в Telegram
      ↓
aiogram adapters (auth, commands, callbacks, views)
      ↓
Application use cases (bot + durable worker)
      ↓
Domain policies (workflow, medical, style, publication, memory)
      ↓
Ports (repositories, LLM, Telegram, clock, blob storage)
      ↓
Adapters → PostgreSQL | Groq/OpenAI APIs | Telegram Bot API
                   | evidence web sources | Beget S3 backup
```

Домен не импортирует aiogram, SQLAlchemy или SDK провайдера. `bot` и `worker` координируются через PostgreSQL; LLM-роли не имеют прямого доступа к публикации или постоянной памяти.

## 5. Пять ключевых решений

1. **Один allowlisted владелец.** MVP — личный инструмент Кети; owner check выполняется до чтения каждого объекта и повторяется в callbacks.
2. **Approval-first.** Генерация не равна согласию; approval фиксирует `draft_version_id + content_hash`, а schedule не может подменить версию.
3. **Claim-level safety gate.** `red`, incomplete, refuted/manual review, missing provenance или stale review блокируют approval; знания модели и конкуренты не являются evidence.
4. **Память принадлежит приложению.** Правила стиля активируются только после решения Кети; правка или отклонение сами ничего не «обучают».
5. **Сменные провайдеры и консервативная доставка.** Groq/OpenAI следуют одному typed contract и проходят eval; `DELIVERY_UNKNOWN` никогда не retry-ится автоматически, чтобы не создавать дубли.

## 6. Тестирование

Расположение:

- `tests/unit/` — чистые policies, ranking, retention, validators;
- `tests/property/` — переходы, hashes, idempotency;
- `tests/integration/` — PostgreSQL constraints, migrations, leases, restore;
- `tests/contract/` — одинаковая нормализованная семантика Groq/OpenAI;
- `tests/security/` — ownership, callback, SSRF, injection, redaction;
- `tests/e2e/` — полный Telegram workflow и restart recovery;
- `evals/` — model/style/safety dataset и activation gate.

Чек-лист задачи:

- [ ] Сначала написан точечный failing test, затем минимальная реализация.
- [ ] Пройдены тесты изменённого модуля и связанные regression tests.
- [ ] Пройдены `python -m ruff check .` и `python -m mypy src evals`.
- [ ] Для migration проверены upgrade → downgrade → upgrade на PostgreSQL.
- [ ] Проверены owner isolation, safe Russian error и отсутствие секретов в логах.
- [ ] Изменение claim/версии аннулирует stale review/approval.
- [ ] Перед phase gate проходит полный `python -m pytest -v`.
- [ ] Успех заявляется только по свежему выводу команд, не по предположению.

## 7. Документация

| Файл | Содержит | Когда обновлять |
|---|---|---|
| `AGENTS.md` | навигацию, границы, команды, source-of-truth | при смене структуры, стека, gates или команд |
| `idea.md` | проблему, JTBD, scope и голос продукта | после утверждённого продуктового решения Кети |
| `research.md` | подтверждённые факты, vendor claims, рекомендации | после фактчекинга нового источника с URL и датой |
| `docs/discovery/2026-08-28-pre-spec-decisions.md` | ответы и решения discovery | не переписывать; новое решение оформлять отдельно |
| `docs/superpowers/specs/2026-08-28-bodrye-lyudi-mvp-design.md` | канонические требования, AC-01…AC-20 | только после явного согласования изменения Кети |
| `docs/superpowers/plans/2026-08-28-bodrye-lyudi-mvp.md` | задачи, интерфейсы, тесты и phase gates | при утверждённой корректировке способа реализации |
| `Plan.md` | живой фазовый трекер, статусы, blockers и evidence | после каждой задачи и каждого phase gate |
| `README.md` | фактическую установку и эксплуатацию | в том же коммите, где меняется команда/переменная |
| `docs/pilot/*` | доказательства acceptance и двухнедельный runbook | при каждом release gate и событии пилота |
| `docs/archive/*` | сохранённые старые версии | не использовать как текущие требования |

Порядок источников истины: явное текущее решение Кети → утверждённая MVP-спецификация → `Plan.md` (фазы, статусы, gates) + текущий implementation plan (интерфейсы, TDD-шаги) → `idea.md` → `AGENTS.md`/`README.md` → `research.md`. При противоречии остановись, покажи конфликт и не угадывай.

## 8. Commands

Сейчас в `master` утверждены документы; scaffold приложения появляется по Task 1. Не выдавай целевые команды за работающие, пока соответствующий файл не существует.

```powershell
# Начало сессии
git status --short
git log -5 --oneline
rg --files

# После появления pyproject.toml
python -m pip install -e . --group dev
python -m ruff check .
python -m mypy src evals
python -m pytest -v

# Локальная инфраструктура и запуск
docker compose up -d postgres
python -m alembic upgrade head
python -m bodrye_bot.main_bot
python -m bodrye_bot.main_worker

# Сборка и health gate
docker compose build
python -m bodrye_bot.healthcheck

# Production: выполнять только по deploy/beget/OPERATIONS.md
docker compose -f deploy/compose.prod.yaml build --pull
docker compose -f deploy/compose.prod.yaml up -d
```

Не запускать production deployment, migration, restore, публикацию, платный API или destructive operation без проверки точной цели и требуемого согласования.

## 9. Самообновление

### В начале каждой новой сессии

1. Прочитай `AGENTS.md`, затем каноническую спецификацию и текущий plan.
2. Проверь `git status`, ветку, последние коммиты и существование путей.
3. Не используй `docs/archive/`, план 2026-08-27 или `feature/editorial-core` как основу.
4. Определи текущую задачу и предыдущий phase gate; не перескакивай красный gate.

### После каждого изменения

1. Код изменил интерфейс/структуру/команду → обнови `README.md` и при необходимости `AGENTS.md` в том же коммите.
2. Продуктовое поведение изменилось → сначала получи решение Кети, затем обнови spec, AC, plan и только после этого код.
3. Добавлен внешний факт → внеси его в `research.md`, разделив факт, заявление поставщика и рекомендацию; укажи источник и дату проверки.
4. Изменена схема БД → добавь Alembic migration и migration test; не редактируй уже применённую revision.
5. Изменён provider/prompt/schema/style gate → обнови versioned `evals/` и сохрани новый отчёт до активации.
6. Завершена задача → запиши свежие test evidence, обнови task status и «Факт» в `Plan.md`, затем проверь `git diff`/`git status`.
7. Завершена фаза → пройди full gate, обнови таблицу прогресса и evidence journal в `Plan.md`; после появления scripts используй `update_plan_status.py` и `check_plan.py`.

`AGENTS.md` обновляет исполнитель задачи, затронувшей его сведения. Любое самообновление обязано опираться на уже проверенное состояние репозитория; агент не меняет спецификацию, scope или безопасность «для удобства» без явного решения Кети.
