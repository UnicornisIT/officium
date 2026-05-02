# ДолгТрекер / Debt Manager

## Описание

Debt Manager — это веб-приложение для учёта долгов, платежей, доходов и расходов. Оно предназначено для частных пользователей, которые хотят видеть:

- активные долги и остатки по ним;
- ближайшие платежи, просрочки и прогресс погашения;
- доходы, расходы и свободный остаток;
- архив закрытых долгов;
- управление в единой веб-панели.

Проект реализован на Flask с авторизацией через Telegram Login Widget и поддержкой локальной разработки.

## Возможности

- Авторизация через Telegram Login Widget.
- Dev-вход для локальной разработки без Telegram.
- Роли `user`, `admin`, `superadmin`.
- Дашборд долгов с фильтрами, поиском и картами долгов.
- Типы долгов: `credit_card`, `split`, `mortgage`.
- Добавление, редактирование и архивация долгов.
- Внесение и история платежей по долгу.
- Учёт доходов и расходов.
- Финансовая страница с месячной статистикой.
- Тёмная/светлая тема.
- Админ-панель с управлением пользователями, логами, настройками и экспортом CSV.
- Логи активности пользователей.

## Технологии

- Python 3.9+
- Flask
- Flask-SQLAlchemy
- Flask-Migrate / Alembic
- Flask-Login
- Flask-WTF / CSRF
- PyMySQL
- Waitress
- Bootstrap 5
- JavaScript
- python-dotenv

## Структура проекта

```
debt_manager/
├── app/
│   ├── __init__.py             # Создание Flask-приложения, регистрация роутов и миграций
│   ├── models.py               # SQLAlchemy-модели и to_dict()
│   ├── routes/                 # Маршруты приложения
│   │   ├── auth.py             # Авторизация, Telegram, dev-login, admin-login
│   │   ├── admin.py            # Админка: пользователи, настройки, экспорт, справочники
│   │   ├── debts.py            # API долгов: CRUD, архив, восстановление, удаление
│   │   ├── incomes.py          # Доходы: создание, редактирование, удаление
│   │   ├── expenses.py         # Расходы: создание, редактирование, удаление
│   │   ├── main.py             # Дашборд, финансы, ипотека, архив, seed-данные
│   │   └── payments.py         # API платежей по долгам
│   ├── services/               # Сервисная логика
│   │   ├── debt_service.py
│   │   ├── finance_summary_service.py
│   │   ├── payment_service.py
│   │   └── telegram_auth_service.py
│   └── utils.py                # Парсинг, настройки, проверки, логирование
├── migrations/                 # Alembic миграции
├── static/                     # CSS и JS клиента
│   ├── css/style.css
│   └── js/app.js
├── templates/                  # HTML-шаблоны
├── config.py                   # Загрузка .env и построение SQLAlchemy URI
├── extensions.py               # Инициализация SQLAlchemy
├── run.py                      # Production-сервер через Waitress
├── deploy.sh                   # Скрипт обновления на сервере
├── start.bat                   # Быстрый запуск на Windows
├── requirements.txt            # Python-зависимости
├── .env.example                # Образец файла окружения
└── README.md                   # Документация проекта
```

## Переменные окружения

