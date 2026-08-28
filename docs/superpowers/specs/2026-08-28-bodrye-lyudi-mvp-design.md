# «Бодрые люди»: спецификация MVP редакционного ИИ-агента

Дата: 28 августа 2026 года
Статус: утверждённый дизайн для пользовательского ревью перед планированием реализации
Владелец продукта: Кети

## 1. Назначение и источник требований

Эта спецификация описывает первую рабочую версию личного Telegram-агента Кети для подготовки и публикации контента канала «Бодрые люди».

Документ реализует решения из:

1. `docs/discovery/2026-08-28-pre-spec-decisions.md`;
2. `idea.md`;
3. `research.md`;
4. Discovery Interview от 28 августа 2026 года.

Спецификация и план от 27 августа 2026 года устарели и не являются источниками требований. При расхождении этой спецификации с ними действует этот документ.

Нормативные слова `ДОЛЖЕН`, `НЕЛЬЗЯ`, `РАЗРЕШЕНО` обозначают обязательное поведение. Числовые пороги являются acceptance criteria, если явно не названы launch-гипотезами.

## 2. Резюме продукта

«Бодрые люди» — новый полезно-развлекательный Telegram-канал о здоровой жизни после 35 лет. Приватный агент работает только для Кети: собирает небольшой дайджест из разрешённых источников, помогает выбрать тему, извлекает проверяемые утверждения, показывает доказательства и ограничения, предлагает три подачи, создаёт пост в утверждённом голосе канала, принимает правки и публикует только конкретную утверждённую версию в выбранное время.

Агент не пишет от лица Кети, не изображает врача, не выдаёт знания модели за доказательства и не публикует автономно.

Главный Job-to-be-Done:

> За 20–30 минут выбрать одну тему, безопасно подготовить один пост, утвердить конкретную версию и поставить её в расписание.

## 3. Пользователь, аудитория и редакционный голос

### 3.1. Пользователь агента

В MVP существует один владелец — Кети. Доступ задаётся allowlist Telegram ID. Подписчики канала не взаимодействуют с ботом и не являются пользователями системы.

### 3.2. Аудитория канала

Ядро — работающие городские женщины и мужчины 35–50 лет. Аудитория 50+ вторична. Темы должны помогать читателю поддерживать энергию, сон, движение, питание и медицинскую грамотность без фанатизма, стыда и чудо-обещаний.

### 3.3. Голос канала

Канал — самостоятельный медиабренд, а не личный дневник.

Обязательные характеристики:

- полезно-развлекательный;
- умный, но не заумный;
- тёплый, но не фамильярный;
- доказательный, но без позиции врача;
- практичный и спокойный;
- уважительный к возрасту, телу и заболеваниям;
- преимущественно безличный, с обращением к читателю на «вы»;
- допускает редакционное «мы» только для реально выполненного действия.

Фразы `мы изучили` и `мы проверили` допустимы только при сохранённом provenance соответствующей операции. `Нам понравилось` допустимо как оценка идеи, формата или удобства. Нельзя писать `мы попробовали`, `мы почувствовали`, `нам помогло` без реального человеческого опыта.

Юмор адаптивный: в бытовой теме допустимы один-два оригинальных юмористических элемента; в рискованной медицинской теме юмор минимален. Запрещены эйджизм, body shaming, высмеивание болезни, копирование чужих шуток и маскировка медицинского совета шуткой.

## 4. Цели, метрики и границы успеха

### 4.1. Цели MVP

- Сократить медианное активное время Кети на утверждённый пост до 30 минут или меньше.
- Обеспечить целевой ритм до пяти качественных постов в рабочую неделю.
- Создать воспроизводимый голос нового канала без fine-tuning.
- Исключить публикацию неутверждённой версии.
- Сделать evidence, ограничения, версии, память и затраты наблюдаемыми.

### 4.2. Launch-гипотезы первых двух недель

- `card_acceptance_rate >= 60%`.
- `agent_originated_publish_rate >= 70%`.
- медиана активного времени на пост `<= 30 минут`.
- медицинских инцидентов `0`.
- подтверждённых дублей публикаций `0`.

Определения:

- `card_acceptance_rate = unique cards with develop_or_save / delivered cards`;
- `agent_originated_publish_rate = published posts linked to digest item or agent input / all posts published through bot`;
- активное время считается между событиями workflow; интервалы без действий более 10 минут исключаются;
- медицинский инцидент — публикация с блокирующим риском, незавершённым review, отсутствующим provenance или несовпадающим approved hash;
- дубль — лишнее Telegram-сообщение для одного `PublicationJob`.

### 4.3. Что не обещает MVP

