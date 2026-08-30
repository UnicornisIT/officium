# officium

Закрытое веб-приложение для личного учета финансов: долги, платежи, доходы, расходы, регулярные траты, импорт банковских выписок и Telegram-бот для быстрого ввода.

## Возможности

- долги: кредитные карты, потребительские кредиты, ипотека, split/рассрочка;
- платежи по долгам с пересчетом остатка;
- доходы и расходы по категориям;
- ежемесячные расходы с автогенерацией;
- импорт расходов из банковских выписок;
- месячная финансовая сводка;
- вход через Telegram Login Widget, Google OAuth, dev/test-режимы;
- единый интерфейс для браузера и Telegram Mini App;
- админ-панель для пользователей, настроек, справочников, логов и экспорта;
- безопасное обновление сервера до последнего GitHub Release из-под superadmin;
- Telegram-бот с кнопками для расходов, доходов, долгов и платежей.

## Технологии

- Python 3.10+
- Flask
- Flask-SQLAlchemy
- Flask-Migrate / Alembic
- Flask-Login
- Flask-WTF
- Authlib
- requests
- SQLite для локальной разработки
- MySQL/MariaDB для production

## Структура

```text
app/
  routes/       страницы, API и webhook
  services/     бизнес-логика
  models.py     модели БД
templates/      HTML-шаблоны
static/         CSS и JS
migrations/     миграции Alembic
tests/          unittest-тесты
config.py       настройки из окружения
run.py          запуск приложения
```

## Быстрый старт

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
```

Для локального SQLite в `.env`:

```env
OFFICIUM_ENV=development
SECRET_KEY=local-only-random-secret-at-least-32-characters
FLASK_DEBUG=true
SESSION_COOKIE_SECURE=false
DB_ENGINE=sqlite
SQLITE_PATH=dev.db
DEV_LOGIN_ENABLED=true
TEST_USER_ENABLED=false
TELEGRAM_BOT_ENABLED=false
GOOGLE_LOGIN_ENABLED=false
```

Применить миграции и запустить:

```powershell
$env:FLASK_APP = 'run.py'
.\.venv\Scripts\python.exe -m flask db upgrade
.\.venv\Scripts\python.exe run.py
```

Открыть: `http://127.0.0.1:5000`.

## Настройки

Главные переменные окружения:

```env
OFFICIUM_ENV=production
SECRET_KEY=<случайная строка длиной не менее 32 символов>
FLASK_DEBUG=false
SESSION_COOKIE_SECURE=true
MAX_CONTENT_LENGTH=10485760

DB_ENGINE=mysql
DB_HOST=localhost
DB_PORT=3306
DB_USER=officium
DB_PASSWORD=<пароль отдельного пользователя БД>
DB_NAME=debt_manager

TELEGRAM_BOT_TOKEN=
TELEGRAM_BOT_USERNAME=
TELEGRAM_LOGIN_ENABLED=false
TELEGRAM_MINI_APP_ENABLED=false
TELEGRAM_MINI_APP_SHORT_NAME=
TELEGRAM_WEB_APP_AUTH_MAX_AGE_SECONDS=86400
TELEGRAM_BOT_ENABLED=false
TELEGRAM_WEBHOOK_SECRET=long-random-secret
TELEGRAM_PRIVATE_CHAT_ONLY=true
TELEGRAM_BOT_RATE_LIMIT_PER_MINUTE=20
TELEGRAM_REMINDER_DAYS=7
TELEGRAM_UPDATE_RETENTION_DAYS=30
TELEGRAM_CONVERSATION_TTL_MINUTES=30

DEV_LOGIN_ENABLED=false
TEST_USER_ENABLED=false

GOOGLE_LOGIN_ENABLED=false
GOOGLE_CLIENT_ID=
GOOGLE_CLIENT_SECRET=
GOOGLE_REDIRECT_URI=https://your-domain.com/auth/google/callback

ADMIN_LOGIN_ENABLED=false
ADMIN_TELEGRAM_IDS=
ADMIN_PASSWORD_HASH=
```

