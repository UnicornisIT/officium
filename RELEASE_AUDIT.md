# Officium Release Audit

## Дата проверки

30 августа 2026 года.

## Проверенный commit

Исходный commit, с которого началась проверка:
`0d2e7939b1e38fed9b32fdabbeb96c23fccf3dd5` (`master`).

Отчёт относится к этому commit вместе с незакоммиченными исправлениями,
перечисленными ниже.

## Итог

**READY WITH WARNINGS**

Код, SQLite-схема, финансовая математика, основные маршруты и локальный интерфейс
готовы к выпуску. Перед открытием production-доступа обязательны резервная копия,
установка обновлённых зависимостей, миграция `20260829_integrity`, безопасная
production-конфигурация и smoke-проверка на реальном MySQL/MariaDB. Живой
MySQL/MariaDB-сервер и реальные Telegram/Google endpoints в текущей среде
недоступны, поэтому эти интеграционные проверки нельзя считать выполненными.

Найдено и исправлено 29 сгруппированных проблем:

- CRITICAL: 3;
- HIGH: 9;
- MEDIUM: 13;
- LOW: 4;
- исправлено: 29;
- открытых исправимых дефектов в проверенном локальном контуре: 0.

## Выполненные проверки

- изучены структура проекта, фабрика Flask-приложения, модели, маршруты, шаблоны,
  сервисы, миграции, конфигурация, зависимости и документация;
- зафиксирован исходный Git commit и исходный результат тестов: 128/128;
- выполнен полный поиск TODO/FIXME, legacy-файлов, отладочных артефактов и
  потенциально опасных обработчиков исключений;
- проверены денежные поля, границы сумм, округление до копеек и использование
  `Decimal` в расчётном контуре;
- проверены проценты с переходом календарного года, високосным годом,
  фиксированной базой 365/366, сменой ставки и включением дня платежа;
- проверены обычные, частичные, просроченные, досрочные и комбинированные платежи,
  изменение старого платежа, закрытие долга и переплата;
- проверены финансовая сводка, ипотечная сводка, финансовый план, цели и
  финансовая подушка;
- проверены регулярные расходы, повторный запуск генератора и защита от
  конкурентного дубликата;
- проверен импорт CSV/XLSX/PDF, предпросмотр, подтверждение, сопоставление
  регулярных расходов и ограничения размера/сложности файла;
- проверены авторизация Telegram Login, Telegram Mini App, Google OAuth,
  emergency admin, dev/test-входы, блокировка пользователей и регистрационный
  флаг;
- проверены Telegram webhook secret, дедупликация `update_id`, приватные чаты,
  быстрые команды и обязательное подтверждение записи;
- проверены CSRF, IDOR в пользовательских запросах, CSV formula injection,
  безопасные ответы 400/403/404/500, cookie и production fail-fast;
- выполнен `pip check` — конфликтов зависимостей нет;
- выполнен `pip-audit --local --progress-spinner off` — известных уязвимостей в
  установленном окружении не найдено;
- выполнен `compileall` для приложения, конфигурации и тестов;
- выполнено полное применение Alembic на новой SQLite-базе;
- проверены `flask db current`, `flask db heads` и `flask db check`;
- миграция применена к копии существующей SQLite-базы: до и после осталось
  users=2, debts=4, payments=13, incomes=20, expenses=125; `integrity_check=ok`,
  `foreign_key_check` пуст;
- модельная MySQL DDL успешно компилируется SQLAlchemy, URL с особыми символами
  в логине/пароле корректно кодируется;
- вручную в локальном браузере проверены вход, дашборд, долги, доходы, расходы,
  ипотека, аналитика, финансовый план, архив, документация и все основные
  разделы админки;
- интерфейс проверен на desktop и viewport 390×844; найденная горизонтальная
  прокрутка исправлена и повторно проверена;
- проверены форма долга, клиентская валидация и фильтр типов долгов;
- ошибок и предупреждений в консоли браузера после прохода не обнаружено;
- выполнены `git diff --check`, просмотр `git status`, `git diff --stat` и
  содержательная ревизия diff.

Не выполнялись и не заявляются как выполненные: миграция на живом MySQL/MariaDB,
реальный Telegram webhook, реальный Google OAuth callback, нагрузочный тест,
проверка конкретного reverse proxy/system service и запуск root-owned updater на
живом Linux-сервере.

## Результаты тестирования

Финальный результат:

- всего: 168;
- успешно: 168;
- ошибок: 0;
- падений: 0;
- пропущено: 0;
- новых тестов: 40;
- время финального полного прогона: 13.123 s.