- медицинскую безошибочность;
- рост подписчиков или охватов;
- публикацию ровно пяти постов независимо от качества;
- полное покрытие рынка или медицинской литературы;
- круглосуточную доступность внешних API.

## 5. Scope MVP

### 5.1. P0 — обязательное ядро

- доступ только allowlisted владельцу;
- onboarding и проверка подключения канала;
- калибровка голоса канала с нуля;
- реестр разрешённых источников;
- дайджест 3–5 карточек в рабочие дни;
- ручной ввод ссылки или текста;
- preview извлечения и подтверждение понимания;
- атомарные claims, evidence verdict и risk;
- три разные подачи;
- адаптивный выбор длины;
- один Telegram-пост и три заголовка;
- адаптивный публичный блок источников;
- неизменяемые версии и смысловая повторная проверка;
- явные approval и schedule;
- перенос и отмена;
- консервативная идемпотентная доставка;
- библиотека сохранённых тем и утверждённых материалов;
- управляемые правила стиля;
- удаление исходника и производных данных;
- журнал, расходы, backup и восстановление;
- заменяемые Groq и OpenAI adapters.

### 5.2. P1 — входит в релиз после P0-гейтов

- еженедельный обзор повторяющихся стилевых правок;
- карантин нестабильных источников;
- экспорт карточки материала с provenance;
- пользовательский экран состояния провайдеров, квот и backup;
- повторный model/style eval из Telegram-команды владельца.

P1 не расширяет форматы контента и не может ослаблять P0-гейты.

### 5.3. Вне MVP

- голосовой ввод и транскрипция;
- готовые изображения и видео;
- Stories, Reels и Instagram;
- афиша и события;
- реклама и партнёрские размещения;
- автоматический поиск мемов и UGC;
- комментарии, модерация и сообщество;
- аналитика роста аудитории;
- fine-tuning;
- автономные ИИ-агенты;
- userbot и массовый scraping;
- публикация без участия Кети;
- команда редакторов и роли экспертов.

## 6. Пользовательский интерфейс

### 6.1. Команды

| Команда | Назначение |
|---|---|
| `/start` | onboarding или главное меню |
| `/digest` | получить или повторно открыть текущий дайджест |
| `/new` | начать workflow из текста или ссылки |
| `/library` | сохранённые темы и утверждённые материалы |
| `/scheduled` | будущие публикации, перенос и отмена |
| `/style` | профиль, правила, калибровка и eval |
| `/sources` | белый список и состояние источников |
| `/status` | здоровье системы, очередь, провайдеры и backup |
| `/costs` | использование квот, токенов и затраты |
| `/delete` | управляемое удаление выбранного объекта |
| `/help` | краткая справка без технического жаргона |

Команды и callback должны проверять `owner_id` до чтения объекта.

### 6.2. Onboarding

Последовательность:

1. Проверить allowlisted Telegram ID.
2. Проверить `telegram_channel_id` и права отправки сообщений.
3. Показать тестовый preview без публикации.
4. Подтвердить `Europe/Moscow`.
5. Проверить Groq key и список доступных production-моделей.
6. Проверить реестр источников.
7. Провести калибровку стиля.
8. Показать итоговый readiness report.

Onboarding завершается только при успешных owner, database, Telegram и style gates. Недоступность Groq допускает завершить инфраструктурную часть, но блокирует активацию генерации.

### 6.3. Утренний дайджест

В рабочий день в 10:00 MSK worker формирует до 3–5 карточек. Если сильных тем меньше, квота не заполняется.

Карточка содержит:

- заголовок;
- суть в 2–3 предложениях;
- рубрику;
- дату;
- соответствие аудитории;
- первоисточник;
- предварительный риск;
- причину выбора;
- действия `Развить`, `Сохранить`, `Не интересно`, `Источник`.

Отбор использует отдельные признаки: соответствие аудитории, свежесть, практическая ценность и качество происхождения. Вес хранится в конфигурации и не является скрытым решением LLM.

### 6.4. Развитие темы

Канонический путь:

```text
источник
→ extraction preview
→ подтверждение понимания
→ claims review
→ три подачи
→ выбор подачи
→ выбор длины
→ черновик
→ правки
→ draft review
→ approval
→ schedule
→ publication
```

До генерации бот показывает подтверждённые тезисы, ограничения, запрещённые усиления вывода и рекомендуемый формат.

Форматы:

- `short`: до 1 000 знаков;
- `medium`: 1 200–2 200 знаков;
- `long`: 2 500–3 800 знаков с учётом заголовка и источников.

Один workflow создаёт один пост. Автоматического разбиения на серию сообщений нет.

## 7. Архитектура

### 7.1. Стиль системы

Модульный Python-монолит: один репозиторий, одно доменное ядро и несколько процессов с чёткими интерфейсами.

Технологии:

- Python 3.12+;
- aiogram 3.x;
- SQLAlchemy 2.x async;
- Alembic;
- PostgreSQL 16+;
- asyncpg;
- Pydantic 2 и pydantic-settings;
- httpx;
- structlog;
- pytest, pytest-asyncio, Ruff и mypy strict;
- Docker Compose.

### 7.2. Процессы

- `bot`: Telegram long polling и пользовательские команды;
- `worker`: ingestion, review, generation, scheduler и publication queue;
- `postgres`: данные и durable coordination;
- `backup`: ежедневный зашифрованный `pg_dump`;
- `healthcheck`: состояние процессов, базы, очереди, провайдеров и backup.

Long polling выбран для single-user VPS: публичный webhook и домен не обязательны. PostgreSQL и служебные endpoints не публикуются в интернет.

### 7.3. Слои

```text
Telegram adapters
    ↓
Application use cases
    ↓
Domain entities, policies and state machine
    ↓
Repository and provider ports
    ↓
PostgreSQL and external adapters
```

Доменный слой не импортирует aiogram, SQLAlchemy или SDK провайдера.

### 7.4. Модули

- `identity`: владелец и authorization;
- `sources`: allowlist, fetch и provenance;
- `digest`: ranking и deduplication;
- `editorial`: workflow, angles и drafts;
- `medical`: claims, evidence и risk policy;
- `style`: calibration, profile, rules и examples;
- `publication`: approval, schedule и delivery;
- `memory`: library, retention и deletion;
- `providers`: Groq, OpenAI, Telegram и backup adapters;
- `operations`: costs, audit, health и alerts.

Специализированные LLM-роли являются сервисами с типизированными входами, а не автономными агентами. Ни одна роль не имеет прямого доступа к публикации или постоянной памяти.

## 8. Состояния и переходы

### 8.1. WorkflowStatus

Спецификация уточняет неоднозначное повторное `review_passed` из pre-spec документа отдельными состояниями claims и draft:

```text
INGESTED
→ EXTRACTED
→ EXTRACTION_CONFIRMED
→ CLAIMS_REVIEW_PENDING
→ CLAIMS_REVIEW_PASSED | CLAIMS_REVIEW_BLOCKED
→ ANGLES_READY
→ ANGLE_SELECTED
→ DRAFT
→ DRAFT_REVIEW_PENDING
→ DRAFT_REVIEW_PASSED | DRAFT_REVIEW_BLOCKED
→ APPROVED
→ SCHEDULED
→ PROCESSING
→ PUBLISHED | FAILED | DELIVERY_UNKNOWN | CANCELLED
```

`REJECTED` разрешён из любого состояния до `PROCESSING`. Сохранение в библиотеку моделируется отдельным `LibraryItem`, а не состоянием публикации.

### 8.2. Разрешённые возвраты

- `EXTRACTED → INGESTED` после исправления исходного ввода;
- `CLAIMS_REVIEW_BLOCKED → EXTRACTION_CONFIRMED` после удаления или изменения claims;
- `DRAFT_REVIEW_BLOCKED → DRAFT` после правки;
- `DRAFT_REVIEW_PASSED → DRAFT` создаёт новую версию и аннулирует review;
- `APPROVED → DRAFT` создаёт новую версию и аннулирует approval;
- `FAILED → SCHEDULED` только для доказанно неотправленной попытки;
- `DELIVERY_UNKNOWN` разрешает только ручное `MARK_PUBLISHED` либо явно подтверждённый `RETRY`.

### 8.3. Инварианты

- переходы выполняет серверный доменный сервис;
- каждый переход записывает `AuditEvent`;
- approval привязан к `version_id` и `content_hash`;
- schedule доступен только из `APPROVED`;
- worker использует database lease;
- callback не может обойти policy;
- stale review никогда не считается пройденным.

## 9. Модель данных

### 9.1. Базовые требования

Все таблицы используют UUID, `owner_id`, `created_at` и при необходимости `updated_at`. Время хранится в UTC. Пользовательский ввод и расписание отображаются в `Europe/Moscow`.

### 9.2. Основные сущности

#### Source

`id`, `owner_id`, `name`, `canonical_url`, `source_type`, `roles[]`, `access_method`, `status`, `checked_at`, `failure_count`, `license_note`, `config_json`.

#### SourceDocument

`id`, `source_id`, `url`, `title`, `published_at`, `fetched_at`, `content_hash`, `bounded_excerpt`, `raw_expires_at`, `fetch_status`, `http_metadata`.

#### DigestItem

`id`, `source_document_id`, `topic_fingerprint`, `summary`, `rubric`, `audience_reason`, `selection_reason`, `preliminary_risk`, `score_components`, `digest_date`, `disposition`.

#### ContentWorkflow

