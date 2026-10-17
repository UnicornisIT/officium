# officium

[Русский](README.md) · English

A private web application for personal finance: debts, payments, incomes,
expenses, recurring spending, bank statement import and a Telegram bot for
quick entry.

## Features

- debts: credit cards, consumer loans, mortgages, split/instalment plans;
- debt payments with the balance recalculated;
- incomes and expenses by category;
- monthly expenses generated automatically;
- expense import from bank statements;
- monthly financial summary;
- financial plan: recommendations on where each income should go until the
  next one, goals and the emergency fund, a daily limit until the next income;
- sign-in with the Telegram Login Widget, Google OAuth, dev/test modes;
- one interface for the browser and the Telegram Mini App;
- a responsive admin panel: users, activity log, settings, reference lists,
  audit export and diagnostics without access to users' financial data;
- safe server update to the latest GitHub Release by a superadmin;
- a Telegram bot with buttons for expenses, incomes, debts and payments.

## Technology

- Python 3.10+
- Flask
- Flask-SQLAlchemy
- Flask-Migrate / Alembic
- Flask-Login
- Flask-WTF
- Authlib
- requests
- SQLite for local development
- MySQL/MariaDB for production

## Layout

```text
app/
  routes/       pages, API and webhook
  services/     business logic
  models.py     database models
templates/      HTML templates
static/         CSS and JS
migrations/     Alembic migrations
tests/          unittest tests
config.py       settings from the environment
run.py          application entry point
```

## Quick start

### PowerShell

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item .env.example .env
$env:FLASK_APP = 'run.py'
.\.venv\Scripts\python.exe -m flask db upgrade
.\.venv\Scripts\python.exe run.py
```

On Windows, `scripts\start.bat` automates the same steps: it creates `.venv`
and a local `.env` and turns on development mode, SQLite and dev sign-in.

### Bash

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
cp .env.example .env
export FLASK_APP=run.py
python -m flask db upgrade
python run.py
```

`.env.example` is already set up for development and SQLite. For a local
sign-in without an external provider, set `DEV_LOGIN_ENABLED=true` in the
created `.env`, on your own machine only. In development an empty `SECRET_KEY`
is replaced with a random key for the life of the process. Then open
`http://127.0.0.1:5000`.

## Settings

The complete, up-to-date list of variables is in [`.env.example`](.env.example).
Key production settings:

- `OFFICIUM_ENV=production` — the default mode in code;
- `SECRET_KEY` — a unique string of at least 32 characters, required in production;
- `APP_TIMEZONE=UTC` — the default time zone;
- `DB_ENGINE=mysql` with a dedicated `DB_USER=officium` — the recommended production setup;
- `TELEGRAM_LOGIN_ENABLED`, `TELEGRAM_MINI_APP_ENABLED`, `TELEGRAM_BOT_ENABLED`,
  `GOOGLE_LOGIN_ENABLED` and `SERVER_UPDATE_ENABLED` are off by default;
- `SERVER_UPDATE_REPOSITORY` is set explicitly as `<repository-owner>/officium`;
- the emergency admin sign-in only accepts `ADMIN_PASSWORD_HASH`.

Never commit the local `.env`, database files, logs or backups to Git.

In development an empty `SECRET_KEY` is replaced with a random key that only
lives until the process restarts; the application logs a warning about it. In
production the key is required. The emergency admin sign-in only supports
`ADMIN_PASSWORD_HASH`. To create the hash without printing the password on the
command line:

```powershell
python -c "from getpass import getpass; from werkzeug.security import generate_password_hash; print(generate_password_hash(getpass()))"
```

The Bash command is the same:

```bash
python -c 'from getpass import getpass; from werkzeug.security import generate_password_hash; print(generate_password_hash(getpass()))'
```

With `OFFICIUM_ENV=production` the application refuses to start with a weak
`SECRET_KEY`, with debug/dev/test sign-in, an insecure session cookie, without
the token of enabled Telegram features, without the secret of an enabled
Telegram webhook, without the credentials of enabled Google OAuth or without
the password hash of an enabled emergency admin sign-in.

## Migrations

Upgrade the database:

```powershell
$env:FLASK_APP = 'run.py'
.\.venv\Scripts\python.exe -m flask db upgrade
```

Create a new migration:

```powershell
.\.venv\Scripts\python.exe -m flask db migrate -m "Description"
.\.venv\Scripts\python.exe -m flask db upgrade
```

Check the state:

```powershell
.\.venv\Scripts\python.exe -m flask db current
.\.venv\Scripts\python.exe -m flask db heads
.\.venv\Scripts\python.exe -m flask db check
```

Do not use `db.create_all()` to update an existing database.
`scripts/init_db.sql` only creates an empty MySQL database; tables are always
created and updated with `flask db upgrade`.

## Telegram bot

The bot works through a webhook: `POST /telegram/webhook`.

What matters:

- the bot is off by default;
- the user must already exist in the database with the right `telegram_id`;
- the bot does not register new users from messages;
- a record is created only after confirmation.

Menu:

```text
Расход  Доход  Платеж
Долг    Долги  Итог
```

The `Расход` (expense), `Доход` (income), `Платеж` (payment) and `Долг` (debt)
buttons start step-by-step entry. `Долги` (debts) and `Итог` (summary) show the
debt list and the monthly summary right away.

Quick commands work too. They parse the line and show a confirmation screen;
the record appears only after the «Сохранить» (save) button:

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

Enable it:

```env
TELEGRAM_BOT_ENABLED=true
TELEGRAM_BOT_TOKEN=...
TELEGRAM_BOT_USERNAME=...
TELEGRAM_WEBHOOK_SECRET=long-random-secret
TELEGRAM_PRIVATE_CHAT_ONLY=true
```

Set up the webhook:

```bash
curl -X POST "https://api.telegram.org/bot$TELEGRAM_BOT_TOKEN/setWebhook" \
  -d "url=https://<your-domain>/telegram/webhook" \
  -d "secret_token=$TELEGRAM_WEBHOOK_SECRET"
```

Debt reminders:

```powershell
$env:FLASK_APP = 'run.py'
.\.venv\Scripts\python.exe -m flask send-telegram-reminders --dry-run
.\.venv\Scripts\python.exe -m flask send-telegram-reminders
```

## Telegram Mini App

The Mini App uses the same Flask interface, routes and database as the regular
site. Entry point: `https://<your-domain>/telegram-app`.

Setup:

1. Make sure the domain is served over HTTPS with a valid certificate.
2. In BotFather, create a Main Mini App or a Mini App with a short name.
3. Set the URL to `https://<your-domain>/telegram-app`.
4. Fill in `TELEGRAM_MINI_APP_SHORT_NAME` if the app has a short name.
5. Restart the application service.

Sign-in uses the signed `Telegram.WebApp.initData`. The signature and its
lifetime are checked locally on the server with `TELEGRAM_BOT_TOKEN`;
`initDataUnsafe` must never be trusted for sign-in.

Telegram Web (`web.telegram.org/k` and `/a`) opens the Mini App in a frame of
another site. For that case the server answers with
`Content-Security-Policy: frame-ancestors 'self' https://web.telegram.org`
instead of `X-Frame-Options: SAMEORIGIN` and gives the frame a separate cookie,
`officium_tg_frame`, with `SameSite=None; Secure; Partitioned`. The regular
`session` cookie stays `SameSite=Lax`, and the CSRF token is still required.
`TELEGRAM_WEB_FRAME_ANCESTORS` sets the allowed origins (an empty value forbids
opening in Telegram Web). Safari blocks cookies in a frame of another site, so
signing in from Telegram Web in Safari may not work — use a Telegram client.

The VPS does not need outbound access to `api.telegram.org` for Mini App
sign-in and the interface. It is needed separately for bot messages and
reminders.

## Security and personal data

The code reduces risks, but complying with personal data law also takes
organisational measures: a data processing policy, a clear purpose of
processing, restricted access, retention periods, backups and a deletion
procedure.

Technical measures in the project:

- the webhook checks `X-Telegram-Bot-Api-Secret-Token`;
- only existing users may use the bot;
- blocked users cannot use the bot;
- only private chats are accepted by default;
- messages per minute are rate-limited;
- the text of incoming Telegram messages is not written to `ActivityLog`;
- repeated webhooks do not create duplicates thanks to `telegram_processed_updates`;
- step-by-step entry is kept temporarily in `telegram_conversation_states`;
- the temporary state is cleared by TTL;
- a financial record is created only after confirmation.

Production checklist:

- HTTPS is on;
- `OFFICIUM_ENV=production`;
- `FLASK_DEBUG=false`;
- `SESSION_COOKIE_SECURE=true`;
- `DEV_LOGIN_ENABLED=false`;
- `TEST_USER_ENABLED=false` unless the test sign-in is meant to be public;
- unused `TELEGRAM_LOGIN_ENABLED`, `TELEGRAM_MINI_APP_ENABLED` and `TELEGRAM_BOT_ENABLED` are off;
- `SECRET_KEY`, `TELEGRAM_WEBHOOK_SECRET` and database passwords are unique and long;
- `.env` is not stored in the repository;
- only trusted people have access to the database and the server;
- backups are protected as well as the main database;
- new registrations are opened by an administrator with the `registration_enabled` setting;
- a verified database backup is made before an update.

## Admin panel

The `/admin` panel shows service data: accounts, roles, the activity log,
settings and service health. Debts, payments, incomes, expenses and goals of
particular users are neither shown nor exported there, including
administrators' own records.

Administrators from the users table (`admin` and `superadmin`) are regular
users at the same time: outside `/admin` they work in their own personal
cabinet with their own data, switching with the «Мой кабинет» (my cabinet)
link in the panel and «Панель администратора» (admin panel) in the cabinet
without signing in again. After a web sign-in they land in the panel; in the
Telegram Mini App, in the cabinet. The emergency administrator
(`ADMIN_LOGIN_ENABLED`) has no personal cabinet and always returns to the panel.