| Переменная | Обязательна | Пример | Описание |
|------------|------------|--------|----------|
| `SECRET_KEY` | Да | `change-me` | Секрет Flask для сессий и CSRF. |
| `FLASK_DEBUG` | Нет | `true` | Включает debug-режим. |
| `DATABASE_URL` | Нет | `mysql+pymysql://user:pass@localhost/debt_manager?charset=utf8mb4` | Полный URI БД. Используется первым. |
| `DB_ENGINE` | Нет | `mysql` / `sqlite` | Выбор двигателя базы данных. |
| `DB_HOST` | Нет | `localhost` | Хост MySQL. |
| `DB_PORT` | Нет | `3306` | Порт MySQL. |
| `DB_USER` | Нет | `debt_user` | Пользователь MySQL. |
| `DB_PASSWORD` | Нет | `secret` | Пароль MySQL. |
| `DB_NAME` | Нет | `debt_manager` | Имя базы данных. |
| `SQLITE_PATH` | Нет | `dev.db` | Файл SQLite. |
| `DEV_SQLITE_COPY_FROM_MYSQL` | Нет | `true` | Копирует данные из MySQL в SQLite при первом запуске. |
| `DEV_SQLITE_COPY_SOURCE_URL` | Нет | `mysql+pymysql://...` | Явный источник MySQL для копирования. |
| `TELEGRAM_BOT_TOKEN` | Да для Telegram | `123456:ABC-DEF...` | Токен Telegram-бота. |
| `TELEGRAM_BOT_USERNAME` | Да для Telegram | `YourBotUsername` | Имя Telegram-бота. |
| `ADMIN_LOGIN_ENABLED` | Нет | `true` | Разрешает аварийный вход `/admin/login`. |
| `ADMIN_PASSWORD` | Нет | `secret` | Пароль admin-login если хеш не задан. |
| `ADMIN_PASSWORD_HASH` | Нет | `pbkdf2:...` | Хеш пароля admin-login; приоритет выше. |
| `ADMIN_TELEGRAM_IDS` | Нет | `12345,67890` | Список Telegram ID супер-админов. |
| `DEV_LOGIN_ENABLED` | Нет | `true` | Включает dev-login при debug. |
| `TEST_USER_ENABLED` | Нет | `false` | Включает точку входа `/test-login`. |
| `TEST_USER_TELEGRAM_ID` | Нет | `-999999999999` | ID для тестового пользователя. |
| `TEST_USER_USERNAME` | Нет | `testuser` | Имя тестового пользователя. |
| `TEST_USER_FIRST_NAME` | Нет | `Тестовый` | Имя тестового пользователя. |
| `TEST_USER_LAST_NAME` | Нет | `Пользователь` | Фамилия тестового пользователя. |
| `TEST_USER_ROLE` | Нет | `user` | Роль тестового пользователя. |

> `ADMIN_PASSWORD_HASH` проверяется через `werkzeug.security.check_password_hash`.

## Локальный запуск

```bash
git clone https://github.com/UnicornisIT/debt_manager.git
cd debt_manager
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

1. Настройте `.env`.
2. Если используете MySQL, создайте базу.
3. Примените миграции:

```bash
export FLASK_APP=run.py
flask db upgrade
```

4. Запустите приложение:

```bash
python -m flask run
```

5. Откройте `http://127.0.0.1:5000`.

Для Windows:

```powershell
.venv\Scripts\activate
$env:FLASK_APP = 'app'
flask db upgrade
python -m flask run
```

## MySQL / MariaDB

```bash
sudo apt update && sudo apt install mysql-server -y
sudo mysql
```

В MySQL:

```sql
CREATE DATABASE debt_manager CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE USER 'debt_user'@'localhost' IDENTIFIED BY 'strong_password';
GRANT ALL PRIVILEGES ON debt_manager.* TO 'debt_user'@'localhost';
FLUSH PRIVILEGES;
EXIT;
```

Настройка `.env`:

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

## Миграции и таблицы

Проект использует Flask-Migrate. Создание схемы и приведение структуры базы к актуальному состоянию выполняются командой:

```bash
export FLASK_APP=run.py
flask db upgrade
```

`db.create_all()` не обновляет существующие таблицы и не добавляет новые столбцы в уже созданную базу. После изменения моделей всегда применяйте миграции через Alembic/Flask-Migrate.

### Миграции базы данных

- Для новой пустой базы достаточно:

```bash
export FLASK_APP=run.py
flask db upgrade
```

- После изменения моделей создайте миграцию и примените её:

```bash
export FLASK_APP=run.py
flask db migrate -m "Описание изменения"
flask db upgrade
```

- Для существующей базы без таблицы `alembic_version` `deploy.sh` автоматически ставит базовую миграцию `73459c8513a1` и затем применяет новые миграции.

- Нельзя использовать `db.drop_all()` или удалять таблицы вручную на production.

### Обновление существующей базы

Если после обновления проекта появляется ошибка:

```text
Unknown column 'ip_address' in 'field list'
```

то необходимо выполнить миграции:

```bash
export FLASK_APP=run.py
flask db upgrade
```

Если база была создана до внедрения Alembic и не содержит таблицы `alembic_version`, то `deploy.sh` автоматически помечает существующую схему базовой миграцией `73459c8513a1` и затем применяет новые изменения.

Если появляется ошибка:

```text
Table 'app_settings' already exists
```

это означает, что в базе уже есть схема, а Alembic ещё не инициализирован. Обновлённый `deploy.sh` решает это, устанавливая метку `73459c8513a1` для существующей схемы и далее выполняя `flask db upgrade`.

Если появляется ошибка `Data truncated for column 'debt_type'`, то миграция должна обновить `debts.debt_type` до `ENUM('credit_card','split','mortgage')`.

Если нужно проверить структуру таблицы вручную:

```bash
sudo mysql
USE debt_manager;
DESCRIBE activity_logs;
DESCRIBE users;
DESCRIBE debts;
```

Если по какой-то причине миграция не прошла и требуется ручной резервный вариант:

```sql
ALTER TABLE activity_logs ADD COLUMN ip_address VARCHAR(45) NULL AFTER description;
ALTER TABLE activity_logs ADD COLUMN user_agent TEXT NULL AFTER ip_address;
ALTER TABLE debts MODIFY debt_type ENUM('credit_card','split','mortgage') NOT NULL;
```

После этого выполните:

```bash
sudo systemctl restart debt_manager
```

## Обновление старой базы

Если в старой MySQL-базе поле `debt_type` не поддерживает `mortgage`, выполните:

```sql
ALTER TABLE debts MODIFY debt_type ENUM('credit_card','split','mortgage') NOT NULL;
```

## Telegram Login