`id`, `owner_id`, `origin_type`, `origin_id`, `status`, `selected_angle_id`, `recommended_format`, `current_version_id`, `version`.

#### Claim

`id`, `workflow_id`, `draft_version_id?`, `exact_text`, `claim_type`, `population`, `context`, `is_medical`, `status`.

#### Evidence

`id`, `claim_id`, `source_document_id`, `verdict`, `risk`, `exact_excerpt`, `excerpt_hash`, `applicability`, `limitations`, `reviewed_at`, `review_model_run_id`.

#### Angle

`id`, `workflow_id`, `angle_type`, `name`, `hook`, `promise`, `tone_note`, `selected_at`.

#### DraftVersion

`id`, `workflow_id`, `version_number`, `body`, `body_hash`, `format`, `headlines[]`, `public_sources[]`, `style_profile_version`, `created_by_run_id`, `supersedes_id`.

#### ReviewDecision

`id`, `draft_version_id`, `status`, `blocking_reasons[]`, `changed_claim_ids[]`, `reviewed_at`, `policy_version`.

#### Approval

`id`, `draft_version_id`, `content_hash`, `approved_by`, `approved_at`, `revoked_at`, `revoke_reason`.

#### PublicationJob

`id`, `draft_version_id`, `approval_id`, `scheduled_at_utc`, `status`, `idempotency_key`, `attempt_id`, `lease_until`, `telegram_message_id`, `safe_error_code`, `last_attempt_at`.

#### StyleProfile

`id`, `owner_id`, `version`, `status`, `activated_at`, `supersedes_id`.

#### StyleRule

`id`, `profile_id`, `scope`, `rule_text`, `positive_example`, `negative_example`, `origin`, `status`, `confirmed_at`.

#### StyleExample

`id`, `profile_id`, `draft_version_id?`, `text`, `rubric`, `format`, `tags[]`, `rating`, `is_holdout`.

#### Operational entities

`ProviderRun`, `CostEvent`, `AuditEvent`, `LibraryItem`, `DeletionTombstone`, `BackupRun`, `SourceHealthEvent`.

### 9.3. Ограничения базы

- unique `(workflow_id, version_number)`;
- unique active approval per workflow;
- unique `PublicationJob.idempotency_key`;
- unique non-null `telegram_message_id`;
- foreign keys не допускают orphan claims, reviews и approvals;
- owner consistency проверяется составными foreign keys либо repository policy;
- optimistic version предотвращает потерянные обновления;
- запрещён `APPROVED` без review текущего hash.

## 10. Система стиля

### 10.1. Калибровка с нуля

1. Создать 8–10 тестовых тем разных уровней риска.
2. Для каждой показать три коротких варианта подачи.
3. Сохранить выбор, отклонение и пользовательские правки.
4. Сформировать только `proposed` rules.
5. Получить отдельное подтверждение правил.
6. Создать три полных поста на holdout-темах.
7. Активировать профиль после style gate.

Gate:

- нет нарушения hard style rules;
- минимум 2 из 3 holdout-постов принимаются без полной переработки;
- медианная оценка Кети не ниже 4 из 5.

### 10.2. Сбор StyleContext

`StyleContextBuilder` передаёт модели:

- активные hard rules;
- правила выбранного формата;
- 3–5 утверждённых релевантных примеров;
- релевантные антипримеры;
- выбранную подачу;
- медицинские ограничения.

Вся история переписки не передаётся. В MVP примеры выбираются по тегам, рубрике и формату; embeddings не обязательны.

### 10.3. Обучение на правках

- явная команда `Запомни как правило` создаёт `proposed` rule;
- повторяющаяся закономерность предлагается после минимум трёх сходных случаев;
- еженедельный обзор не активирует правила автоматически;
- конфликт требует отдельного подтверждения;
- заменённое правило получает `superseded` и доступно для аудита и отката;
- отклонённый текст не становится правилом сам по себе.

### 10.4. Смена модели

При смене runtime-модели запускается полный style eval. Новая модель не участвует в реальных публикациях до прохождения gate. Профиль и примеры не мигрируют между провайдерами, потому что принадлежат приложению.

## 11. Источники и дайджест

### 11.1. Начальный реестр

- Рубрикатор клинических рекомендаций Минздрава РФ — ручной evidence search;
- WHO Fact Sheets — evidence/topic;
- WHO News Releases — topic с переходом к первичному документу;
- USPSTF — evidence с пометкой применимости к США;
- NICE — evidence с пометкой применимости к Великобритании;
- Cochrane Reviews — evidence при доступном тексте;
- PubMed RSS: движение/активное долголетие;
- PubMed RSS: сон/восстановление;
- PubMed RSS: питание/метаболическое здоровье;
- вручную утверждённые Telegram-источники — только topic/format/anti-example.

