# «Бодрые люди»

Приватный Telegram-агент Кети для evidence-first подготовки, утверждения и планирования постов канала «Бодрые люди».

## Текущее состояние

Реализация идёт по [Plan.md](Plan.md). Полный medical/evidence конвейер в коде есть, но **первый рабочий контур** другой: Кети пишет `/draft тема`, сама проверяет факты и публикует через `/reviewed` и `/publish` (см. [решение от 4 сентября 2026](docs/discovery/2026-09-04-manual-medical-review.md)). Автоматический medical gate не блокирует выпуск: тему в дайджесте можно развивать, рекламировать лекарства канал не должен. P6/P7 пока не делаем.

Запуск бота (одно окно PowerShell, его нельзя закрывать):

```powershell
python -m bodrye_bot.main_bot
```

В том же процессе раз в минуту проверяется дайджест. В **будний день после 10:00 по Москве** бот сам пишет Кети в личку карточки из разрешённых PubMed RSS. Команда `/digest` присылает дайджест сразу, в том числе в выходные. Компьютер должен быть включён и процесс запущен, иначе 10:00 пропускается.

Окно не закрывайте. Не запускайте одновременно `python -m bodrye_bot.main_worker`: это второй цикл дайджеста и риск двух одинаковых сообщений.

Команду `python -m bodrye_bot.main_bot` вставляйте только в PowerShell, не в Telegram.

Внизу экрана reply-меню: «Темы», «Пост», «Я проверила», «В канал», «Помощь». У карточек дайджеста — кнопки «Развить», «Сохранить», «Не интересно», «Источник». «Развить» даёт короткий черновик и обложку. После черновика — «Доработать», «Новый текст», «Я проверила», «В канал».

Первый `/start` показывает онбординг из трёх сообщений (что умеет бот, как загрузить тон командой `/settov`, быстрый старт). Повторный `/start` открывает меню. Команда `/help` и кнопка «Помощь» снова показывают инструкцию.

`/settov` принимает примеры постов текстом или голосом; «готово» сохраняет тон. Черновик и отметка «дайджест уже ушёл сегодня» живут в памяти процесса: после перезапуска бота незавершённый `/draft` нужно сделать заново, а `/digest` можно отправить ещё раз. Флаг «онбординг уже показан» и примеры тона пишутся в `data/owner_guide.json`.

## Локальная установка

```powershell
Copy-Item .env.example .env
python -m pip install --upgrade "pip>=25.1"
python -m pip install -e . --group dev
```

Заполните обязательные значения `TELEGRAM_BOT_TOKEN`, `TELEGRAM_OWNER_ID`, `TELEGRAM_CHANNEL_ID` и `GROQ_API_KEY` в локальном `.env`. Для расшифровки голосовых добавьте `DEEPGRAM_API_KEY` и перезапустите бота. Секреты нельзя коммитить или присылать в Telegram.

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

Кандидат модели проверяется воспроизводимым офлайн-набором без сетевых вызовов:

```powershell
python -m evals.run --provider fake --dataset evals/dataset.jsonl --output .artifacts/eval-fake.json
```

Команда возвращает код `0` только для проходящего отчёта. Сгенерированные файлы `.artifacts/` локальны и не коммитятся.

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
