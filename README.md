# «Бодрые люди»

Приватный Telegram-агент Кети для evidence-first подготовки, утверждения и планирования постов канала «Бодрые люди».

## Текущее состояние

Реализация идёт по [Plan.md](Plan.md). Создан Python 3.12 application scaffold со строгой конфигурацией и автоматическими quality gates. Бот и worker появятся в следующих задачах плана.

## Локальная установка

```powershell
Copy-Item .env.example .env
python -m pip install --upgrade "pip>=25.1"
python -m pip install -e . --group dev
```

Заполните обязательные значения `TELEGRAM_BOT_TOKEN`, `TELEGRAM_OWNER_ID`, `TELEGRAM_CHANNEL_ID` и `GROQ_API_KEY` в локальном `.env`. Секреты нельзя коммитить или отправлять в Telegram.

## Локальная PostgreSQL

Для разработки schema поднимается только на loopback-интерфейсе; production-профиль появится отдельной задачей. Перед запуском задайте уникальный локальный пароль, затем примените migration:

```powershell
$env:POSTGRES_PASSWORD = "<local-password>"
docker compose up -d postgres
$env:DATABASE_URL = "postgresql+asyncpg://bodrye_bot:<url-encoded-local-password>@127.0.0.1:5432/bodrye_bot"
python -m alembic upgrade head
```

`DATABASE_URL` также должен быть задан в локальном `.env` для будущих bot/worker-процессов. PostgreSQL не публикуется наружу: compose привязывает его только к `127.0.0.1`.

## Проверки

```powershell
python scripts/check_plan.py Plan.md
python -m ruff check .
python -m mypy src evals
python -m pytest -v
```

## Проверка и обновление Plan.md

После свежих проверок задача или фаза закрывается одной из команд:

```powershell
python scripts/update_plan_status.py complete-task P0.T0 --evidence "unittest: 8 passed"
python scripts/update_plan_status.py complete-phase P0 --evidence "ruff, mypy, pytest and migration round trip: exit 0"
```

Updater отказывается перескакивать незавершённую задачу или gate. Он меняет `Plan.md` через атомарную замену файла и добавляет evidence journal row.

## Источники истины

- [Каноническая MVP-спецификация](docs/superpowers/specs/2026-08-28-bodrye-lyudi-mvp-design.md)
- [Детальный implementation plan](docs/superpowers/plans/2026-08-28-bodrye-lyudi-mvp.md)
- [Живой фазовый трекер](Plan.md)
- [Инструкции для агентов](AGENTS.md)