Точные RSS query являются версионируемой конфигурацией источника и утверждаются при onboarding. Их изменение не требует релиза кода, но записывается в audit.

### 11.2. Fetch policy

- только `http/https`;
- allowlist host для evidence sources;
- блокировка private, loopback, link-local и metadata IP;
- максимум 10 МБ ответа до декодирования;
- connect timeout 5 секунд, total timeout 20 секунд;
- максимум 3 redirect с повторной SSRF-проверкой;
- raw HTML хранится не более 24 часов;
- недоступный полный текст не реконструируется по знаниям модели.

### 11.3. Дедупликация

Сначала нормализуется canonical URL, затем используется content hash и topic fingerprint. Несколько публикаций об одной теме объединяются в одну карточку с несколькими provenance links.

## 12. Claims, evidence и риск

### 12.1. ClaimType

`effect`, `causal`, `association`, `risk`, `numeric`, `diagnosis`, `treatment`, `dosage`, `prevention`, `safety`.

### 12.2. EvidenceVerdict

- `supported`: прямое совпадение формулировки, популяции и контекста с актуальным авторитетным источником;
- `refuted`: актуальный авторитетный источник прямо противоречит claim;
- `insufficient`: данные ранние, косвенные, конфликтующие или неприменимые;
- `manual_required`: цена ошибки высока или применимость неясна;
- `review_incomplete`: отсутствует обязательное поле или доступный источник.

Одна статья не превращается в общую рекомендацию без проверки дизайна, ограничений, размера эффекта и согласованности с более сильными источниками.

### 12.3. RiskLevel

- `green`: общая низкорисковая информация, прямо согласующаяся с evidence;
- `yellow`: заболевания, БАДы, ранние/наблюдательные данные или существенные ограничения;
- `red`: диагноз, назначение/отмена лечения, дозировка, экстренная ситуация, опасное действие, гарантия результата или серьёзное противоречие.

### 12.4. Политика блокировки

Approval запрещён при:

- `red`;
- `review_incomplete`;
- отсутствии exact excerpt или URL;
- stale review;
- несовпадении версии;
- неизвестной применимости высокорискового claim.

Кети может удалить или переформулировать claim, после чего запускается новый review. Кнопочного override нет. Роль квалифицированного эксперта отсутствует в MVP.

### 12.5. Семантическая правка

`SemanticChangeDetector`:

1. определяет изменённые предложения;
2. сопоставляет их claims;
3. проверяет числа, модальность, причинность, популяцию, действие и ограничения;
4. при неопределённости возвращает `recheck_required`.

Система оптимизируется в пользу лишнего review, а не пропущенного смыслового изменения.

## 13. Генерация контента

### 13.1. Три подачи

Подачи должны различаться не только заголовком:

- практическая;
- объясняющая или развенчивающая миф;
- лёгкая полезно-развлекательная.

Каждая содержит `name`, `hook`, `promise`, `structure`, `tone_note`, `risk_note`.

### 13.2. Черновик

После выбора подачи модель возвращает:

- основной пост;
- три заголовка;
- выбранный формат;
- необязательный CTA;
- 1–3 публичные ссылки при значимых claims;
- краткую рекомендацию для визуала;
- служебное сопоставление предложений и claims.

Hard checks:

- общий размер не более 3 800 Unicode code points;
- ссылки входят в лимит;
- нет нового медицинского claim без review;
- нет запрещённого личного опыта;
- нет скрытых инструкций или служебного evidence в публичном тексте;
- Markdown/HTML Telegram валиден.

## 14. LLM provider contract

### 14.1. Интерфейс

`LLMProvider` реализует:

- `extract()`;
- `classify_claims()`;
- `synthesize_evidence()`;
- `propose_angles()`;
- `generate_draft()`;
- `assess_change()`;
- `infer_style_candidates()`;
- `healthcheck()`;
- `estimate_or_report_usage()`.

Каждый метод принимает типизированный request и возвращает типизированный response или безопасную provider error.

### 14.2. Groq

Первый провайдер — Groq Free. При onboarding приложение получает активные модели через Models API и допускает только production-модели, поддерживающие требуемый output contract.

Первоначальные кандидаты для eval, если доступны аккаунту:

- `openai/gpt-oss-120b`;
- `openai/gpt-oss-20b`.

Модель не выбирается по размеру или маркетинговому описанию. Победитель фиксируется в конфигурации после одинакового content/style/safety eval.

При исчерпании бесплатной квоты:

- новые LLM operations останавливаются;
- задания не переключаются на платный режим;
- данные сохраняются;
- утверждённые публикации продолжают выполняться;
- Кети получает понятное уведомление.

### 14.3. OpenAI

