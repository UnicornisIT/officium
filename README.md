# officium

`officium` - веб-приложение для персонального учета долгов, платежей, доходов и расходов.

Проект помогает видеть общую долговую нагрузку, ближайшие обязательные платежи, свободный остаток на месяц, историю доходов и расходов, ипотечные обязательства, просрочки и административную картину по пользователям. Приложение рассчитано на личное использование или небольшую закрытую группу пользователей с входом через Telegram, Google OAuth, dev/test-режимами и отдельной админ-панелью.

## Содержание

- [Что умеет проект](#что-умеет-проект)
- [Что добавили и улучшили](#что-добавили-и-улучшили)
- [Основные сценарии](#основные-сценарии)
- [Технологии](#технологии)
- [Архитектура](#архитектура)
- [Структура проекта](#структура-проекта)
- [Модель данных](#модель-данных)
- [Маршруты и API](#маршруты-и-api)
- [Переменные окружения](#переменные-окружения)
- [Локальный запуск](#локальный-запуск)
- [База данных](#база-данных)
- [Миграции](#миграции)
- [Ежемесячные расходы](#ежемесячные-расходы)
- [Авторизация](#авторизация)
- [Роли и админ-панель](#роли-и-админ-панель)
- [Тестирование](#тестирование)
- [Развертывание](#развертывание)
- [Обновление проекта](#обновление-проекта)
- [Резервное копирование](#резервное-копирование)
- [Частые ошибки](#частые-ошибки)
- [Безопасность](#безопасность)

## Что умеет проект

### Финансовый дашборд

- Показывает активные долги пользователя.
- Считает общий первоначальный долг и текущий остаток.
- Находит ближайший платеж и количество просрочек.
- Показывает доходы, расходы и платежи по долгам за выбранный месяц.
- Считает свободный остаток: доходы минус расходы минус платежи.
- Считает дневной бюджет до конца текущего месяца.
- При отсутствии данных за текущий месяц умеет подхватить последний месяц, где есть финансовые записи.

### Долги

- Поддерживаются типы долгов:
  - `credit_card` - кредитная карта;
  - `split` - рассрочка или split-платеж;
  - `mortgage` - ипотека.
- Для каждого долга хранятся банк, продукт, общая сумма, остаток, минимальный платеж, ставка, дата следующего платежа, комментарий и статус.
- Долги можно создавать, редактировать, архивировать, восстанавливать и удалять.
- Активные долги сортируются так, чтобы обязательства с ближайшими датами платежа были выше.
- API возвращает расчетные поля: процент погашения, дни до платежа, человекочитаемые даты и подпись типа долга.

### Платежи

- По каждому долгу можно вести историю платежей.
- При внесении платежа приложение уменьшает остаток долга.
- В платеже сохраняется сумма, дата, комментарий и остаток после платежа.
- API сообщает, погашен ли долг после внесенного платежа.
- Платежи по архивным долгам запрещены.

### Доходы и расходы

- Доходы ведутся по категориям: зарплата, аванс, подработка, возврат долга, премия, стипендия, другое.
- Расходы ведутся по категориям: продукты, транспорт, связь, аренда, кредиты, развлечения, здоровье, обучение, одежда, подписки, другое.
- Для расходов можно указать способ оплаты: карта, наличные, перевод или другое.
- История доходов и расходов группируется по месяцу фактической даты операции, а не по дате создания записи.
- Доходы и расходы можно создавать, редактировать и удалять.

### Ежемесячные расходы

- Расход можно отметить как ежемесячный.
- Для ежемесячного расхода создается группа `monthly_group_id`.
- При создании или включении ежемесячности приложение генерирует недостающие записи от месяца исходного расхода до текущего месяца.
- Генерация идемпотентна: повторный запуск не создает дубликаты.
- Даты корректно переносятся между месяцами разной длины: например, расход за 31 января создаст запись за 28 февраля или 29 февраля в високосный год.
- Ежемесячность можно отключить. Уже созданная история не удаляется, но будущие копии перестают генерироваться.
- Есть CLI-команда `flask generate-monthly-expenses` для ручного запуска или запуска по расписанию.

### Ипотека и просрочки

- Для ипотечных долгов есть отдельная страница `/mortgages`.
- Финансовая сводка считает количество ипотек, общий остаток по ипотекам и ориентировочный месячный процент.
- Страница `/debts/<id>/overdue` рассчитывает просрочку:
  - количество дней просрочки;
  - дневную ставку;
  - проценты за день;
  - общую сумму просроченных процентов;
  - остаток с учетом просрочки.

### Администрирование

- Есть роли `user`, `admin`, `superadmin`.
- Админ-панель доступна по `/admin`.
- Администратор видит пользователей, долги, платежи, логи и справочники.
- `superadmin` может менять роли, блокировать и удалять пользователей, управлять настройками, справочниками и экспортом.
- Есть impersonation: `superadmin` может временно войти как обычный пользователь для диагностики.
- Есть защита от удаления или понижения последнего `superadmin`.
- Есть экспорт CSV по пользователям, долгам и платежам.
- В логи активности записываются действия, IP-адрес и User-Agent.

### Авторизация

- Telegram Login Widget.
- Google OAuth 2.0 через Authlib.
- Локальный dev-вход для разработки: `/dev-login/user`, `/dev-login/admin`, `/dev-login/superadmin`.
- Тестовый вход `/test-login`.
- Аварийный админ-вход `/admin/login` с лимитом неудачных попыток и временной блокировкой.
- Заблокированный пользователь автоматически выходит из сессии и не может работать с приложением.

### Интерфейс

- Серверные HTML-шаблоны на Jinja.
- Bootstrap 5.
- Клиентский JavaScript в `static/js/app.js`.
- Темная и светлая тема.
- Форматирование денег в рублях через Jinja-фильтр `money`.
- Человекочитаемый вывод пустых значений через Jinja-фильтр `display`.

## Что добавили и улучшили

Этот README отражает актуальное состояние проекта после последних доработок.

### Ежемесячные расходы

- В модель `Expense` добавлены поля `is_monthly`, `monthly_group_id`, `generated_from_id`, `generated_for_month`.
- Добавлен сервис `app/services/monthly_expenses_service.py`.
- Добавлена генерация копий регулярного расхода от исходного месяца до целевого месяца.
- Добавлена защита от дублей по `monthly_group_id`, `generated_for_month` и фактической дате расхода.
- Добавлена обработка коротких месяцев.
- Добавлено включение ежемесячности при создании и редактировании расхода.
- Добавлено отключение ежемесячности без удаления уже созданной истории.
- Добавлена CLI-команда `generate-monthly-expenses`.
- Добавлена зависимость `python-dateutil`.
- Добавлены тесты для создания, редактирования, переноса дат и идемпотентности регулярных расходов.

### Google OAuth

- В модель `User` добавлены поля `google_id`, `email`, `avatar_url`.
- Добавлена авторизация через Google OAuth 2.0.
- При входе через Google приложение:
  - проверяет наличие `sub` и `email`;
  - требует подтвержденный email;
  - связывает Google ID с существующим пользователем по email, если такой пользователь уже есть;
  - создает нового пользователя, если привязки еще нет.
- Добавлены миграции для Google-полей.
- В `.env.example` добавлены переменные Google OAuth.

### Миграции и деплой

- Схема переведена на Flask-Migrate/Alembic как основной способ обновления базы.
- Добавлена базовая миграция `73459c8513a1`.
- Добавлены миграции для ипотек, логов с IP/User-Agent, Google OAuth и ежемесячных расходов.
- Добавлена merge-миграция `20260517_merge_heads`, объединяющая ветки Google OAuth и ежемесячных расходов.
- `deploy.sh` теперь различает пустую базу, старую базу без `alembic_version`, битую таблицу версий и нормальную Alembic-схему.
- `deploy.sh` не вызывает `flask db stamp head` для старых баз, а безопасно ставит только базовую ревизию `73459c8513a1`.
- Добавлена нормализация старых длинных revision ID в короткие revision ID, чтобы они помещались в стандартный MySQL-столбец `alembic_version.version_num`.
- Добавлены тесты миграционного графа и запрет на опасные операции вроде `op.drop_table` в миграциях.

### Локальная разработка

- Добавлен `create_app(config_overrides=None)` для тестов и конфигурационных переопределений.
- `run.py` использует пакетный объект `app`, а не старый монолит.
- Добавлена команда `copy-mysql-to-sqlite` для переноса данных из MySQL в уже мигрированную SQLite-базу.
- Добавлены тестовые режимы входа и fallback `LocalTestUser`, чтобы можно было смотреть интерфейс даже при проблемах с БД.

### Админка и безопасность

- Аварийный admin-login поддерживает `ADMIN_PASSWORD_HASH`.
- Добавлены `ADMIN_MAX_LOGIN_ATTEMPTS` и `ADMIN_LOCKOUT_MINUTES`.
- В активности сохраняются IP и User-Agent.
- Защищены операции с последним `superadmin`.
- Добавлены блокировка пользователей, impersonation и CSV-экспорт.

## Основные сценарии

### Для обычного пользователя

1. Войти через Telegram, Google или разрешенный тестовый вход.
2. Добавить активные долги: кредитные карты, рассрочки, ипотеки.
3. Указать остатки, минимальные платежи, ставки и даты следующих платежей.
4. Вносить платежи по долгам.
5. Добавлять доходы и расходы.
6. Отмечать аренду, подписки и другие повторяющиеся траты как ежемесячные.
7. Смотреть финансовую страницу и дневной бюджет.
8. Переносить закрытые долги в архив.

### Для администратора

1. Войти как `admin` или `superadmin`.
2. Проверить статистику пользователей, долгов, платежей и логов.
3. Найти пользователя по роли, статусу или поиску.
4. Заблокировать пользователя при необходимости.
5. Посмотреть последние действия пользователя.
6. Экспортировать CSV, если есть права `superadmin`.

### Для разработчика

1. Запустить приложение локально на SQLite или MySQL.
2. Включить `DEV_LOGIN_ENABLED=true` и `FLASK_DEBUG=true`.
3. Войти через `/dev-login/user`, `/dev-login/admin` или `/dev-login/superadmin`.
4. Применять миграции через `flask db upgrade`.
5. Проверять изменения тестами из `tests/`.

### Для владельца сервера

1. Настроить `.env`, MySQL, systemd и Nginx.
2. Применить миграции.
3. Настроить HTTPS.
4. Настроить Telegram-домен в BotFather.
5. Обновлять проект через `deploy.sh`.
6. Делать регулярные резервные копии БД.

## Технологии

- Python 3.9+
- Flask 3
- Flask-SQLAlchemy
- SQLAlchemy 2
- Flask-Migrate / Alembic
- Flask-Login
- Flask-WTF / CSRF
- Authlib
- requests
- PyMySQL
- cryptography
- python-dotenv
- python-dateutil
- Waitress
- Bootstrap 5
- Jinja templates
- JavaScript
- unittest

## Архитектура

Проект построен как модульное Flask-приложение.

### Точка входа

- `run.py` запускает production-сервер через Waitress.
- `app/__init__.py` создает приложение, подключает расширения, регистрирует маршруты, Jinja-фильтры и CLI-команды.
- Для Flask CLI используется `FLASK_APP=run.py`.

### Расширения

- `extensions.py` содержит общий объект `db`.
- `Flask-Login` управляет пользовательскими сессиями.
- `Flask-Migrate` управляет миграциями.
- `Flask-WTF` включает CSRF-защиту.

### Маршруты

Маршруты разделены по областям:

- `app/routes/auth.py` - вход, выход, Telegram, Google, dev/test/admin-login.
- `app/routes/admin.py` - админ-панель.
- `app/routes/debts.py` - JSON API долгов.
- `app/routes/payments.py` - JSON API платежей по долгам.
- `app/routes/incomes.py` - страницы доходов.
- `app/routes/expenses.py` - страницы расходов и регулярных расходов.
- `app/routes/main.py` - дашборд, финансы, ипотека, архив, seed-данные.

### Сервисы

Сервисный слой выносит бизнес-логику из маршрутов:

- `app/services/debt_service.py` - получение пользовательского долга и demo-долги для локального тестового режима.
- `app/services/payment_service.py` - внесение платежа и пересчет остатка.
- `app/services/finance_summary_service.py` - месячная финансовая сводка.
- `app/services/monthly_expenses_service.py` - генерация регулярных расходов.
- `app/services/telegram_auth_service.py` - проверка подписи Telegram Login Widget.

### Утилиты

`app/utils.py` содержит:

- списки категорий доходов и расходов;
- списки типов справочников;
- значения настроек по умолчанию;
- парсинг денежных значений и дат;
- форматирование денег;
- группировку записей по месяцам;
- чтение и запись настроек;
- запись активности;
- декораторы `admin_required` и `superadmin_required`.

### Legacy-файлы

- `legacy_app.py` и `legacy_models.py` оставлены как исторический монолитный снимок.
- Они не являются основной точкой входа.
- Новые изменения должны вноситься в пакет `app/`.
- Production, Flask CLI и миграции должны использовать `run.py`.

## Структура проекта

```text
officium/
├── app/
│   ├── __init__.py
│   ├── forms.py
│   ├── models.py
│   ├── utils.py
│   ├── routes/
│   │   ├── __init__.py
│   │   ├── admin.py
│   │   ├── auth.py
│   │   ├── debts.py
│   │   ├── expenses.py
│   │   ├── incomes.py
│   │   ├── main.py
│   │   └── payments.py
│   └── services/
│       ├── __init__.py
│       ├── debt_service.py
│       ├── finance_summary_service.py
│       ├── monthly_expenses_service.py
│       ├── payment_service.py
│       └── telegram_auth_service.py
├── migrations/
│   ├── alembic.ini
│   ├── env.py
│   ├── script.py.mako
│   └── versions/
├── static/
│   ├── css/
│   │   └── style.css
│   └── js/
│       └── app.js
├── templates/
│   ├── admin_dashboard.html
│   ├── admin_dictionaries.html
│   ├── admin_export.html
│   ├── admin_logs.html
│   ├── admin_login.html
│   ├── admin_settings.html
│   ├── admin_user_detail.html
│   ├── admin_users.html
│   ├── archive.html
│   ├── base.html
│   ├── expenses.html
│   ├── finance.html
│   ├── incomes.html
│   ├── index.html
│   ├── login.html
│   ├── mortgages.html
│   └── overdue_interest.html
├── tests/
│   ├── __init__.py
│   ├── test_app_import.py
│   ├── test_migrations.py
│   ├── test_monthly_expenses.py
│   └── test_schema_contract.py
├── .env.example
├── .gitignore
├── check_routes.py
├── config.py
├── deploy.sh
├── dev.db
├── extensions.py
├── init_db.sql
├── legacy_app.py
├── legacy_models.py
├── MONTHLY_EXPENSES_GUIDE.md
├── README.md
├── requirements.txt
├── run.py
├── start.bat
└── test_results.txt
```

## Модель данных

### `User`

Пользователь приложения.

Основные поля:

- `telegram_id` - обязательный уникальный идентификатор. Для Google-пользователей генерируется техническое отрицательное значение.
- `username`, `first_name`, `last_name`, `photo_url` - данные Telegram или Google.
- `role` - `user`, `admin` или `superadmin`.
- `is_blocked` - блокировка доступа.
- `last_login_ip`, `last_user_agent`, `login_count` - данные входов.
- `google_id`, `email`, `avatar_url` - данные Google OAuth.

Связи:

- `debts`
- `incomes`
- `expenses`
- `activity_logs`

### `Debt`

Долг или кредитный продукт.

Основные поля:

- `bank_name`
- `debt_type`
- `product_name`
- `total_amount`
- `remaining_amount`
- `minimum_payment`
- `interest_rate`
- `next_payment_date`
- `comment`
- `status`
- `user_id`

Статусы:

- `active`
- `archived`

Типы:

- `credit_card`
- `split`
- `mortgage`

### `Payment`

Платеж по долгу.

Основные поля:

- `debt_id`
- `amount`
- `payment_date`
- `comment`
- `remaining_after_payment`
- `created_at`

### `Income`

Доход пользователя.

Основные поля:

- `user_id`
- `amount`
- `category`
- `source`
- `income_date`
- `comment`

Категории:

- `salary`
- `advance`
- `side_job`
- `debt_return`
- `bonus`
- `scholarship`
- `other`

### `Expense`

Расход пользователя.

Основные поля:

- `user_id`
- `amount`
- `category`
- `title`
- `expense_date`
- `payment_method`
- `comment`
- `is_monthly`
- `monthly_group_id`
- `generated_from_id`
- `generated_for_month`

Категории:

- `products`
- `transport`
- `communication`
- `rent`
- `loans`
- `entertainment`
- `health`
- `education`
- `clothing`
- `subscriptions`
- `other`

### `AppSetting`

Настройки приложения, управляемые из админки.

Примеры настроек:

- `app_name`
- `default_currency`
- `registration_enabled`
- `telegram_login_enabled`
- `debt_limit_per_user`
- `archive_enabled`
- `export_enabled`
- `payment_warning_days`
- `urgent_payment_days`
- `overdue_after_date`

### `DictionaryEntry`

Справочники для админки.

Типы справочников:

- `bank`
- `debt_type`
- `debt_category`
- `status`
- `comment_template`
- `interest_rate`
- `product_type`

### `ActivityLog`

Журнал активности.

Поля:

- `user_id`
- `action`
- `entity_type`
- `entity_id`
- `description`
- `ip_address`
- `user_agent`
- `created_at`

## Маршруты и API

### Пользовательские страницы

| Метод | Путь | Назначение |
| --- | --- | --- |
| `GET` | `/` | Главный дашборд долгов и месячной финансовой сводки. |
| `GET` | `/finance` | Подробная финансовая страница с выбором года и месяца. |
| `GET` | `/mortgages` | Отдельная страница ипотечных долгов. |
| `GET` | `/debts/<debt_id>/overdue` | Расчет процентов по просроченному долгу. |
| `GET` | `/archive` | Архив закрытых долгов. |
| `POST` | `/api/init-db` | Заполнение demo-данными для текущего пользователя, если данных еще нет. |

### Доходы

| Метод | Путь | Назначение |
| --- | --- | --- |
| `GET`, `POST` | `/incomes` | Список, создание и форма доходов. |
| `GET`, `POST` | `/incomes/edit/<income_id>` | Редактирование дохода. |
| `POST` | `/incomes/delete/<income_id>` | Удаление дохода. |

### Расходы

| Метод | Путь | Назначение |
| --- | --- | --- |
| `GET`, `POST` | `/expenses` | Список, создание и форма расходов. |
| `GET`, `POST` | `/expenses/edit/<expense_id>` | Редактирование расхода. |
| `POST` | `/expenses/delete/<expense_id>` | Удаление расхода. |

### Долги

| Метод | Путь | Назначение |
| --- | --- | --- |
| `GET` | `/api/debts` | Список долгов текущего пользователя. Поддерживает фильтры `status`, `bank`, `type`. |
| `POST` | `/api/debts` | Создание долга. |
| `GET` | `/api/debts/<debt_id>` | Получение одного долга. |
| `PUT` | `/api/debts/<debt_id>` | Обновление долга. |
| `POST` | `/api/debts/<debt_id>/archive` | Перенос долга в архив. |
| `POST` | `/api/debts/<debt_id>/restore` | Восстановление долга из архива. |
| `DELETE` | `/api/debts/<debt_id>/delete` | Удаление долга. |

### Платежи

| Метод | Путь | Назначение |
| --- | --- | --- |
| `GET` | `/api/debts/<debt_id>/payments` | История платежей по долгу. |
| `POST` | `/api/debts/<debt_id>/payments` | Внесение платежа по долгу. |

### Авторизация

| Метод | Путь | Назначение |
| --- | --- | --- |
| `GET` | `/login` | Страница входа. |
| `GET` | `/telegram-login` | Callback Telegram Login Widget. |
| `GET` | `/auth/google` | Старт Google OAuth. |
| `GET` | `/auth/google/callback` | Callback Google OAuth. |
| `GET` | `/dev-login/<role>` | Dev-вход под ролью `user`, `admin` или `superadmin`. |
| `GET` | `/dev-logout` | Выход из dev-режима. |
| `GET` | `/test-login` | Тестовый вход. |
| `GET`, `POST` | `/admin/login` | Аварийный админ-вход. |
| `GET` | `/admin/stop-impersonate` | Завершение impersonation. |
| `GET` | `/logout` | Выход. |

### Админ-панель

| Метод | Путь | Назначение |
| --- | --- | --- |
| `GET` | `/admin` | Админ-дашборд. |
| `GET`, `POST` | `/admin/settings` | Настройки приложения. Только `superadmin`. |
| `GET`, `POST` | `/admin/dictionaries` | Справочники. Только `superadmin`. |
| `POST` | `/admin/dictionaries/<entry_id>/delete` | Удаление элемента справочника. Только `superadmin`. |
| `GET` | `/admin/users` | Список пользователей с фильтрами. |
| `GET`, `POST` | `/admin/users/<user_id>` | Карточка пользователя и действия над ним. |
| `POST` | `/admin/impersonate/test` | Вход как тестовый пользователь. Только `superadmin`. |
| `POST` | `/admin/impersonate/<user_id>` | Вход как выбранный пользователь. Только `superadmin`. |
| `GET` | `/admin/logs` | Последние 100 логов активности. |
| `GET` | `/admin/export` | Страница экспорта. Только `superadmin`. |
| `POST` | `/admin/export/<export_type>.csv` | CSV-экспорт `users`, `debts` или `payments`. Только `superadmin`. |

## Переменные окружения

Файл `.env.example` содержит актуальный шаблон настроек.

### Основные

| Переменная | Обязательна | Пример | Описание |
| --- | --- | --- | --- |
| `SECRET_KEY` | Да | `change-me` | Секрет Flask для сессий и CSRF. На production должен быть уникальным. |
| `FLASK_DEBUG` | Нет | `false` | Включает debug-режим Flask. |
| `HOST` | Нет | `0.0.0.0` | Хост для `run.py`. |
| `PORT` | Нет | `5000` | Порт для `run.py`. |

### База данных

| Переменная | Обязательна | Пример | Описание |
| --- | --- | --- | --- |
| `DATABASE_URL` | Нет | `mysql+pymysql://user:pass@localhost/debt_manager?charset=utf8mb4` | Полный URI БД. Если задан, имеет приоритет над отдельными `DB_*`. |
| `DB_ENGINE` | Нет | `mysql` или `sqlite` | Тип БД. |
| `SQLITE_PATH` | Нет | `dev.db` | Путь к SQLite-файлу относительно корня проекта. |
| `DB_HOST` | Для MySQL | `localhost` | Хост MySQL/MariaDB. |
| `DB_PORT` | Для MySQL | `3306` | Порт MySQL/MariaDB. |
| `DB_USER` | Для MySQL | `debt_user` | Пользователь БД. |
| `DB_PASSWORD` | Для MySQL | `strong_password` | Пароль пользователя БД. |
| `DB_NAME` | Для MySQL | `debt_manager` | Имя базы данных. |
| `DEV_SQLITE_COPY_FROM_MYSQL` | Нет | `false` | Разрешает CLI-команду копирования данных из MySQL в SQLite. |
| `DEV_SQLITE_COPY_SOURCE_URL` | Нет | `mysql+pymysql://...` | Явный MySQL-источник для копирования в SQLite. |

### Telegram

| Переменная | Обязательна | Пример | Описание |
| --- | --- | --- | --- |
| `TELEGRAM_BOT_TOKEN` | Да для Telegram | `123456:ABC-DEF...` | Токен Telegram-бота. |
| `TELEGRAM_BOT_USERNAME` | Да для Telegram | `YourBotUsername` | Username Telegram-бота без `@`. |
| `ADMIN_TELEGRAM_IDS` | Нет | `12345,67890` | Telegram ID пользователей, которые автоматически становятся `superadmin`. |

### Google OAuth

| Переменная | Обязательна | Пример | Описание |
| --- | --- | --- | --- |
| `GOOGLE_LOGIN_ENABLED` | Нет | `true` | Включает вход через Google. |
| `GOOGLE_CLIENT_ID` | Да для Google | `...apps.googleusercontent.com` | Client ID из Google Cloud Console. |
| `GOOGLE_CLIENT_SECRET` | Да для Google | `secret` | Client Secret из Google Cloud Console. |
| `GOOGLE_REDIRECT_URI` | Нет | `http://127.0.0.1:5000/auth/google/callback` | Redirect URI. В коде callback строится через `url_for(..., _external=True)`, поэтому публичный адрес приложения должен совпадать с настройками Google. |

### Dev/test-вход

| Переменная | Обязательна | Пример | Описание |
| --- | --- | --- | --- |
| `DEV_LOGIN_ENABLED` | Нет | `false` | Включает `/dev-login/<role>`, но только при `FLASK_DEBUG=true`. |
| `TEST_USER_ENABLED` | Нет | `false` | Включает `/test-login`. |
| `TEST_USER_TELEGRAM_ID` | Нет | `-999999999999` | Telegram ID тестового пользователя. |
| `TEST_USER_USERNAME` | Нет | `testuser` | Username тестового пользователя. |
| `TEST_USER_FIRST_NAME` | Нет | `Тестовый` | Имя тестового пользователя. |
| `TEST_USER_LAST_NAME` | Нет | `Пользователь` | Фамилия тестового пользователя. |
| `TEST_USER_ROLE` | Нет | `user` | Роль тестового пользователя. |

### Аварийный админ-вход

| Переменная | Обязательна | Пример | Описание |
| --- | --- | --- | --- |
| `ADMIN_LOGIN_ENABLED` | Нет | `false` | Включает `/admin/login`. |
| `ADMIN_PASSWORD` | Нет | `strong_password` | Прямой пароль для аварийного входа. Используйте только локально или временно. |
| `ADMIN_PASSWORD_HASH` | Нет | `pbkdf2:...` | Хеш пароля. Имеет приоритет над `ADMIN_PASSWORD`. |
| `ADMIN_MAX_LOGIN_ATTEMPTS` | Нет | `5` | Количество неудачных попыток до временной блокировки. |
| `ADMIN_LOCKOUT_MINUTES` | Нет | `15` | Длительность временной блокировки в минутах. |

## Локальный запуск

### Вариант для Windows

```powershell
git clone https://github.com/UnicornisIT/debt_manager.git
cd debt_manager
python -m venv .venv
.venv\Scripts\activate
python -m pip install --upgrade pip
pip install -r requirements.txt
Copy-Item .env.example .env
```

Настройте `.env`.

Для SQLite:

```env
SECRET_KEY=dev-secret-change-me
FLASK_DEBUG=true
DB_ENGINE=sqlite
SQLITE_PATH=dev.db
DEV_LOGIN_ENABLED=true
GOOGLE_LOGIN_ENABLED=false
```

Примените миграции и запустите приложение:

```powershell
$env:FLASK_APP = 'run.py'
flask db upgrade
python -m flask run
```

Откройте:

```text
http://127.0.0.1:5000
```

Для быстрого старта на Windows также есть `start.bat`.

### Вариант для Linux/macOS

```bash
git clone https://github.com/UnicornisIT/debt_manager.git
cd debt_manager
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
cp .env.example .env
```

Настройте `.env`, затем:

```bash
export FLASK_APP=run.py
flask db upgrade
python -m flask run
```

### Dev-вход после запуска

Для локальной разработки:

```env
FLASK_DEBUG=true
DEV_LOGIN_ENABLED=true
```

Доступные адреса:

```text
http://127.0.0.1:5000/dev-login/user
http://127.0.0.1:5000/dev-login/admin
http://127.0.0.1:5000/dev-login/superadmin
```

Не включайте `DEV_LOGIN_ENABLED` на production.

## База данных

Проект поддерживает SQLite для локальной разработки и MySQL/MariaDB для production.

### SQLite

Минимальная локальная конфигурация:

```env
DB_ENGINE=sqlite
SQLITE_PATH=dev.db
```

После изменения:

```bash
export FLASK_APP=run.py
flask db upgrade
```

### MySQL / MariaDB

Установка на Ubuntu/Debian:

```bash
sudo apt update
sudo apt install mysql-server -y
sudo mysql
```

Создание базы:

```sql
CREATE DATABASE debt_manager CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE USER 'debt_user'@'localhost' IDENTIFIED BY 'strong_password';
GRANT ALL PRIVILEGES ON debt_manager.* TO 'debt_user'@'localhost';
FLUSH PRIVILEGES;
EXIT;
```

Настройки `.env`:

```env
DB_ENGINE=mysql
DB_HOST=localhost
DB_PORT=3306
DB_USER=debt_user
DB_PASSWORD=strong_password
DB_NAME=debt_manager
```

Проверка подключения:

```bash
python -c "from sqlalchemy import create_engine; print(create_engine('mysql+pymysql://debt_user:strong_password@localhost:3306/debt_manager?charset=utf8mb4').url)"
```

### Копирование MySQL в SQLite для разработки

Команда полезна, если нужно локально работать с копией данных.

1. Включите SQLite как активную БД:

```env
DB_ENGINE=sqlite
SQLITE_PATH=dev.db
```

2. Разрешите копирование:

```env
DEV_SQLITE_COPY_FROM_MYSQL=true
DEV_SQLITE_COPY_SOURCE_URL=mysql+pymysql://debt_user:strong_password@localhost:3306/debt_manager?charset=utf8mb4
```

3. Примените миграции к SQLite:

```bash
export FLASK_APP=run.py
flask db upgrade
```

4. Скопируйте данные:

```bash
flask copy-mysql-to-sqlite
```

Команда копирует данные только если SQLite еще не содержит пользователей.

## Миграции

Основной способ создания и обновления схемы:

```bash
export FLASK_APP=run.py
flask db upgrade
```

Не используйте `db.create_all()` для обновления существующей БД. Он не добавляет новые столбцы в уже созданные таблицы и не заменяет миграции.

### Создание новой миграции

После изменения моделей:

```bash
export FLASK_APP=run.py
flask db migrate -m "Описание изменения"
flask db upgrade
```

### Проверка состояния

```bash
export FLASK_APP=run.py
flask db current
flask db heads
flask db history
```

### Текущая цепочка миграций

| Revision | Down revision | Назначение |
| --- | --- | --- |
| `73459c8513a1` | `None` | Базовая схема. |
| `20260502_mortgage` | `73459c8513a1` | Добавление поддержки `Debt.debt_type = mortgage`. |
| `20260502_log_ip_ua` | `20260502_mortgage` | Добавление `activity_logs.ip_address` и `activity_logs.user_agent`. |
| `20260503_debt_type` | `20260502_log_ip_ua` | Синхронизация MySQL/MariaDB enum `debts.debt_type`. |
| `acd5bddc3168` | `20260503_debt_type` | Добавление `users.google_id`. |
| `e49a6c3dc4b8` | `acd5bddc3168` | Добавление `users.email` и `users.avatar_url`. |
| `20260517_monthly_expenses` | `20260503_debt_type` | Поля регулярных расходов в `expenses`. |
| `20260517_merge_heads` | `e49a6c3dc4b8`, `20260517_monthly_expenses` | Объединение веток Google OAuth и ежемесячных расходов. |

У проекта есть правило: `revision` должен быть короче 32 символов, чтобы помещаться в стандартный MySQL/MariaDB столбец `alembic_version.version_num`.

### Как работает `deploy.sh` с миграциями

`deploy.sh` выполняет preflight-проверку БД перед `flask db upgrade`.

Он различает четыре состояния:

- пустая база - сразу выполняется `flask db upgrade`;
- старая база с таблицами приложения, но без `alembic_version` - выполняется `flask db stamp 73459c8513a1`, затем `flask db upgrade`;
- таблица `alembic_version` без `version_num` - если таблица пустая, скрипт добавляет недостающий столбец и выбирает безопасный путь;
- нормальная база с `alembic_version.version_num` - выполняется обычный `flask db upgrade`.

Скрипт не выполняет `flask db stamp head` для старой базы, потому что это может скрыть непримененные миграции.

## Ежемесячные расходы

Ежемесячные расходы нужны для регулярных платежей: аренда, связь, подписки, обучение, обязательные сервисы.

### Как включить

На странице `/expenses`:

1. Заполните сумму, категорию, название и дату.
2. Включите переключатель ежемесячного расхода.
3. Сохраните расход.

Если дата расхода в прошлом, приложение создаст недостающие записи до текущего месяца.

Пример:

- сегодня май 2026;
- пользователь создает расход "Аренда" с датой `2026-03-15`;
- приложение сохранит записи за март, апрель и май;
- у всех записей будет общий `monthly_group_id`.

### Как редактировать

При редактировании можно:

- изменить сумму;
- изменить категорию;
- изменить название;
- изменить дату;
- изменить способ оплаты;
- изменить комментарий;
- включить ежемесячность для старого обычного расхода;
- отключить ежемесячность для всей группы.

Если для выбранного месяца в этой группе уже есть запись, приложение не даст создать дубль.

### Как генерировать вручную

Для текущего месяца:

```bash
export FLASK_APP=run.py
flask generate-monthly-expenses
```

Для конкретного пользователя:

```bash
flask generate-monthly-expenses --user-id 123
```

Для конкретного месяца:

```bash
flask generate-monthly-expenses --month 2026-06
```

Для конкретного пользователя и месяца:

```bash
flask generate-monthly-expenses --user-id 123 --month 2026-06
```

Команда выводит:

- сколько расходов создано;
- сколько пропущено как уже существующие;
- были ли ошибки.

### Как запускать по расписанию

Можно запускать команду в начале каждого месяца через cron или systemd timer.

Пример cron:

```cron
5 0 1 * * cd /var/www/debt_manager && . venv/bin/activate && FLASK_APP=run.py flask generate-monthly-expenses
```

Даже если команда запустится несколько раз, дубликаты не появятся.

### Технические правила генерации

- Источник группы выбирается из записей без `generated_from_id`; если таких нет, берется самая ранняя запись группы.
- `monthly_group_id` связывает все записи одной регулярной траты.
- `generated_for_month` хранит месяц в формате `YYYY-MM`.
- `generated_from_id` указывает на исходную запись.
- Дата копии сохраняет исходный день месяца, если он существует.
- Если день не существует, используется последний день месяца.
- Проверка существующей записи смотрит и на `generated_for_month`, и на фактическую `expense_date`.

## Авторизация

### Telegram Login

1. Создайте бота через [BotFather](https://t.me/BotFather).
2. Получите `TELEGRAM_BOT_TOKEN`.
3. Настройте домен через `/setdomain`.
4. Укажите `TELEGRAM_BOT_USERNAME` и `TELEGRAM_BOT_TOKEN` в `.env`.
5. Убедитесь, что production работает по HTTPS.

Telegram Login Widget требует HTTPS и разрешенный домен.

### Google OAuth

1. Откройте [Google Cloud Console](https://console.cloud.google.com/).
2. Создайте проект или выберите существующий.
3. Настройте OAuth consent screen.
4. Создайте OAuth 2.0 Client ID типа Web application.
5. Добавьте redirect URI.

Локально:

```text
http://127.0.0.1:5000/auth/google/callback
```

Production:

```text
https://your-domain.com/auth/google/callback
```

Настройки `.env`:

```env
GOOGLE_LOGIN_ENABLED=true
GOOGLE_CLIENT_ID=your-google-client-id.apps.googleusercontent.com
GOOGLE_CLIENT_SECRET=your-google-client-secret
GOOGLE_REDIRECT_URI=http://127.0.0.1:5000/auth/google/callback
```

Важно: адрес, который видит Flask при `url_for(..., _external=True)`, должен совпадать с разрешенным redirect URI в Google.

### Dev-вход

Dev-вход работает только если включены оба условия:

```env
FLASK_DEBUG=true
DEV_LOGIN_ENABLED=true
```

Адреса:

```text
/dev-login/user
/dev-login/admin
/dev-login/superadmin
/dev-logout
```

### Тестовый вход

Тестовый вход включается так:

```env
TEST_USER_ENABLED=true
```

Адрес:

```text
/test-login
```

Если БД недоступна, приложение может использовать локальный объект `LocalTestUser`, чтобы интерфейс все равно открылся для просмотра.

### Аварийный админ-вход

Включение:

```env
ADMIN_LOGIN_ENABLED=true
ADMIN_PASSWORD_HASH=pbkdf2:...
ADMIN_MAX_LOGIN_ATTEMPTS=5
ADMIN_LOCKOUT_MINUTES=15
```

Адрес:

```text
/admin/login
```

Лучше использовать `ADMIN_PASSWORD_HASH`, а не `ADMIN_PASSWORD`.

## Роли и админ-панель

### Роли

| Роль | Возможности |
| --- | --- |
| `user` | Работает со своими долгами, платежами, доходами и расходами. |
| `admin` | Имеет доступ к админ-панели, пользователям и логам. |
| `superadmin` | Управляет ролями, настройками, справочниками, экспортом и impersonation. |

### Как назначить `superadmin`

Через `.env`:

```env
ADMIN_TELEGRAM_IDS=12345,67890
```

Или через CLI:

```bash
export FLASK_APP=run.py
flask create-superadmin <telegram_id>
```

Пользователь с этим Telegram ID должен уже существовать в базе.

### Возможности админки

- Дашборд со статистикой.
- Последние логи активности.
- Управление настройками приложения.
- Управление справочниками.
- Список пользователей с фильтрами по роли и статусу.
- Поиск пользователей по username, имени, фамилии и Telegram ID.
- Просмотр карточки пользователя.
- Блокировка и разблокировка.
- Назначение `admin` и `superadmin`.
- Понижение до `user`.
- Удаление пользователя.
- Защита последнего `superadmin`.
- Impersonation обычного пользователя.
- Экспорт CSV.

## Тестирование

Проект использует `unittest`.

Запуск всех тестов:

```bash
python -m unittest discover -v
```

Точечные проверки:

```bash
python -m unittest tests.test_app_import -v
python -m unittest tests.test_schema_contract -v
python -m unittest tests.test_migrations -v
python -m unittest tests.test_monthly_expenses -v
```

Что покрыто тестами:

- импорт приложения и `create_app`;
- связь `run.py` с пакетным объектом `app`;
- наличие `mortgage` в enum долгов;
- подпись ипотечного типа в `Debt.to_dict()`;
- наличие IP/User-Agent в `ActivityLog`;
- длина revision ID для MySQL;
- граф миграций и единственная head-ревизия;
- отсутствие опасных `drop_table`/`drop_all` в миграциях;
- preflight-логика `deploy.sh`;
- создание и редактирование регулярных расходов;
- генерация пропущенных месяцев;
- идемпотентность генерации;
- перенос дат на короткие месяцы;
- отключение ежемесячности без удаления истории.

## Развертывание

### Подготовка VPS

```bash
sudo apt update
sudo apt upgrade -y
sudo apt install python3 python3-venv python3-pip git nginx mysql-server -y
```

### Установка проекта

```bash
sudo mkdir -p /var/www/debt_manager
sudo chown -R $USER:$USER /var/www/debt_manager
cd /var/www/debt_manager
git clone https://github.com/UnicornisIT/debt_manager.git .
python3 -m venv venv
source venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
cp .env.example .env
```

Настройте `.env`, создайте MySQL-базу и примените миграции:

```bash
export FLASK_APP=run.py
flask db upgrade
```

### Проверка Waitress

```bash
python run.py
```

По умолчанию `run.py` слушает:

```text
0.0.0.0:5000
```

Можно переопределить:

```bash
HOST=127.0.0.1 PORT=5000 python run.py
```

### systemd

Пример `/etc/systemd/system/debt_manager.service`:

```ini
[Unit]
Description=officium Flask Application
After=network.target mysql.service

[Service]
User=www-data
Group=www-data
WorkingDirectory=/var/www/debt_manager
EnvironmentFile=/var/www/debt_manager/.env
ExecStart=/var/www/debt_manager/venv/bin/python /var/www/debt_manager/run.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

Команды:

```bash
sudo systemctl daemon-reload
sudo systemctl enable debt_manager
sudo systemctl start debt_manager
sudo systemctl status debt_manager
```

### Nginx

Пример `/etc/nginx/sites-available/debt_manager`:

```nginx
server {
    listen 80;
    server_name example.com www.example.com;

    client_max_body_size 20M;

    location /static/ {
        alias /var/www/debt_manager/static/;
        expires 30d;
        add_header Cache-Control "public";
    }

    location / {
        proxy_pass http://127.0.0.1:5000;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

Включение:

```bash
sudo ln -s /etc/nginx/sites-available/debt_manager /etc/nginx/sites-enabled/
sudo nginx -t
sudo systemctl restart nginx
```

### SSL / Certbot

```bash
sudo apt install certbot python3-certbot-nginx -y
sudo certbot --nginx -d example.com -d www.example.com
```

После выпуска сертификата:

- обновите домен в BotFather;
- проверьте redirect URI в Google Cloud Console;
- проверьте, что `X-Forwarded-Proto` передается в Flask через Nginx.

## Обновление проекта

Рекомендуемый способ на VPS:

```bash
cd /var/www/debt_manager
chmod +x deploy.sh
./deploy.sh
```

`deploy.sh`:

- подтягивает код из Git;
- активирует или создает virtualenv;
- устанавливает зависимости;
- выставляет `FLASK_APP=run.py`;
- проверяет состояние Alembic;
- при необходимости ставит baseline-ревизию;
- применяет миграции;
- перезапускает systemd-сервис;
- показывает статус сервиса.

Переменные для `deploy.sh`:

| Переменная | Значение по умолчанию | Назначение |
| --- | --- | --- |
| `APP_DIR` | `/var/www/debt_manager` | Каталог приложения. |
| `DEPLOY_BRANCH` | `master` | Ветка для `git pull`. |
| `SERVICE_NAME` | `debt_manager` | Имя systemd-сервиса. |

Ручной вариант:

```bash
cd /var/www/debt_manager
git pull origin master
source venv/bin/activate
pip install -r requirements.txt
export FLASK_APP=run.py
flask db upgrade
sudo systemctl restart debt_manager
sudo systemctl status debt_manager
```

Если на сервере есть локальные изменения, сначала проверьте:

```bash
git status
```

## Резервное копирование

### MySQL backup

```bash
mysqldump -u debt_user -p debt_manager > backup_debt_manager.sql
```

### MySQL restore

```bash
mysql -u debt_user -p debt_manager < backup_debt_manager.sql
```

### SQLite backup

Для SQLite достаточно скопировать файл, указанный в `SQLITE_PATH`, когда приложение остановлено или когда нет активной записи в БД.

## Частые ошибки

### `pymysql.err.OperationalError: Can't connect to MySQL`

Проверьте:

- запущен ли MySQL/MariaDB;
- верны ли `DB_HOST`, `DB_PORT`, `DB_USER`, `DB_PASSWORD`, `DB_NAME`;
- существует ли база;
- есть ли права у пользователя.

### `Table 'debt_manager.users' doesn't exist`

Примените миграции:

```bash
export FLASK_APP=run.py
flask db upgrade
```

### `Table 'app_settings' already exists`

Обычно означает, что база была создана до Alembic.

Используйте обновленный `deploy.sh`, либо вручную:

```bash
export FLASK_APP=run.py
flask db stamp 73459c8513a1
flask db upgrade
```

Не используйте `flask db stamp head` для старой базы без понимания, какие миграции уже применены.

### `Unknown column 'ip_address' in 'field list'`

Схема не обновлена до миграции `20260502_log_ip_ua`.

```bash
export FLASK_APP=run.py
flask db upgrade
```

### `Data truncated for column 'debt_type'`

Старый MySQL enum `debts.debt_type` не знает значение `mortgage`.

```bash
export FLASK_APP=run.py
flask db upgrade
```

### `Data too long for column 'version_num' at row 1`

В `alembic_version.version_num` попал слишком длинный revision ID.

В проекте revision ID должны быть короче 32 символов. Обновленный `deploy.sh` умеет нормализовать известные старые alias-значения, но неизвестные длинные значения нужно разбирать вручную.

### Telegram Login Widget показывает `Username invalid`

Проверьте:

- `TELEGRAM_BOT_USERNAME`;
- токен бота;
- домен в BotFather;
- HTTPS;
- отсутствие `@` в username бота внутри `.env`.

### Google OAuth возвращает ошибку redirect URI

Проверьте:

- redirect URI в Google Cloud Console;
- публичный домен приложения;
- настройки Nginx `Host` и `X-Forwarded-Proto`;
- `GOOGLE_LOGIN_ENABLED=true`;
- корректность `GOOGLE_CLIENT_ID` и `GOOGLE_CLIENT_SECRET`.

### Dev-вход не отображается

Проверьте:

```env
FLASK_DEBUG=true
DEV_LOGIN_ENABLED=true
```

Dev-вход специально скрыт, если Flask не в debug-режиме.

### Ежемесячные расходы не создаются

Проверьте:

- применена ли миграция `20260517_monthly_expenses`;
- включен ли флаг ежемесячности у расхода;
- есть ли `monthly_group_id`;
- запускается ли `flask generate-monthly-expenses`;
- не создана ли запись для этого месяца уже ранее.

### Темная тема не применяется

Очистите `localStorage` браузера и перезагрузите страницу.

### Static-файлы не загружаются на сервере

Проверьте Nginx-блок:

```nginx
location /static/ {
    alias /var/www/debt_manager/static/;
}
```

### `permission denied` в `/var/www/debt_manager`

Проверьте владельца каталога, пользователя systemd-сервиса и права на virtualenv, `.env`, static-файлы и каталог проекта.

## Безопасность

Production-чеклист:

- `FLASK_DEBUG=false`.
- `DEV_LOGIN_ENABLED=false`.
- `TEST_USER_ENABLED=false`, если тестовый вход не нужен публично.
- `SECRET_KEY` уникальный и длинный.
- `.env` не коммитится.
- MySQL-пользователь отдельный и с минимально нужными правами.
- Включен HTTPS.
- Telegram-домен настроен через BotFather.
- Google redirect URI соответствует production-домену.
- `ADMIN_LOGIN_ENABLED=false`, если аварийный вход не нужен.
- Если аварийный вход нужен, используйте `ADMIN_PASSWORD_HASH`, а не открытый пароль.
- `ADMIN_MAX_LOGIN_ATTEMPTS` и `ADMIN_LOCKOUT_MINUTES` настроены.
- `ADMIN_TELEGRAM_IDS` содержит только доверенные Telegram ID.
- Регулярно делаются backup БД.
- Перед деплоем на production запускаются тесты.
- Миграции применяются через Alembic, без ручного удаления таблиц.

## Полезные команды

```bash
export FLASK_APP=run.py
flask db current
flask db heads
flask db history
flask db upgrade
flask generate-monthly-expenses
python -m unittest discover -v
sudo systemctl status debt_manager
sudo journalctl -u debt_manager -f
sudo journalctl -u debt_manager -n 100
sudo nginx -t
sudo systemctl restart debt_manager
```

## Дополнительная документация

- `MONTHLY_EXPENSES_GUIDE.md` - отдельное руководство по функциональности ежемесячных расходов.
- `migrations/README` - стандартная информация Alembic.
- `test_results.txt` - сохраненный вывод одного из запусков тестов.