`.env` нельзя коммитить в git.

В режиме `OFFICIUM_ENV=production` приложение откажется запускаться со слабым
`SECRET_KEY`, с debug/dev/test-входом, небезопасной cookie сессии, без токена
включённых Telegram-функций, без секрета включённого Telegram webhook, без
реквизитов включённого Google OAuth или без хеша пароля включённого аварийного
админ-входа. Обычный `ADMIN_PASSWORD` в production не принимается.

## Миграции

Обновить базу:

```powershell
$env:FLASK_APP = 'run.py'
.\.venv\Scripts\python.exe -m flask db upgrade
```

Создать новую миграцию:

```powershell
.\.venv\Scripts\python.exe -m flask db migrate -m "Описание"
.\.venv\Scripts\python.exe -m flask db upgrade
```

Проверить состояние:

```powershell
.\.venv\Scripts\python.exe -m flask db current
.\.venv\Scripts\python.exe -m flask db heads
.\.venv\Scripts\python.exe -m flask db check
```

Не используйте `db.create_all()` для обновления существующей базы.
`init_db.sql` создаёт только пустую MySQL-базу; таблицы всегда создаются и
обновляются командой `flask db upgrade`.

## Telegram-бот

Бот работает через webhook: `POST /telegram/webhook`.

Что важно:

- бот выключен по умолчанию;
- пользователь должен уже существовать в базе с нужным `telegram_id`;
- бот не регистрирует новых пользователей из сообщений;
- запись создается только после подтверждения.

Меню:

```text
Расход  Доход  Платеж
Долг    Долги  Итог
```

Кнопки `Расход`, `Доход`, `Платеж`, `Долг` запускают пошаговый ввод. Кнопки `Долги` и `Итог` сразу показывают список долгов и месячную сводку.

Быстрые команды тоже работают. Они распознают строку и показывают экран проверки;
запись появляется только после кнопки «Сохранить»:

```text
расход 850 продукты пятерочка
доход 120000 зарплата работа
платеж 5000 сбер
долг 300000 сбер кредит мин=15000 ставка=18.5 дата=2026-08-15
долги
итог
/privacy
/cancel
```

Включение:

```env
TELEGRAM_BOT_ENABLED=true
TELEGRAM_BOT_TOKEN=...
TELEGRAM_BOT_USERNAME=...
TELEGRAM_WEBHOOK_SECRET=long-random-secret
TELEGRAM_PRIVATE_CHAT_ONLY=true
```

Настройка webhook:

```bash
curl -X POST "https://api.telegram.org/bot$TELEGRAM_BOT_TOKEN/setWebhook" \
  -d "url=https://your-domain.com/telegram/webhook" \
  -d "secret_token=$TELEGRAM_WEBHOOK_SECRET"
```

Напоминания по долгам:

```powershell
$env:FLASK_APP = 'run.py'
.\.venv\Scripts\python.exe -m flask send-telegram-reminders --dry-run
.\.venv\Scripts\python.exe -m flask send-telegram-reminders
```

## Telegram Mini App

Mini App использует тот же Flask-интерфейс, маршруты и базу данных, что и обычный сайт.
Точка входа: `https://your-domain.com/telegram-app`.

Настройка:

1. Убедиться, что домен доступен по HTTPS с действующим сертификатом.
2. В BotFather создать Main Mini App или Mini App с коротким именем.
3. Указать URL `https://your-domain.com/telegram-app`.
4. Заполнить `TELEGRAM_MINI_APP_SHORT_NAME`, если создано приложение с коротким именем.
5. Перезапустить сервис приложения.

Авторизация выполняется через подписанный `Telegram.WebApp.initData`. Подпись и срок действия
проверяются локально на сервере с помощью `TELEGRAM_BOT_TOKEN`; доверять данным
`initDataUnsafe` для входа нельзя.