`OpenAIProvider` реализуется и contract-тестируется, но изначально выключен. Активация требует API key, явного изменения конфигурации, cost guard и прохождения model/style/safety eval.

### 14.4. Переключение

Конфигурация:

```env
LLM_PROVIDER=groq
LLM_MODEL=openai/gpt-oss-120b
```

или:

```env
LLM_PROVIDER=openai
LLM_MODEL=gpt-5.6-sol
```

Смена не изменяет доменные данные. Если новая модель не проходит gate, deployment возвращает прежнюю конфигурацию.

### 14.5. Надёжность вызовов

- connect timeout 5 секунд;
- total timeout 60 секунд;
- максимум 2 retry для доказанно безопасных 429/5xx с jitter;
- schema-invalid output валидируется приложением и допускает одну repair-попытку;
- medical uncertainty не repair-ится в уверенный ответ, а блокируется;
- provider response, latency, tokens и error class записываются без секретов и лишнего контента.

## 15. Approval, schedule и доставка

### 15.1. Approval

`Утвердить версию` создаёт `Approval` для `version_id + content_hash`. Оно не назначает время и не отправляет сообщение.

### 15.2. Schedule

`Запланировать` показывает финальный preview, дату и время MSK. На сервере время преобразуется в UTC. Прошедшее или неоднозначное локальное время отклоняется.

### 15.3. Claiming job

Worker выбирает due job через `SELECT ... FOR UPDATE SKIP LOCKED`, записывает lease и `attempt_id`, затем проверяет approval/hash непосредственно перед отправкой.

### 15.4. Консервативная доставка

Telegram Bot API не предоставляет транзакцию вместе с PostgreSQL, поэтому абсолютное exactly-once невозможно гарантировать при падении между отправкой и сохранением результата.

Политика:

- доказанно неотправленная ошибка может retry;
- после успешного ответа сохраняется `telegram_message_id`;
- timeout или crash с неизвестным исходом переводит job в `DELIVERY_UNKNOWN`;
- `DELIVERY_UNKNOWN` не retry-ится автоматически;
- Кети проверяет канал и выбирает `Отметить опубликованным` либо `Повторить` с явным предупреждением о риске дубля.

Цель — 0 подтверждённых дублей за счёт отказа от опасного автоматического retry.

## 16. Безопасность и приватность

### 16.1. Identity и authorization

- allowlist одного Telegram ID;
- owner check до загрузки объекта;
- callback содержит opaque ID, а не доверенный owner/status;
- повторная server-side проверка перехода;
- неизвестному пользователю возвращается нейтральный отказ без раскрытия данных.

### 16.2. Сервер

- Ubuntu 24.04 LTS;
- непривилегированный deploy user;
- SSH только по ключу;
- password и root login отключены;
- firewall;
- PostgreSQL слушает только Docker/private interface;
- регулярные security updates;
- контейнеры без privileged mode;
- read-only filesystem там, где возможно;
- resource limits и log rotation.

### 16.3. Secrets

Telegram token, provider keys, database password, backup credentials и encryption key:

- отсутствуют в Git;
- хранятся в secrets/environment с минимальными правами;
- маскируются в логах;
- ротируются независимо;
- не показываются в Telegram.

### 16.4. Недоверенный контент

- source content отделяется от system instructions;
- инструкции внутри источника никогда не исполняются;
- LLM output считается недоверенным;
- обязательна schema и domain validation;
- URL проходят SSRF policy;
- HTML очищается;
- неизвестные enum и дополнительные поля отклоняются.

### 16.5. Чувствительный ввод

Продукт не предназначен для медицинских карт, анализов, диагнозов и данных подписчиков. При обнаружении потенциально личного медицинского материала исходник не записывается постоянно до отдельного `Сохранить несмотря на предупреждение`. Отмена удаляет transient payload.

## 17. Хранение и удаление

| Класс | Retention |
|---|---|
| raw HTML и response cache | 24 часа |
| bounded evidence excerpt и metadata | пока существует claim |
| rejected draft | 90 дней |
| approved/published material | до явного удаления |
| operational logs без полного контента | 30 дней |
| backups | 30 дней |
| derived index | до удаления исходника, live deletion <=24 часов |

Удаление создаёт `DeletionTombstone`, каскадно удаляет summaries, excerpts без иных claims, style links, derived index и cache. Backup restore обязан повторно применить tombstones до открытия системы пользователю.

## 18. Ошибки и пользовательские сообщения

Минимальные safe codes:

- `owner_forbidden`;
- `source_unavailable`;
- `source_blocked`;
- `extraction_failed`;
- `llm_timeout`;
- `llm_rate_limit`;
- `llm_quota_exhausted`;
- `llm_unavailable`;
- `llm_invalid_output`;
- `medical_review_incomplete`;
- `style_profile_not_ready`;
- `approval_stale`;
- `publication_failed`;
- `delivery_unknown`;
- `backup_stale`;
- `internal_error`.

