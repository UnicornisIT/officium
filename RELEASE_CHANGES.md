# Release Changes

## Исправлено

- устранено повторное начисление просроченных процентов при нескольких частичных
  платежах;
- платежи не принимают non-finite/отрицательные суммы, переплату, закрытый долг и
  разбивку, не сходящуюся до копейки;
- проценты корректно делятся по календарному году и дате смены ставки;
- високосный год, fixed 365/366 и включение дня платежа учитываются единообразно;
- финансовая и ипотечная сводки используют точные Decimal-суммы;
- быстрые Telegram-команды создают запись только после подтверждения;
- закрытая регистрация действует для Telegram, Mini App и Google;
- отключённый test access нельзя вернуть поддельным legacy session ID;
- emergency admin корректно возвращается из impersonation через POST+CSRF;
- feature flags реально закрывают архив и экспорт;
- dev init endpoint недоступен вне явного debug/dev режима;
- импорт выписок ограничен по размеру, сложности XLSX и числу PDF-страниц;
- регулярные расходы защищены от конкурентных дубликатов;
- CSV export нейтрализует spreadsheet formulas;
- server-side валидация покрывает настройки, JSON, суммы, даты и длины строк;
- ошибки 400/403/404/500 не показывают внутренние детали;
- исправлена мобильная горизонтальная прокрутка дашборда;
- MySQL credentials корректно URL-encode;
- Google synthetic ID стал детерминированным и collision-aware;
- удалена устаревшая альтернативная схема `init_db.sql`.

## Улучшено

- добавлен единый сервис финансовой математики;
- обработка ошибок журналирует внутреннюю причину и возвращает пользователю
  безопасное сообщение;
- добавлены индексы основных запросов по пользователю/статусу/дате;
- Alembic больше не считает SQLite VARCHAR эквивалент Enum schema drift;
- добавлены безопасные response headers;
- production-конфигурация валидируется до подключения расширений;
- суперадминистратор может проверить и установить последний опубликованный
  GitHub Release через отдельную страницу админки;
- серверное обновление выполняется отдельной systemd-задачей с точным тегом,
  резервной копией БД, миграциями, перезапуском и отображением статуса;
- из runtime-дерева удалены неиспользуемые legacy-модули и TODO-заглушка;
- локальная БД и test output исключены из Git.

## Добавлены тесты

- 8 тестов финансовой целостности: leap year, rate/year boundaries, day-count,
  rounding, payment breakdown, overpayment, closed debt и repeated partial late
  payments;
- 13 security/regression-тестов: production flags/secrets/cookie, test session,
  error responses, headers, CSV injection, admin settings и impersonation;
- 2 теста MySQL-совместимости: URL credentials и DDL compilation;
- 1 тест закрытой регистрации Telegram Mini App;
- 15 тестов обновления сервера: GitHub API, права superadmin, CSRF, строгий тег,
  блокировки, безопасный helper, журналирование и интерфейс;
- финальный результат: 168/168, пропущено 0.

## Безопасность

- Authlib обновлён с 1.3.0 до 1.7.2;
- pypdf обновлён с 4.3.1 до 6.16.2;
- обновлены Flask, Werkzeug, cryptography, requests и python-dotenv;
- `pip check`: конфликтов нет;
- `pip-audit`: известных уязвимостей нет;
- production запрещает debug/dev/test, insecure session cookie и слабый secret;
- Telegram token/webhook secret и Google credentials обязательны при включении;
- emergency admin в production требует password hash;
- обновление сервера выключено по умолчанию, доступно только superadmin и не
  принимает произвольные команды из HTTP-запроса;
- root-owned updater повторно проверяет последний GitHub Release и разрешённый
  origin, блокирует параллельные запуски и требует backup перед checkout;
- webhook secret сравнивается constant-time;
- `.env` остаётся вне Git, значения секретов в отчёты не попадали.

## База данных

- добавлена миграция `20260829_integrity`;
- добавлен self-FK `expenses.generated_from_id -> expenses.id` с `SET NULL`;
- добавлена уникальность регулярного события расхода;
- добавлены индексы долгов, платежей, доходов и расходов;
- чистый SQLite upgrade, current, heads и schema check прошли;
- migration copy сохранила counts и прошла integrity/foreign-key checks;
- `init_db.sql` теперь создаёт только пустую MySQL-базу, схему ведёт Alembic.