- the `admin` role sees the overview, users and the log and can only block
  regular users; the «Система» (system) group is available to `superadmin`
  and the emergency administrator;
- «Аудит-выгрузка» (audit export) downloads `accounts.csv` (without IP and
  browser data) and `activity_log.csv`; every export is logged and can be
  turned off with the `export_enabled` setting;
- the only way to a user's finances is «Войти как пользователь» (sign in as
  the user): `superadmin` only, not for blocked users or `admin`/`superadmin`;
  the session ends if the user is given admin rights. It needs a reason of
  5–200 characters, is view-only by default, is limited by
  `impersonation_timeout_minutes` (30 minutes), shows a banner on every page,
  and its start, end and changes made on the user's behalf are logged;
- the `payment_warning_days` and `urgent_payment_days` settings control the
  highlighting of upcoming payments and the bot's reminder window
  (`TELEGRAM_REMINDER_DAYS` is used until the setting is saved);
  `archive_enabled` hides the archive;
- reference lists give suggestions in the debt form: banks, product names,
  interest rates and comment templates.

## Financial plan

The «Распределение отдельных поступлений» (individual income allocation)
section recommends where each income of the month should go. There is one
reference point, the next income: the nearest salary or advance (or an income
marked as a main payment), or, when it is not recorded yet, the date a month
after a known payment. Incomes are taken from the earliest to the latest, and
each one:

1. pays the dates that have already come and are still uncovered;
2. pays the dates before the next income, nearest first;
3. keeps a share of the «Минимум на жизнь» (living minimum) for the days until
   the next income;
4. pays early the nearest dates within a month of its own date;
5. funds goals by priority and early repayment, and leaves the rest for
   everyday spending.

Nothing moves by itself: every line is a recommendation. When the money does
not cover the living minimum until the next income or a due date, the income
card warns about it. Dates at the start of the month that the previous month's
income reached are shown as paid by it. The dashboard and the plan show how
much can be spent per day until the next income: the everyday money of the
current income minus recorded spending other than recurring expenses. An
income can be marked «Основная выплата» (main payment) or «Разовое
поступление» (one-off income) when its category does not fit.

## Monthly expenses

Generate recurring expenses:

```powershell
$env:FLASK_APP = 'run.py'
.\.venv\Scripts\python.exe -m flask generate-monthly-expenses
```

For a particular user or month:

```powershell
.\.venv\Scripts\python.exe -m flask generate-monthly-expenses --user-id 123
.\.venv\Scripts\python.exe -m flask generate-monthly-expenses --month 2026-07
```

Running it again does not create duplicates.

## Tests

All tests:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -v
```

Only the Telegram bot:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_telegram_bot -v
```

Migrations and schema:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_migrations tests.test_schema_contract -v
```

The `.github/workflows/ci.yml` workflow runs Python/SQLite, real MySQL
migrations and dump/restore by a user without `PROCESS`, a blocking
`pip-audit` and Playwright in Chromium/Firefox/WebKit. JUnit, coverage,
screenshots, traces and sanitized logs are kept as artifacts. The full
Ubuntu/systemd update gate lives in `staging-release-gate.yml` and needs a
separate disposable QA repository. The test plan is in
[`docs/QA_TEST_PLAN.md`](docs/QA_TEST_PLAN.md), the release criteria in
[`docs/RELEASE_CHECKLIST.md`](docs/RELEASE_CHECKLIST.md) (both in Russian).

## Production

Minimal order:

1. Configure `.env`.
2. Make a backup and set up MySQL/MariaDB.
3. Install the dependencies.
4. Run `flask db upgrade`.
5. Run the application with Waitress/systemd.
6. Put Nginx in front as a reverse proxy.
7. Turn on HTTPS.
8. Set up the Telegram webhook and Google OAuth only after HTTPS.
9. Run the tests before an update.

The full step-by-step guide, post-deployment checks and rollback are in
[`docs/DEPLOYMENT.md`](docs/DEPLOYMENT.md) (in Russian).

Pre-release check:

```bash
git status
python -m unittest discover -v
flask db upgrade
flask db current
flask db heads
flask db check
```

A manual MySQL backup without the global `PROCESS` privilege (the built-in
updater also hides the password in a temporary option file):

```bash
mysqldump --single-transaction --quick --no-tablespaces \
  --routines --triggers --events -u officium -p officium > backup.sql
```

For a live SQLite database use the backup API (for example Python's
`sqlite3.backup`) rather than copying the `.db` file while WAL is on.

A superadministrator can check and install the latest published GitHub
Release from the «Обновление сервера» (server update) page. The feature is off
by default and needs a root-owned socket-activated updater installed once;
Flask does not use `sudo`, so `NoNewPrivileges=true` stays on. The full
procedure is in [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md#5-обновление-managed-установки).
The «На сервере» (on the server) card is filled from local data only, so it is
visible even with the feature off: the version, release date and notes from
that version's section of `CHANGELOG.md`, the commit, commit date, Alembic
revision and the last successful update. When the latest version is
installed, the notes are not repeated in the GitHub release card.