Пользовательское сообщение содержит: что произошло, что сохранено, допустимое следующее действие и `trace_id`. Stack trace, token, URL с секретом и полный prompt пользователю не показываются.

## 19. Развёртывание и операции

### 19.1. Production profile

- Beget VPS в доступном регионе после однодневного smoke-test исходящих запросов к Telegram, Groq и источникам;
- 2 CPU / 4 ГБ RAM / 40 ГБ NVMe;
- public IPv4;
- ориентир 33 ₽/день + 5 ₽/день IPv4;
- Docker Compose;
- app и PostgreSQL на одном VPS для single-user MVP.

GPU, Kubernetes, n8n, control panel и отдельный DBaaS не используются.

### 19.2. Backup

- ежедневный encrypted `pg_dump`;
- отдельное S3-compatible storage в Beget Cloud;
- server backup Beget — дополнительный уровень, а не замена database backup;
- retention 30 дней;
- ежемесячное восстановление в отдельную `_restore_test` database;
- RPO 24 часа;
- RTO 4 часа.

### 19.3. Release

1. Build immutable image.
2. Запустить unit, integration, contract, security и migration tests.
3. Создать backup.
4. Применить migration.
5. Запустить контейнеры и health gate.
6. Выполнить Telegram/Groq/source smoke tests.
7. При неуспехе вернуть image и использовать согласованный rollback/forward-fix migration path.

## 20. Бюджет и наблюдаемость

### 20.1. Бюджет

- Beget VPS: ориентировочно 1 140–1 178 ₽/месяц;
- Groq: бесплатная квота на старте;
- soft limit всех операционных расходов, включая VPS, storage и API: 3 500 ₽/месяц;
- hard limit всех операционных расходов: 5 000 ₽/месяц;
- платный fallback без подтверждения запрещён.

Неизвестная стоимость не считается нулевой. При расчёте порога учитывается фиксированная стоимость VPS. Сервер не выключается при hard limit; блокируются новые платные AI/search operations.

### 20.2. Метрики

- provider requests, tokens, latency, error class;
- estimated/actual cost per operation и workflow;
- digest delivery time;
- source success/failure/quarantine;
- queue age;
- publication latency и delivery state;
- duplicate incidents;
- style ratings и edit distance proxy;
- claim verdict/risk counts;
- backup age и restore result;
- CPU, RAM и disk.

### 20.3. Alerts

Кети получает уведомление при:

- исчерпании Groq quota;
- 80% hard limit;
- трёх сбоях источника;
- `DELIVERY_UNKNOWN`;
- просроченной очереди;
- backup старше 26 часов;
- disk >80%;
- невозможности пройти health gate после релиза.

## 21. Нефункциональные требования

- digest готов к 10:05 MSK не менее чем в 95% рабочих дней после стабилизации;
- неполный digest и список ошибок приходят до 10:10;
- 99% доступных publication jobs начинают попытку в пределах 60 секунд;
- при доступном Telegram успешная доставка завершается в пределах 5 минут;
- подтверждённых автоматических дублей — 0;
- обычная Telegram-команда без LLM отвечает p95 <=2 секунд;
- все доменные операции идемпотентны либо имеют документированное ambiguous state;
- система выдерживает рестарт каждого процесса без потери утверждённых данных;
- русский язык используется для интерфейса и safe errors;
- код проходит Ruff, mypy strict и тесты.

## 22. Тестовая стратегия

### 22.1. Автоматические тесты

- unit: domain policies, state transitions, ranking, retention;
- property: запрещённые переходы, hashes, idempotency;
- PostgreSQL integration: constraints, leases, ownership, migrations;
- provider contract: одинаковая нормализованная семантика Groq/OpenAI;
- schema: malformed, extra fields, wrong enum, refusal;
- security: callback ownership, SSRF, redirect, prompt injection, secret redaction;
- publication: definite failure, success, crash, timeout и `DELIVERY_UNKNOWN`;
- deletion: cascade и tombstone replay;
- backup: restore в `_restore_test`;
- Telegram e2e: отдельный bot token и test channel.

### 22.2. Content eval

Единый versioned dataset содержит:

- 8–10 calibration topics;
- 3 style holdouts;
- supported/refuted/insufficient/manual claims;
- числовые, causal и association traps;
- source unavailable;
- prompt-injected source;
- смысловые правки чисел, модальности, популяции и действия;
- русские useful-entertainment темы разной длины.

Model candidate сравнивается слепо. Отчёт сохраняет model ID, provider, prompt version, schema version, user rating, safety violations, latency и tokens.