Основная команда:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -v
```

Дополнительные команды:

```powershell
.\.venv\Scripts\python.exe -m compileall -q app tests config.py run.py
.\.venv\Scripts\python.exe -m pip check
.\.venv\Scripts\pip-audit.exe --local --progress-spinner off
$env:FLASK_APP = 'run.py'
.\.venv\Scripts\python.exe -m flask db upgrade
.\.venv\Scripts\python.exe -m flask db current
.\.venv\Scripts\python.exe -m flask db heads
.\.venv\Scripts\python.exe -m flask db check
```

Исходный полный набор содержал 128 тестов и проходил. Добавлено 8 тестов
финансовой целостности, 13 security/regression-тестов, 2 теста MySQL-совместимости,
1 тест запрета регистрации через Telegram Mini App, 15 тестов обновления сервера
и 1 deploy contract test точного release tag/backup.

## Финансовая логика

### Процентные ставки и начисление процентов

Расчёт периода централизован в `debt_math_service`. Период делится одновременно
по границе календарного года и по дате изменения ставки. Для `actual_year`
используется 365 дней в обычном году и 366 в високосном; режимы `fixed_365` и
`fixed_366` сохраняют фиксированную базу. Флаг `include_payment_day` добавляет
день платежа только один раз. Каждый сегмент и итог округляются `ROUND_HALF_UP`
до `0.01`.

Контрольные примеры:

- 2024 год проверен как високосный с делителем 366;
- период через 31.12/01.01 разбивается по двум годовым базам;
- период со сменой ставки разбивается точно в дату изменения;
- фиксированные базы 365/366 дают ожидаемо разные результаты;
- включение дня платежа меняет число процентных дней на один;
- график платежей и расчёт просрочки используют одну формулу.

### Платежи и остатки

Сервис платежей теперь проверяет конечность и знак суммы, максимальное значение,
закрытый долг, переплату и согласованность `principal + interest + fee == total`
до копейки. Остаток уменьшается только на principal. После изменения старого
платежа последующие остатки пересчитываются. Два частичных просроченных платежа
не начисляют проценты повторно за уже учтённый период.

Проверены обычный обязательный платёж, частичный платёж, просрочка, досрочный
платёж, комбинированный обязательный+досрочный платёж, ручная банковская разбивка,
первый нестандартный платёж, переплата и платёж по закрытому долгу.

### Decimal и округление

Ввод нормализуется через `Decimal`, не принимает `NaN`/`Infinity`, отрицательные
значения и суммы более `9 999 999 999.99`, после чего округляется
`ROUND_HALF_UP`. Денежные суммы финансовой и ипотечной сводок суммируются как
`Decimal`. Преобразование в `float` оставлено только на границе отображения/JSON,
где оно не участвует в последующих денежных расчётах.

### Финансовая сводка

Проверены доходы, расходы, платежи, свободный остаток, категории, крупнейшие
траты, регулярные расходы, ипотечный остаток и проценты. Для ипотечных процентов
убрана приблизительная формула «годовая ставка / 12»; используется тот же
дневной алгоритм, что и в графике долга.

## Найденные ошибки

### ISSUE-001 — повторное начисление процентов при двух частичных просроченных платежах

Severity: **CRITICAL**

Проблема: второй частичный платёж мог снова начислить проценты за уже обработанный
период и исказить остаток.

Причина: начальная точка и погашенные проценты восстанавливались без единой
хронологии principal/interest.

Исправление: восстановлена последовательная временная шкала платежей, баланса и
уже уплаченных процентов.

Файлы: `app/services/payment_service.py`, `app/services/debt_math_service.py`.

Добавленный regression test: `test_two_partial_late_payments_do_not_charge_same_interest_twice`.

### ISSUE-002 — отсутствовал fail-safe production-контур

Severity: **CRITICAL**

Проблема: production мог стартовать со слабым секретом, debug/dev/test-входами и
небезопасной cookie; debug также не должен обходить проверку.

Причина: обязательная проверка production-настроек отсутствовала.

Исправление: добавлена строгая валидация `OFFICIUM_ENV=production`, запрет debug,
dev/test-входов, требование secure cookie и обязательных секретов.

Файлы: `app/__init__.py`, `config.py`, `.env.example`.

Добавленные regression tests: `test_production_rejects_placeholder_secret`,
`test_production_rejects_development_features`,
`test_production_requires_secure_session_cookie`.

### ISSUE-003 — критически уязвимая версия Authlib

Severity: **CRITICAL**

Проблема: `Authlib==1.3.0` входил в диапазон критической OAuth/OIDC-уязвимости
GHSA-wvwj-cvrp-7pv5.

Причина: зависимость давно не обновлялась.

Исправление: Authlib обновлён до 1.7.2; повторный `pip-audit` не нашёл известных
уязвимостей.

Файлы: `requirements.txt`.

Добавленный regression test: автоматический dependency audit (`pip-audit`).

### ISSUE-004 — сервис платежей допускал некорректные финансовые состояния

Severity: **HIGH**

Проблема: отсутствовала единая защита от non-finite значений, отрицательной суммы,
переплаты, платежа по закрытому долгу и неточной ручной разбивки.

Причина: проверки были распределены между маршрутом и отдельными ветками сервиса.

Исправление: инварианты перенесены в сервис и применяются к созданию и изменению
платежа.

Файлы: `app/services/payment_service.py`, `app/routes/payments.py`.

Добавленные regression tests: `test_explicit_breakdown_must_match_to_the_cent`,
`test_overpayment_and_payment_of_closed_debt_are_rejected`,
`test_service_rejects_non_finite_payment`.

### ISSUE-005 — быстрые Telegram-команды записывали данные до подтверждения

Severity: **HIGH**

Проблема: поведение расходилось с README и создавало финансовую запись сразу
после распознавания текста.

Причина: быстрые ветки вызывали create-функции напрямую.

Исправление: расход, доход, долг и платёж сначала сохраняются во временном
conversation state и создаются только callback-подтверждением.

Файлы: `app/services/telegram_bot_service.py`, `tests/test_telegram_bot.py`.

Добавленный regression test: обновлённые quick-command/webhook тесты проверяют
отсутствие записи до подтверждения и дедупликацию после него.

### ISSUE-006 — регистрационный флаг обходился отдельными каналами входа

Severity: **HIGH**

Проблема: новый пользователь мог быть создан через Mini App или Google OAuth при
закрытой регистрации.

Причина: `registration_enabled` применялся не ко всем auth flows.

Исправление: флаг применяется к Telegram, Mini App и Google; административный
Telegram ID сохраняет контролируемую возможность bootstrap.

Файлы: `app/routes/auth.py`, `tests/test_telegram_mini_app.py`.

Добавленный regression test: `test_new_user_is_rejected_when_registration_is_disabled`.

### ISSUE-007 — поддельная legacy test-сессия могла включить тестовый доступ

Severity: **HIGH**

Проблема: идентификатор `test-user` принимался загрузчиком без повторной проверки
feature flag.

Причина: доверие к старому значению `_user_id` в подписанной сессии.

Исправление: загрузчик возвращает пользователя только при явном
`TEST_USER_ENABLED=true`.

Файлы: `app/routes/auth.py`, `tests/test_security.py`.

Добавленный regression test: `test_forged_legacy_test_session_is_rejected_when_feature_is_disabled`.

### ISSUE-008 — импорт выписок был уязвим к ресурсоёмким файлам

Severity: **HIGH**

Проблема: большой PDF или XLSX/ZIP-bomb мог занять чрезмерно много памяти/CPU.

Причина: не было верхних границ размера, количества ZIP members, распакованного
объёма, compression ratio и страниц PDF.

Исправление: добавлены лимиты 10 MB, 500 members, 50 MB распакованных данных,
проверка ratio и максимум 200 PDF-страниц; архив закрывается гарантированно.

Файлы: `config.py`, `app/services/bank_statement_import_service.py`.

Добавленный regression test: существующий полный набор CSV/XLSX/PDF повторно
пройден после введения ограничений; лимиты дополнительно проверены код-ревью.

### ISSUE-009 — конкурентная генерация регулярных расходов могла создать дубликат

Severity: **HIGH**

Проблема: проверка «найти, затем вставить» не защищала от двух процессов.

Причина: не было уникального ограничения уровня БД.

Исправление: добавлен уникальный индекс
`(user_id, monthly_group_id, generated_for_month)` и безопасная обработка
`IntegrityError`.

Файлы: `app/models.py`, `app/services/monthly_expenses_service.py`,
`migrations/versions/20260829_schema_integrity.py`.

Добавленный regression test: существующий idempotency-набор и schema contract.

### ISSUE-010 — схема не обеспечивала заявленную ссылочную целостность

Severity: **HIGH**

Проблема: отсутствовал self-FK `expenses.generated_from_id`, а несколько
`ondelete` в моделях не совпадали с реальной схемой.

Причина: модель развивалась быстрее миграций.

Исправление: FK и каскадные правила выровнены, SQLite использует безопасный batch
recreate; выполнена проверка копии существующей БД.

Файлы: `app/models.py`, `migrations/versions/20260829_schema_integrity.py`.

Добавленный regression test: `tests/test_schema_contract.py` и полный migration
smoke на чистой/существующей SQLite-базе.

### ISSUE-011 — Telegram production-секреты проверялись недостаточно строго

Severity: **HIGH**

Проблема: webhook secret сравнивался обычным оператором, а production мог
стартовать с включённым Telegram без token/webhook secret.

Причина: отсутствовали constant-time comparison и общий fail-fast.

Исправление: используется `secrets.compare_digest`; token обязателен для любой
Telegram-функции, webhook secret — для включённого бота.

Файлы: `app/routes/telegram_bot.py`, `app/__init__.py`, `config.py`.

Добавленные regression tests: webhook tests и
`test_production_requires_token_for_telegram_features`,
`test_production_requires_webhook_secret_for_enabled_bot`.

### ISSUE-012 — уязвимая версия pypdf

Severity: **HIGH**

Проблема: `pypdf==4.3.1` входил в уязвимый диапазон GHSA-fp3f-mc75-235c.

Причина: устаревший pin.

Исправление: pypdf обновлён до 6.16.2, импорт PDF повторно протестирован.

Файлы: `requirements.txt`.

Добавленный regression test: полный PDF import suite и `pip-audit`.

### ISSUE-013 — процентные периоды не имели единого календарного алгоритма

Severity: **MEDIUM**

Проблема: разные места могли по-разному учитывать високосный год, смену ставки и
день платежа.

Причина: дублирование формул.

Исправление: добавлен единый сегментный расчёт процентов.

Файлы: `app/services/debt_math_service.py`,
`app/services/debt_interest_service.py`, `app/services/debt_schedule_service.py`.

Добавленные regression tests: четыре теста `DebtMathIntegrityTestCase`.

### ISSUE-014 — денежные сводки использовали float и приближённый месячный процент

Severity: **MEDIUM**

Проблема: суммы могли накапливать двоичную погрешность; ипотечный процент
оценивался как ставка/12.

Причина: раннее преобразование Numeric в float.

Исправление: суммы переведены на `Decimal`, ипотечный процент — на ежедневный
алгоритм.

Файлы: `app/services/finance_summary_service.py`, `app/routes/main.py`.

Добавленный regression test: финансовый integrity/summary suite.

### ISSUE-015 — MySQL URL ломался на специальных символах credentials

Severity: **MEDIUM**

Проблема: `@`, `:`, `/` и другие символы меняли структуру connection URL; CLI
helper обращался к Flask config как к объекту с атрибутами.

Причина: значения не кодировались и использовался неверный API config.

Исправление: применён `quote_plus`, helper работает с mapping interface.

Файлы: `config.py`, `app/__init__.py`.

Добавленный regression test: `test_mysql_url_escapes_credentials`.

### ISSUE-016 — Alembic видел ложный drift и отсутствовали индексы основных запросов

Severity: **MEDIUM**

Проблема: `flask db check` предлагал лишние SQLite Enum-изменения; основные
фильтры не имели составных индексов.

Причина: SQLite отражает Enum как VARCHAR; индексы не были оформлены в metadata.

Исправление: добавлен dialect-aware `compare_type` и индексы долгов, платежей,
доходов и расходов.

Файлы: `migrations/env.py`, `app/models.py`, новая миграция.

Добавленный regression test: schema/migration contracts; `flask db check`
возвращает `No new upgrade operations detected`.

### ISSUE-017 — feature flags и dev init применялись неполно

Severity: **MEDIUM**

Проблема: архив/экспорт могли оставаться доступны при выключенном флаге, а
`/api/init-db` был слишком широким dev endpoint.

Причина: флаги использовались в UI, но не во всех server-side routes.

Исправление: server-side gate для архива/экспорта; init endpoint доступен только
при debug + dev login.

Файлы: `app/routes/admin.py`, `app/routes/debts.py`, `app/routes/main.py`.

Добавленный regression test: route/security suite и браузерный smoke.

### ISSUE-018 — неполная валидация форм и JSON

Severity: **MEDIUM**

Проблема: пустой/некорректный JSON, чрезмерно длинные строки, несовместимые даты,
нулевые суммы и слишком большие ставки обрабатывались непоследовательно.

Причина: проверка полагалась на HTML и отдельные маршруты.

Исправление: добавлены server-side bounds и единые ответы 422/500 без traceback.

Файлы: `app/utils.py`, routes долгов, доходов, расходов и платежей.

Добавленный regression test: financial/security suites и полный route suite.

### ISSUE-019 — настройки администратора принимали произвольные значения

Severity: **MEDIUM**

Проблема: crafted POST мог записать нечисловой лимит и затем сломать создание
долга/Telegram-команду.

Причина: проверялись только browser input types.

Исправление: атомарная server-side валидация имени, валюты, лимитов и взаимного
порядка warning/urgent days.

Файлы: `app/routes/admin.py`, `app/utils.py`, `templates/admin_settings.html`.

Добавленные regression tests: два `test_admin_settings_*`.

### ISSUE-020 — emergency admin не мог завершить impersonation

Severity: **MEDIUM**

Проблема: строковый ID `admin` искался в таблице `users`, поэтому восстановление
аварийной admin-сессии всегда завершалось входом на login.

Причина: endpoint учитывал только DB superadmin.

Исправление: поддержано безопасное восстановление `AdminUser`; действие стало
POST с CSRF.

Файлы: `app/routes/auth.py`, `templates/base.html`.

Добавленный regression test: `test_emergency_admin_session_can_be_restored_after_impersonation`.

### ISSUE-021 — CSV export допускал spreadsheet formula injection

Severity: **MEDIUM**

Проблема: текст пользователя, начинающийся с `=`, `+`, `-`, `@`, мог исполняться
табличным редактором.

Причина: значения экспортировались без нейтрализации.

Исправление: опасные префиксы экранируются апострофом.

Файлы: `app/routes/admin.py`.

Добавленный regression test: `test_spreadsheet_formula_prefixes_are_neutralized`.

### ISSUE-022 — ошибки и security headers обрабатывались непоследовательно

Severity: **MEDIUM**

Проблема: часть исключений не логировалась; не было единых безопасных HTML/JSON
ответов и базовых response headers.

Причина: использовались Flask defaults и локальные catch-блоки.

Исправление: добавлены безопасные 400/403/404/500 handlers, rollback, структурное
логирование и headers `nosniff`, `SAMEORIGIN`, Referrer/Permissions Policy, HSTS
в production.

Файлы: `app/__init__.py`, `templates/error.html`, routes/main и другие routes.

Добавленные regression tests: `test_security_headers_are_added`,
`test_error_pages_and_api_errors_do_not_expose_internal_details`.

### ISSUE-023 — другие runtime-зависимости содержали известные уязвимости

Severity: **MEDIUM**

Проблема: первоначальный audit обнаружил 12 vulnerability findings суммарно в
Authlib, pypdf, Flask, python-dotenv, requests, pip и setuptools.

Причина: устаревшие pins и инструменты окружения.

Исправление: обновлены только затронутые runtime packages и packaging tools;
финальный audit чистый.

Файлы: `requirements.txt` и локальное `.venv` (не коммитится).

Добавленный regression test: `pip check` + `pip-audit`.

### ISSUE-024 — синтетический Google user ID был нестабилен и мог столкнуться

Severity: **MEDIUM**

Проблема: ограниченный modulo ID создавал ненужный риск коллизии.

Причина: внешний subject напрямую сворачивался в короткое пространство.

Исправление: детерминированный отрицательный SHA-256 ID с проверкой коллизии;
поля профиля ограничены по длине.

Файлы: `app/routes/auth.py`.

Добавленный regression test: общий auth/security suite.

### ISSUE-025 — init_db.sql содержал устаревшую альтернативную схему

Severity: **MEDIUM**

Проблема: развёртывание через SQL-файл создавало схему, отличную от Alembic.

Причина: два независимых источника истины.

Исправление: SQL оставлен только для создания пустой базы; таблицы создаёт Alembic.

Файлы: `init_db.sql`, `README.md`, `DEPLOYMENT.md`.

Добавленный regression test: migration contracts и clean migration smoke.

### ISSUE-026 — горизонтальная прокрутка на мобильном дашборде

Severity: **LOW**

Проблема: при viewport 390 px содержимое было шире на 66 px.

Причина: intrinsic grid width фильтров расширял неявную колонку dashboard.

Исправление: колонка ограничена `minmax(0, 1fr)`, filter group получил
`min-width:0/max-width:100%`.

Файлы: `static/css/style.css`, `templates/base.html`.

Добавленный regression test: повторная браузерная проверка 390×844:
`scrollWidth == clientWidth`.

### ISSUE-027 — в runtime-дереве оставались legacy и TODO-заглушки

Severity: **LOW**

Проблема: два старых монолитных файла и пустой `forms.py` создавали ложную точку
сопровождения.

Причина: артефакты реструктуризации не были удалены.

Исправление: неиспользуемые файлы удалены после поиска всех импортов.

Файлы: удалены `legacy_app.py`, `legacy_models.py`, `app/forms.py`.

Добавленный regression test: `test_run_uses_package_app` и полный import suite.

### ISSUE-028 — в Git хранились локальная БД и вывод тестов

Severity: **LOW**

Проблема: `dev.db` содержала пользовательские финансовые записи, а
`test_results.txt` был временным артефактом.

Причина: неполный `.gitignore`.

Исправление: файлы удалены только из Git index и добавлены в ignore; локальный
файл БД пользователя физически не удалялся.

Файлы: `.gitignore`, удалённые из репозитория `dev.db`, `test_results.txt`.

Добавленный regression test: проверка `git status`/tracked files.

### ISSUE-029 — документация не описывала безопасное обновление production

Severity: **LOW**

Проблема: не были чётко описаны backup, Alembic как единственный источник схемы,
production flags, проверка после выкладки и restore-based rollback.

Причина: README совмещал локальный запуск и production без полного runbook.

Исправление: README актуализирован, добавлены `DEPLOYMENT.md`, `.env.example` и
этот release audit/changelog.

Файлы: `.env.example`, `README.md`, `DEPLOYMENT.md`, `RELEASE_AUDIT.md`,
`RELEASE_CHANGES.md`.

Добавленный regression test: documentation tests и ручная сверка команд.

### Дополнение — безопасное обновление сервера из админки

После основного аудита добавлен отдельный superadmin-раздел, который получает
последний опубликованный GitHub Release и передаёт установку root-owned помощнику.
HTTP-запрос не может передать shell-команду: разрешён только проверенный тег.
Фоновая systemd-задача повторно сверяет GitHub и origin, блокирует параллельные
запуски, создаёт backup MySQL/SQLite, устанавливает точный commit тега, применяет
миграции и перезапускает только настроенную службу. Функция выключена по умолчанию.

Файлы: `app/services/server_update_service.py`, `app/routes/admin.py`,
`templates/admin_server_update.html`, `deploy.sh`, `deployment/*`, `config.py`,
`.env.example`, `DEPLOYMENT.md`, `tests/test_server_update.py`.

Проверка: 15 service/route/security-тестов, deploy contract test, полный прогон
168/168. Реальный systemd/mysqldump запуск остаётся обязательным серверным smoke.

## Внесённые изменения

В таблице «данные» означает прямое изменение пользовательских записей самим
файлом. Миграция отдельно отмечена в последнем столбце.

| Файл | Зачем и что изменено | Данные | Миграция |
|---|---|---:|---:|
| `.env.example` | безопасные ENV-примеры и новые production-переменные | нет | нет |
| `.gitignore` | исключены DB и test output | нет | нет |
| `README.md` | актуальные запуск, Telegram, миграции и checklist | нет | нет |
| `DEPLOYMENT.md` | новый production/backup/rollback runbook | нет | нет |
| `RELEASE_AUDIT.md` | подробный результат аудита | нет | нет |
| `RELEASE_CHANGES.md` | человекочитаемый changelog | нет | нет |
| `app/__init__.py` | production validation, safe errors, headers, MySQL helper | нет | нет |
| `app/forms.py` | удалена пустая TODO-заглушка | нет | нет |
| `app/models.py` | FK, unique constraints и query indexes | нет напрямую | да |
| `app/routes/admin.py` | settings/flags/CSV-защита и superadmin update routes/audit/status | нет напрямую | нет |
| `app/services/server_update_service.py` | GitHub Release, строгий tag/helper и lock | нет | нет |
| `templates/admin_server_update.html` | UI версии, запуска и живого статуса | нет | нет |
| `deploy.sh` | exact release checkout, backup gate и внешний restart | нет напрямую | выполняет |
| `deployment/*` | root-owned systemd runner, backup, sudoers/config samples | backup | выполняет |
| `app/routes/auth.py` | регистрация, test session, Google ID, impersonation | нет напрямую | нет |
| `app/routes/debts.py` | строгая проверка JSON/сумм/дат/лимитов/flags | нет напрямую | нет |
| `app/routes/expenses.py` | валидация, import confirm, безопасное удаление связей | нет напрямую | нет |
| `app/routes/incomes.py` | валидация сумм/строк и безопасные ошибки | нет напрямую | нет |
| `app/routes/main.py` | Decimal ипотечной сводки, flags, dev init, logging | нет напрямую | нет |
| `app/routes/payments.py` | Decimal и безопасные ответы payment API | нет напрямую | нет |
| `app/routes/telegram_bot.py` | constant-time webhook secret | нет | нет |
| `app/services/bank_statement_import_service.py` | лимиты CSV/XLSX/PDF и безопасный parser | нет до confirm | нет |
| `app/services/debt_interest_service.py` | единый сегментный процентный расчёт | влияет на новые расчёты | нет |
| `app/services/debt_math_service.py` | календарные сегменты, базы дней и округление | влияет на новые расчёты | нет |
| `app/services/finance_summary_service.py` | Decimal суммы и точные ипотечные проценты | влияет на отображаемые итоги | нет |
| `app/services/monthly_expenses_service.py` | защита от concurrent duplicate | влияет на будущую генерацию | да |
| `app/services/payment_service.py` | инварианты, точный principal/interest и replay | влияет на новые/изменённые платежи | нет |
| `app/services/telegram_bot_service.py` | подтверждение quick commands и bounds | нет до подтверждения | нет |
| `app/utils.py` | единый money parser и атомарное сохранение settings | нет напрямую | нет |
| `config.py` | окружение, cookie, request size, URL encoding | нет | нет |
| `dev.db` | удалена из Git index, локальная копия сохранена | нет | нет |
| `init_db.sql` | убрана альтернативная таблицам Alembic схема | нет | нет |
| `legacy_app.py` | удалён неиспользуемый старый монолит | нет | нет |
| `legacy_models.py` | удалены неиспользуемые старые модели | нет | нет |
| `migrations/env.py` | dialect-aware schema comparison | нет | нет |
| `migrations/versions/20260829_schema_integrity.py` | FK, unique и query indexes | схема существующей БД | да |
| `requirements.txt` | security/stability upgrades зависимостей | нет | нет |
| `static/css/style.css` | устранено mobile overflow | нет | нет |
| `templates/admin_settings.html` | browser constraints и ошибки формы | нет | нет |
| `templates/base.html` | CSRF POST impersonation и cache-bust CSS | нет | нет |
| `templates/error.html` | безопасная страница ошибок | нет | нет |
| `test_results.txt` | удалён временный tracked output | нет | нет |
| `tests/test_debt_types.py` | ожидания точного расчёта процентов | нет | нет |
| `tests/test_financial_integrity.py` | новые money/interest/payment regressions | нет | нет |
| `tests/test_migrations.py` | новая head migration | нет | нет |
| `tests/test_mysql_compatibility.py` | URL и MySQL DDL compile | нет | нет |
| `tests/test_schema_contract.py` | новые constraints/index contracts | нет | нет |
| `tests/test_security.py` | production/auth/errors/CSV/settings tests | нет | нет |
| `tests/test_telegram_bot.py` | подтверждение быстрых команд | нет | нет |
| `tests/test_telegram_mini_app.py` | запрет закрытой регистрации | нет | нет |

## Миграции

Создана одна новая миграция:

- revision: `20260829_integrity`;
- parent: `20260824_combpay`;
- файл: `migrations/versions/20260829_schema_integrity.py`;
- добавляет self-FK расхода с `ON DELETE SET NULL`;
- добавляет уникальность регулярного события;
- добавляет индексы основных пользовательских и календарных запросов;
- перед unique index проверяет существующие дубликаты и останавливается с
  понятным сообщением, не удаляя данные автоматически.

Alembic имеет одну голову: `20260829_integrity`. На чистой SQLite полный upgrade,
current, heads и check прошли. На копии существующей БД количество финансовых
записей до/после совпало, integrity checks прошли.

**Backup перед обновлением обязателен.** Миграция меняет схему существующей базы.
При обнаружении duplicate monthly occurrences их нужно разрешить вручную только
после отдельной резервной копии.

## Изменения документации

- README разделяет локальный и production запуск;
- зафиксировано требование Python 3.10+;
- описаны строгие production flags и секреты;
- Telegram quick commands документированы как confirm-before-write;
- Alembic объявлен единственным источником схемы;
- добавлены команды `db current`, `db heads`, `db check`;
- добавлен `DEPLOYMENT.md` с backup, обновлением, smoke и rollback;
- `.env.example` не содержит реальных секретов и перечисляет новые flags;
- созданы обязательные release audit и changelog.

## Безопасность

Проверены secret/config patterns, `.env` tracking, CSRF, XSS-границы шаблонов,
SQLAlchemy query usage, IDOR-фильтры `user_id`, open redirect, upload parsing,
unsafe deserialization, command execution, debug/test endpoints, cookie settings,
auth flows, webhook secret и логи. Значения существующих секретов не выводились.

Результат:

- `.env` не отслеживается Git;
- tracked DB удаляется из репозитория без удаления локального файла;
- production требует сильный SECRET_KEY и secure cookie;
- debug/dev/test принудительно запрещены в production;
- emergency admin в production требует password hash, plaintext не принимается;
- Telegram подписи, возраст initData и webhook secret проверяются;
- Google и Telegram не создают пользователя при закрытой регистрации;
- JSON/API ошибки не раскрывают traceback;
- CSV formula prefixes нейтрализованы;
- upload parser имеет ресурсные лимиты;
- dependency audit после обновления: `No known vulnerabilities found`.

## Известные ограничения

1. Живой MySQL/MariaDB отсутствовал. Проверены dialect-aware код, MySQL DDL
   compilation, migration contracts и SQLite migration, но не фактический
   `flask db upgrade` на MySQL. Это обязательный staging gate.
2. Docker/локальный MySQL client/server в среде недоступны.
3. Реальные Telegram API/webhook и Google OAuth endpoints не вызывались; проверена
   локальная криптографическая/маршрутная логика с тестовыми payloads.
4. Telegram rate limiter хранится в памяти процесса. При нескольких worker/process
   нужен общий Redis/БД limiter; текущий Waitress single-process deployment не
   должен масштабироваться горизонтально без этого изменения.
5. Нагрузочное и длительное soak-тестирование не выполнялось.
6. Юридические и организационные требования к персональным данным требуют
   отдельной политики, контроля доступа и проверяемого процесса удаления.

## Инструкция обновления production

1. Закрыть запись в приложение или включить короткое maintenance window.
2. Проверить текущий commit, head миграций и свободное место.
3. Сделать и проверить backup MySQL/MariaDB:

   ```bash
   mysqldump --single-transaction --routines --triggers -u officium -p debt_manager > officium-before-20260829-integrity.sql
   ```

4. Сохранить текущий `.env`, конфигурацию reverse proxy и service unit отдельно.
5. Развернуть новый код без перезаписи `.env`.
6. Проверить production ENV:

   ```env
   OFFICIUM_ENV=production
   FLASK_DEBUG=false
   SESSION_COOKIE_SECURE=true
   DEV_LOGIN_ENABLED=false
   TEST_USER_ENABLED=false
   ```

   Неиспользуемые Telegram/Google/admin функции выключить. Для включённых функций
   задать требуемые токены/секреты; emergency admin использует только
   `ADMIN_PASSWORD_HASH`.

7. Активировать venv и установить обновлённые зависимости:

   ```powershell
   .\.venv\Scripts\python.exe -m pip install -r requirements.txt
   .\.venv\Scripts\python.exe -m pip check
   ```

8. До миграции запустить тесты на release artifact:

   ```powershell
   .\.venv\Scripts\python.exe -m unittest discover -v
   ```

9. Применить миграцию к staging-копии MySQL/MariaDB и выполнить smoke. Только
   после этого применять к production:

   ```powershell
   $env:FLASK_APP = 'run.py'
   .\.venv\Scripts\python.exe -m flask db heads
   .\.venv\Scripts\python.exe -m flask db current
   .\.venv\Scripts\python.exe -m flask db upgrade
   .\.venv\Scripts\python.exe -m flask db current
   .\.venv\Scripts\python.exe -m flask db check
   ```

   Ожидаемая единственная голова/current: `20260829_integrity`; check —
   `No new upgrade operations detected`.

10. Перезапустить service с Waitress за HTTPS reverse proxy.
11. Проверить `/login`, авторизованный dashboard, `/api/debts` без входа (401),
    создание тестового дохода/расхода/долга/платежа, импорт preview/confirm,
    admin flags и журналы.
12. Для включённых интеграций проверить один Telegram login/Mini App/webhook с
    повтором одного `update_id` и один Google login.
13. Проверить логи на 4xx/5xx, migration errors и отсутствие секретов.
14. Открыть запись и продолжить наблюдение.

## Rollback

1. Немедленно закрыть запись и остановить новый service.
2. Сохранить отдельную копию проблемной БД и логов для анализа.
3. Не выполнять слепой `flask db downgrade`: старые миграции проекта местами
   намеренно запрещают downgrade, а восстановление финансовых данных важнее.
4. Создать новую пустую БД и восстановить предрелизный дамп.
5. Вернуть предыдущий проверенный commit и его зависимости.
6. Подключить старый код к восстановленной БД.
7. Запустить smoke-проверки входа, долгов, платежей, доходов и расходов.
8. Открыть доступ только после проверки целостности и логов.

## Финальный вывод

Версию можно выпускать в production **после** выполнения обязательных gates:
backup, staging upgrade на реальном MySQL/MariaDB, безопасный `.env`, установка
зависимостей и интеграционный smoke включённых внешних провайдеров. Локальных
блокирующих дефектов после исправлений и финального прогона 168 тестов не осталось.