## Документация

- README синхронизирован с фактическим поведением и production validation;
- добавлен `DEPLOYMENT.md`;
- обновлён `.env.example`;
- добавлены `RELEASE_AUDIT.md` и `RELEASE_CHANGES.md`;
- описаны backup, migration verification, smoke и restore-based rollback.

## Изменённые файлы

- `.env.example` — новые безопасные ENV defaults;
- `.gitignore` — DB/test output;
- `README.md` — запуск, Telegram, production и миграции;
- `DEPLOYMENT.md` — production runbook;
- `RELEASE_AUDIT.md` — полный аудит;
- `RELEASE_CHANGES.md` — changelog;
- `app/__init__.py` — fail-fast, error handlers, headers, MySQL helper;
- `app/forms.py` — удалён;
- `app/models.py` — FK/index/unique contracts;
- `app/routes/admin.py` — settings, export и flags;
- `app/routes/auth.py` — auth/registration/Google/impersonation;
- `app/routes/debts.py` — debt validation;
- `app/routes/expenses.py` — expense/import validation;
- `app/routes/incomes.py` — income validation;
- `app/routes/main.py` — plan logging, Decimal mortgage summary, dev gate;
- `app/routes/payments.py` — payment API;
- `app/routes/telegram_bot.py` — webhook secret;
- `app/services/bank_statement_import_service.py` — parser limits;
- `app/services/debt_interest_service.py` — precise overdue interest;
- `app/services/debt_math_service.py` — central interest segments;
- `app/services/finance_summary_service.py` — Decimal summary;
- `app/services/monthly_expenses_service.py` — duplicate protection;
- `app/services/payment_service.py` — payment invariants and replay;
- `app/services/telegram_bot_service.py` — confirm-before-write;
- `app/services/server_update_service.py` — GitHub Release и безопасный запуск updater;
- `app/utils.py` — money parser/settings transaction support;
- `config.py` — environment/cookie/upload/MySQL configuration;
- `deploy.sh` — точная установка release tag и внешний restart;
- `deployment/` — root-owned updater, конфигурация и sudoers-шаблон;
- `dev.db` — removed from Git index only;
- `init_db.sql` — Alembic-only schema policy;
- `legacy_app.py`, `legacy_models.py` — removed;
- `migrations/env.py` — SQLite Enum comparison;
- `migrations/versions/20260829_schema_integrity.py` — new migration;
- `requirements.txt` — patched dependency versions;
- `static/css/style.css` — mobile overflow fix;
- `templates/admin_settings.html` — input constraints/errors;
- `templates/admin_dashboard.html` — ссылка на обновление сервера;
- `templates/admin_server_update.html` — версия, релиз, запуск и живой статус;
- `templates/base.html` — impersonation CSRF and CSS version;
- `templates/error.html` — safe error UI;
- `test_results.txt` — removed from Git index;
- `tests/test_debt_types.py` — precise expectations;
- `tests/test_financial_integrity.py` — new finance regressions;
- `tests/test_migrations.py` — new migration head;
- `tests/test_mysql_compatibility.py` — new MySQL compile tests;
- `tests/test_schema_contract.py` — constraints/index contracts;
- `tests/test_security.py` — new security regressions;
- `tests/test_server_update.py` — server update security/routes/service;
- `tests/test_telegram_bot.py` — confirmation flow;
- `tests/test_telegram_mini_app.py` — registration gate.

## Требования перед обновлением production

- backup БД: **требуется**;
- миграции: **требуется `flask db upgrade` до `20260829_integrity`**;
- staging на реальном MySQL/MariaDB: **требуется**;
- новые ENV-переменные: `OFFICIUM_ENV`, `SESSION_COOKIE_SECURE`,
  `MAX_CONTENT_LENGTH`, `TELEGRAM_LOGIN_ENABLED` и выключенная по умолчанию
  группа `SERVER_UPDATE_*`;
- production значения: debug/dev/test false, secure cookie true;
- новые зависимости: нет новых runtime packages, но обновлены версии существующих;
- после установки выполнить `pip check`, полный unittest, Alembic current/heads/check
  и smoke включённых интеграций.