### 22.3. Release gates

- 100% hard safety fixtures проходят;
- 0 unauthorized reads/writes;
- 0 публикаций без matching approval;
- 0 auto-retry из `DELIVERY_UNKNOWN`;
- 100% required schemas валидны;
- style gate пройден;
- restore test пройден;
- smoke tests production endpoints пройдены.

## 23. Acceptance criteria

| ID | Проверяемый результат |
|---|---|
| AC-01 | Неизвестный Telegram ID не может читать или менять данные |
| AC-02 | Onboarding проверяет канал, БД, провайдера, источники и стиль |
| AC-03 | Дайджест содержит не более 5 сильных карточек и не заполняет квоту слабыми |
| AC-04 | Недоступный источник не пересказывается моделью как прочитанный |
| AC-05 | Генерация начинается только после подтверждения extraction |
| AC-06 | До черновика показаны claims, evidence, ограничения и три подачи |
| AC-07 | `red`, incomplete или missing provenance блокируют approval |
| AC-08 | Смысловая правка создаёт версию и аннулирует review/approval |
| AC-09 | Approval фиксирует hash и ничего не публикует |
| AC-10 | Schedule доступен только matching approved version |
| AC-11 | Ambiguous Telegram delivery не retry-ится автоматически |
| AC-12 | Правило стиля не активируется без решения Кети |
| AC-13 | Новый provider проходит model/style/safety eval до активации |
| AC-14 | Groq quota exhaustion не включает платный fallback и не ломает scheduled jobs |
| AC-15 | Удаление очищает live derivatives и создаёт tombstone |
| AC-16 | Daily encrypted backup и monthly restore доказаны журналом |
| AC-17 | Каждая safe error содержит русское объяснение и trace ID |
| AC-18 | Costs/tokens наблюдаемы по операции и workflow |
| AC-19 | Публичный пост не выдаёт выдуманный опыт за редакционный |
| AC-20 | Старый voice/Stories/events/ads scope отсутствует в коде MVP |

## 24. Порядок реализации

Эта последовательность является зависимостью спецификации, но не заменяет подробный implementation plan:

1. Репозиторий, CI, конфигурация и threat-model fixtures.
2. Доменная state machine и PostgreSQL constraints.
3. Identity, Telegram shell и safe errors.
4. Provider contract, Groq adapter и eval harness.
5. Style calibration и memory.
6. Source registry, fetch и digest.
7. Claims/evidence/risk gate.
8. Angles, adaptive draft и editing.
9. Approval, scheduler и conservative publication.
10. Deletion, costs, backup, monitoring и Beget deployment.
11. End-to-end acceptance и двухнедельный pilot.

Нельзя начинать следующий блок, если обязательные gates предыдущего блока не прошли.

## 25. Входные данные владельца перед production

Это операционные значения, а не открытые продуктовые вопросы:

- Telegram bot token;
- Telegram owner ID;
- Telegram channel ID;
- Groq API key;
- Beget account/VPS access;
- S3 backup credentials;
- окончательно выбранная Groq model после eval;
- подтверждённые PubMed RSS queries;
- первый allowlist источников;
- утверждённый StyleProfile после calibration.

Секреты передаются только через защищённый канал настройки и никогда не добавляются в репозиторий или Telegram-сообщение.

## 26. Проверенные внешние основания

- [Telegram Bot API](https://core.telegram.org/bots/api) — update/delivery interface и ограничения Bot API;
- [Telegram updates](https://core.telegram.org/api/updates) — различие bot/user channel access;
- [PubMed RSS](https://pubmed.ncbi.nlm.nih.gov/help/#creating-an-rss-feed) — официальный RSS для saved search;
- [Groq models](https://console.groq.com/docs/models) — доступные production models;
- [Groq structured outputs](https://console.groq.com/docs/structured-outputs) — JSON Schema modes;
- [Groq API](https://console.groq.com/docs/overview) — OpenAI-compatible endpoint;
- [OpenAI GPT-5.6 Sol](https://developers.openai.com/api/docs/models/gpt-5.6-sol) — Responses API и structured outputs;
- [Beget VPS](https://beget.com/ru/vps) — конфигурации, IPv4, Docker и backup;
- [Beget backup](https://beget.com/ru/kb/manual/rezervnoe-kopirovanie-vps) — файловое восстановление;
- [WHO Fact Sheets](https://www.who.int/news-room/fact-sheets);
- [USPSTF recommendations](https://www.uspreventiveservicestaskforce.org/uspstf/recommendation-topics).

Внешние цены, модели, квоты и условия доступа проверяются повторно непосредственно перед deployment. Изменение внешнего тарифа не изменяет продуктовые гейты этой спецификации.