1. Создайте бота через [BotFather](https://t.me/BotFather).
2. Получите `TELEGRAM_BOT_TOKEN`.
3. Настройте домен через `/setdomain`.
4. Укажите `TELEGRAM_BOT_USERNAME` и `TELEGRAM_BOT_TOKEN` в `.env`.

> Telegram Login Widget требует HTTPS и разрешённый домен.

## Dev-вход

Для безопасной локальной разработки используйте:

```env
DEV_LOGIN_ENABLED=true
FLASK_DEBUG=true
```

Доступные адреса:

- `/dev-login/user`
- `/dev-login/admin`
- `/dev-login/superadmin`
- `/dev-logout`

> Не включайте `DEV_LOGIN_ENABLED` на production.

## Роли пользователей

- `user` — работает со своими долгами, доходами, расходами.
- `admin` — доступ к админ-панели.
- `superadmin` — полный доступ, управление ролями и экспорт.

### Как назначить superadmin

Через `ADMIN_TELEGRAM_IDS` или CLI:

```bash
export FLASK_APP=run.py
flask create-superadmin <telegram_id>
```

## Админ-панель

Админка доступна по `/admin`.
Функции:

- статистика и последние логи;
- настройки приложения и справочники;
- список пользователей с фильтрами;
- impersonation пользователей;
- экспорт CSV по пользователям, долгам и платежам.

## Развёртывание на Linux/VPS

### Подготовка

```bash
sudo apt update && sudo apt upgrade -y
sudo apt install python3 python3-venv python3-pip git nginx mysql-server -y
```

### Развертывание

```bash
sudo mkdir -p /var/www/debt_manager
sudo chown -R $USER:$USER /var/www/debt_manager
cd /var/www/debt_manager
git clone https://github.com/UnicornisIT/debt_manager.git .
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
cp .env.example .env
```

Настройте `.env` и базу данных, затем примените миграции:

```bash
export FLASK_APP=run.py
flask db upgrade
```

### Проверка

```bash
python run.py
```

Откройте `http://127.0.0.1:5000`.

## systemd

Пример `/etc/systemd/system/debt_manager.service`:

```ini
[Unit]
Description=Debt Manager Flask Application
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

```bash
sudo systemctl daemon-reload
sudo systemctl enable debt_manager
sudo systemctl start debt_manager
sudo systemctl status debt_manager
```

> Проект использует Waitress в `run.py`. Если вы предпочитаете Gunicorn, установите его отдельно и запустите `gunicorn app:app --workers 3 --bind 127.0.0.1:8000`.

## Nginx

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

```bash
sudo ln -s /etc/nginx/sites-available/debt_manager /etc/nginx/sites-enabled/
sudo nginx -t
sudo systemctl restart nginx
```

## SSL / Certbot

```bash
sudo apt install certbot python3-certbot-nginx -y
sudo certbot --nginx -d example.com -d www.example.com
```

После этого обновите домен в BotFather.

## Обновление проекта

```bash
cd /var/www/debt_manager
git pull origin master
source venv/bin/activate
pip install -r requirements.txt
sudo systemctl restart debt_manager
sudo systemctl status debt_manager
```

Если есть локальные изменения, выполните `git status` и используйте `git stash` или закоммитьте изменения.

## Резервное копирование

```bash
mysqldump -u debt_user -p debt_manager > backup_debt_manager.sql
mysql -u debt_user -p debt_manager < backup_debt_manager.sql
```

## Полезные команды

```bash
sudo systemctl status debt_manager
sudo journalctl -u debt_manager -f
sudo journalctl -u debt_manager -n 100
sudo nginx -t
sudo tail -f /var/log/nginx/error.log
sudo systemctl restart debt_manager
```

## Частые ошибки

- `pymysql.err.OperationalError: Can't connect to MySQL` — проверьте настройки MySQL и `.env`.
- `Table 'debt_manager.users' doesn't exist` — выполните `flask db upgrade`.
- `Table 'app_settings' already exists` — база создана до Alembic, используйте обновлённый `deploy.sh`, который ставит метку `73459c8513a1` и затем выполняет `flask db upgrade`.
- `Unknown column 'ip_address' in 'field list'` — выполните `flask db upgrade`.
- `Data truncated for column 'debt_type'` — выполните `ALTER TABLE debts MODIFY debt_type ENUM('credit_card','split','mortgage') NOT NULL;`.
- `Telegram Login Widget показывает Username invalid` — проверьте `TELEGRAM_BOT_USERNAME`, домен в BotFather и HTTPS.
- `dev-login не отображается` — убедитесь, что `DEV_LOGIN_ENABLED=true` и `FLASK_DEBUG=true`.
- `тёмная тема не применяется` — очистите localStorage и перезагрузите.
- `nginx configuration test failed` — проверьте конфигурацию и пути.
- `static-файлы не подгружаются` — проверьте Nginx правило `/static/`.
- `permission denied в /var/www/debt_manager` — проверьте права и пользователя сервисов.

## Безопасность

- `FLASK_DEBUG=false` на production.
- `DEV_LOGIN_ENABLED=false` на сервере.
- `SECRET_KEY` должен быть уникальным.
- Используйте отдельного пользователя MySQL.
- Не коммитьте `.env`.
- Настройте HTTPS.
- Отключите `ADMIN_LOGIN_ENABLED`, если аварийный вход не нужен.
- Используйте `ADMIN_TELEGRAM_IDS` для супер-админов.
- Делайте резервные копии БД.
''' ; Path('README.md').write_text(text, encoding='utf-8')"