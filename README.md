# «Бодрые люди»

Приватный Telegram-агент Кети для evidence-first подготовки, утверждения и планирования постов канала «Бодрые люди».

## Текущее состояние

Реализация идёт по [Plan.md](Plan.md). На текущем шаге доступны только валидация и атомарное обновление живого плана; application scaffold появится в P0.T1.

## Проверка и обновление Plan.md

```powershell
python scripts/check_plan.py Plan.md
python -m unittest discover -s tests/unit/scripts -p "test_*.py" -v
```

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