Исходящий доступ VPS к `api.telegram.org` для входа и работы интерфейса Mini App не требуется.
Он нужен отдельно для сообщений бота и напоминаний.

## Безопасность и ПД

Код снижает риски, но соблюдение закона о персональных данных требует еще и организационных мер: политики обработки ПД, понятной цели обработки, ограничения доступа, сроков хранения, резервного копирования и процедуры удаления данных.

Технические меры в проекте:

- webhook проверяет `X-Telegram-Bot-Api-Secret-Token`;
- доступ через бота разрешен только существующим пользователям;
- заблокированные пользователи не могут работать через бота;
- по умолчанию принимаются только личные чаты;
- включен лимит сообщений в минуту;
- текст входящих Telegram-сообщений не пишется в `ActivityLog`;
- повторные webhook не создают дубликаты благодаря `telegram_processed_updates`;
- пошаговый ввод временно хранится в `telegram_conversation_states`;
- временное состояние очищается по TTL;
- финансовая запись создается только после подтверждения.

Production-чеклист:

- HTTPS включен;
- `OFFICIUM_ENV=production`;
- `FLASK_DEBUG=false`;
- `SESSION_COOKIE_SECURE=true`;
- `DEV_LOGIN_ENABLED=false`;
- `TEST_USER_ENABLED=false`, если тестовый вход не нужен публично;
- неиспользуемые `TELEGRAM_LOGIN_ENABLED`, `TELEGRAM_MINI_APP_ENABLED` и `TELEGRAM_BOT_ENABLED` отключены;
- `SECRET_KEY`, `TELEGRAM_WEBHOOK_SECRET`, пароли БД уникальные и длинные;
- `.env` не хранится в репозитории;
- доступ к БД и серверу есть только у доверенных людей;
- резервные копии защищены так же, как основная база.
- новые регистрации включаются администратором настройкой `registration_enabled`;
- перед обновлением создаётся проверенная резервная копия БД.

## Ежемесячные расходы

Сгенерировать регулярные расходы:

```powershell
$env:FLASK_APP = 'run.py'
.\.venv\Scripts\python.exe -m flask generate-monthly-expenses
```

Для конкретного пользователя или месяца:

```powershell
.\.venv\Scripts\python.exe -m flask generate-monthly-expenses --user-id 123
.\.venv\Scripts\python.exe -m flask generate-monthly-expenses --month 2026-07
```

Повторный запуск не создает дубликаты.

## Тесты

Все тесты:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -v
```

Только Telegram-бот:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_telegram_bot -v
```

Миграции и схема:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_migrations tests.test_schema_contract -v
```

## Production

Минимальный порядок:

1. Настроить `.env`.
2. Создать резервную копию и настроить MySQL/MariaDB.
3. Установить зависимости.
4. Выполнить `flask db upgrade`.
5. Запустить приложение через Waitress/systemd.
6. Поставить Nginx reverse proxy.
7. Включить HTTPS.
8. Настроить Telegram webhook и Google OAuth только после HTTPS.
9. Запустить тесты перед обновлением.

Полная пошаговая инструкция, проверка после выкладки и откат описаны в
[`DEPLOYMENT.md`](DEPLOYMENT.md).

Проверка перед релизом:

```bash
git status
python -m unittest discover -v
flask db upgrade
flask db current
flask db heads
flask db check
```

Резервная копия MySQL:

```bash
mysqldump -u officium -p debt_manager > backup.sql
```

Резервная копия SQLite:

```powershell
Copy-Item dev.db dev.backup.db
```

Суперадминистратор может проверить и установить последний опубликованный GitHub
Release через раздел «Обновление сервера». Функция выключена по умолчанию и
требует один раз установить root-owned серверный помощник; полный порядок и
автоматическое резервное копирование описаны в [DEPLOYMENT.md](DEPLOYMENT.md#6-обновление-сервера-из-админки).
