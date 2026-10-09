# Развёртывание Officium

Эта инструкция предназначена для релизного окружения с MySQL/MariaDB, HTTPS и
запуском Flask через Waitress за reverse proxy. SQLite оставлен для локальной
разработки и автоматических тестов.

## 1. Подготовка

Требуются Python 3.10+, MySQL 8+/MariaDB 10.6+, домен и HTTPS-сертификат.
Перед первым запуском создайте отдельного пользователя БД с правами только на
базу Officium. Не используйте `root` для приложения.

Сгенерируйте секреты локально:

```powershell
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

Скопируйте `.env.example` в `.env` и обязательно задайте:

```env
OFFICIUM_ENV=production
APP_TIMEZONE=Europe/Moscow
SECRET_KEY=<уникальный секрет не короче 32 символов>
FLASK_DEBUG=false
SESSION_COOKIE_SECURE=true
DEV_LOGIN_ENABLED=false
TEST_USER_ENABLED=false
TELEGRAM_LOGIN_ENABLED=false
TELEGRAM_MINI_APP_ENABLED=false
TELEGRAM_BOT_ENABLED=false

DB_ENGINE=mysql
DB_HOST=127.0.0.1
DB_PORT=3306
DB_USER=officium
DB_PASSWORD=<пароль>
DB_NAME=debt_manager
```

Если включена любая Telegram-функция, задайте `TELEGRAM_BOT_TOKEN`; для webhook
бота дополнительно задайте отдельный длинный `TELEGRAM_WEBHOOK_SECRET`. Если
включён Google OAuth, задайте client ID, client secret и точный HTTPS callback
URL. Для аварийного админ-входа используйте только `ADMIN_PASSWORD_HASH`,
созданный `werkzeug.security.generate_password_hash`. Строгая проверка
production-конфигурации останавливает запуск при небезопасных development-флагах,
cookie или пропущенных обязательных секретах.

## 2. Резервная копия

Перед каждым обновлением:

```bash
mysqldump --single-transaction --routines --triggers -u officium -p debt_manager > officium-before-release.sql
```

Проверьте, что файл не пуст, хранится вне публичной директории и доступ к нему
ограничен. Периодически проверяйте восстановление копии на отдельной базе.

Для локальной SQLite сначала остановите приложение, затем скопируйте файл БД:

```powershell
Copy-Item dev.db dev.backup.db
```

## 3. Установка и миграции

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
$env:FLASK_APP = 'run.py'
.\.venv\Scripts\python.exe -m pip check
.\.venv\Scripts\python.exe -m unittest discover -v
.\.venv\Scripts\python.exe -m flask db upgrade
.\.venv\Scripts\python.exe -m flask db current
.\.venv\Scripts\python.exe -m flask db heads
.\.venv\Scripts\python.exe -m flask db check
```

Ожидается одна голова миграций, `current` на этой голове и сообщение
`No new upgrade operations detected`. `init_db.sql` можно использовать только
для создания пустой MySQL-базы; схему создаёт исключительно Alembic.

## 4. Запуск

`run.py` запускает Waitress. На сервере оформите его как системную службу с
автоматическим перезапуском и отдельным непривилегированным пользователем.
Перед Waitress поставьте Nginx/Caddy/IIS, завершайте TLS на reverse proxy и
передавайте исходные заголовки только от доверенного proxy.

```powershell
$env:HOST = '127.0.0.1'
$env:PORT = '5000'
.\.venv\Scripts\python.exe run.py
```

Не выставляйте внутренний порт Waitress напрямую в интернет. Разрешите наружу
только HTTPS. Ограничьте доступ к `.env`, журналам, БД и резервным копиям.

## 5. Проверка после выкладки

- `/login` открывается по HTTPS, `/admin/login` доступен только если аварийный вход включён;
- неавторизованный запрос к `/api/debts` получает 401;
- миграции находятся на единственной голове;
- создание расхода, дохода, долга и платежа работает в тестовом аккаунте;
- быстрые команды Telegram ничего не записывают до подтверждения;
- повтор одного Telegram `update_id` не создаёт дубликат;
- импорт тестовой CSV/XLSX/PDF-выписки показывает предпросмотр до сохранения;
- Google callback URL в консоли Google в точности совпадает с `GOOGLE_REDIRECT_URI`;
- в production отключены debug-, dev- и test-входы.

## 6. Обновление сервера из админки

Раздел «Админка → Обновление сервера» доступен только пользователю с ролью
`superadmin`. Он получает последний опубликованный полноценный GitHub Release,
сверяет тег и передаёт установку отдельной фоновой задаче systemd. Веб-процесс
не выполняет команды из формы и не обновляет сам себя напрямую.

Функция намеренно выключена по умолчанию. Для её первой настройки на Linux-сервере:

1. Установите проверенный помощник как файл, принадлежащий `root`. Установщик
сохраняет существующий `/etc/officium/updater.env`, проверяет sudoers через
`visudo` и принимает имя пользователя веб-процесса первым аргументом:

```bash
cd /var/www/debt_manager
sudo sh deployment/install-officium-updater.sh officium
sudo editor /etc/officium/updater.env
```

В `/etc/officium/updater.env` обязательно проверьте `APP_DIR`, имя
непривилегированного пользователя приложения `APP_USER`, имя systemd-службы
`SERVICE_NAME` и репозиторий. Пользователь приложения должен владеть Git checkout
и виртуальным окружением, но не файлом `/usr/local/sbin/officium-update-runner`.

Если автоматический установщик не используется, подготовьте каталоги состояния и
резервных копий вручную. В примере пользователь приложения называется `officium`:

```bash
sudo install -d -o root -g officium -m 2775 /var/lib/officium
sudo install -d -o root -g root -m 0750 /var/backups/officium
```

Установите узкое правило sudo. Сначала проверьте имя пользователя в файле:

```bash
sudo editor deployment/officium-updater.sudoers
sudo visudo -cf deployment/officium-updater.sudoers
sudo install -o root -g root -m 0440 deployment/officium-updater.sudoers /etc/sudoers.d/officium-updater
sudo visudo -cf /etc/sudoers.d/officium-updater
```

Помощник принимает только безопасный тег, сам повторно проверяет его через GitHub
API и умеет запустить только фиксированный сценарий обновления. Не заменяйте это
правило разрешением на произвольные `systemctl`, shell или `deploy.sh` от root.

2. Добавьте в `.env` приложения:

```env
SERVER_UPDATE_ENABLED=true
SERVER_UPDATE_REPOSITORY=UnicornisIT/officium
SERVER_UPDATE_HELPER=/usr/local/sbin/officium-update-runner
SERVER_UPDATE_USE_SUDO=true
SERVER_UPDATE_SUDO_PATH=/usr/bin/sudo
SERVER_UPDATE_APP_DIR=/var/www/debt_manager
SERVER_UPDATE_STATUS_PATH=/var/lib/officium/server-update-status.json
SERVER_UPDATE_REQUIRE_ROOT_OWNED_HELPER=true
```

В `/etc/officium/updater.env` можно дополнительно задать локальную HTTP-проверку,
соответствующую реальному адресу Waitress/Gunicorn:

```env
HEALTHCHECK_URL=http://127.0.0.1:5000/login
```

Если порт приложения доступен только через reverse proxy, оставьте переменную
пустой: обязательная проверка `systemctl is-active` всё равно выполняется.

Для публичного репозитория токен не нужен. Для приватного репозитория задайте
`SERVER_UPDATE_GITHUB_TOKEN` в `.env`, а путь к отдельному root-owned файлу с тем
же токеном — в `GITHUB_TOKEN_FILE` файла `/etc/officium/updater.env`.

3. Перезапустите приложение и откройте новый раздел админки. Перед первой
реальной установкой выполните безопасную проверку конфигурации: она не скачивает
релиз, не изменяет checkout и не перезапускает службу.

```bash
sudo -u officium sudo -n /usr/local/sbin/officium-update-runner --check
```

Команда должна вернуть `OFFICIUM_UPDATE_RESULT={"ok":true,"code":"ready"}` и не
запрашивать пароль. Реальное обновление запускайте кнопкой только после публикации
GitHub Release. Поддерживаются теги до 128 символов без `/`, пробелов и shell-знаков.

При обновлении помощник:

- независимо подтверждает, что тег является последним опубликованным релизом;
- сверяет `origin` с разрешённым репозиторием и отказывается работать при локальных
  изменениях отслеживаемых файлов;
- создаёт согласованную SQLite-копию либо дамп MySQL через `mysqldump`, а также
  root-only копии `.env` и `/etc/officium/updater.env`;
- устанавливает точный commit тега в detached HEAD, зависимости и миграции;
- перезапускает только настроенную службу, проверяет её активность и, если задан,
  `HEALTHCHECK_URL`, затем записывает итог в
  `/var/lib/officium/server-update-status.json`.

Логи фоновой операции доступны через `journalctl -u 'officium-update-*'`. Статус,
этап, безопасный код ошибки и идентификатор операции переживают перезапуск Flask,
поскольку хранятся в `/var/lib/officium`, а не в памяти веб-процесса. Сам
root-owned помощник не обновляется из Git checkout намеренно: если его версия
изменилась в новом релизе, повторите команду `install` после проверки файла.

### Диагностика отказа помощника

Помощник не является HTTP-сервисом: у него нет адреса, порта или отдельного секрета.
Flask запускает фиксированный root-owned файл через `sudo -n`, а тот создаёт
transient-unit `officium-update-*` через `systemd-run`. Поэтому сообщение об
авторизации относится к sudo/systemd, а не к Telegram или CSRF.

Для существующего сервера сначала определите пользователя веб-службы и сравните
его с первым полем `/etc/sudoers.d/officium-updater`:

```bash
systemctl show debt_manager --property=User --value
sudo visudo -cf /etc/sudoers.d/officium-updater
sudo -l -U officium
sudo -u officium sudo -n /usr/local/sbin/officium-update-runner --check
sudo cat /var/lib/officium/server-update-status.json
sudo journalctl -u 'officium-update-*' -n 200 --no-pager
sudo journalctl -u debt_manager -n 100 --no-pager
```

Если первая команда выводит не `officium`, переустановите sudoers, передав
фактического пользователя установщику. После обновления файлов выполните:

```bash
cd /var/www/debt_manager
sudo sh deployment/install-officium-updater.sh "$(systemctl show debt_manager --property=User --value)"
sudo editor /etc/officium/updater.env
sudo -u "$(systemctl show debt_manager --property=User --value)" sudo -n /usr/local/sbin/officium-update-runner --check
sudo systemctl restart debt_manager
```

Если сломанная кнопка не позволяет получить релиз с исправлением, сначала обновите
только root-owned помощник из заранее проверенного тега, не переключая рабочий
checkout и не трогая базу. Замените `<FIX_RELEASE_TAG>` опубликованным тегом:

```bash
cd /var/www/debt_manager
app_user="$(systemctl show debt_manager --property=User --value)"
fix_tag='<FIX_RELEASE_TAG>'
sudo -u "$app_user" git fetch origin "refs/tags/$fix_tag:refs/tags/$fix_tag"
helper_temp="$(mktemp)"
sudo -u "$app_user" git show "$fix_tag:deployment/officium-update-runner" > "$helper_temp"
sudo install -o root -g root -m 0755 "$helper_temp" /usr/local/sbin/officium-update-runner
rm -f "$helper_temp"
sudo -u "$app_user" sudo -n /usr/local/sbin/officium-update-runner --check
```

После успешного `--check` можно устанавливать этот же тег кнопкой. После завершения
повторно запустите `install-officium-updater.sh` уже из обновлённого checkout.

## 7. Автоматические ежемесячные расходы

Генератор запускается отдельным systemd timer, а не внутри Flask/Waitress worker.
Штатное расписание — ежедневно в 00:05 по `Europe/Moscow`: первого числа записи
появляются в первые минуты месяца, а ежедневный повтор автоматически восстанавливает
пропуски после недоступности приложения или БД. Команда идемпотентна, а уникальный
индекс БД защищает от параллельных запусков.

После обновления кода и `flask db upgrade` установите units. Если каталог,
виртуальное окружение, пользователь или группа отличаются от примера, сначала
отредактируйте копию `officium-monthly-expenses.service`:

```bash
cd /var/www/debt_manager
sudo install -o root -g root -m 0644 deployment/officium-monthly-expenses.service /etc/systemd/system/officium-monthly-expenses.service
sudo install -o root -g root -m 0644 deployment/officium-monthly-expenses.timer /etc/systemd/system/officium-monthly-expenses.timer
sudo systemctl daemon-reload
sudo systemctl enable --now officium-monthly-expenses.timer
sudo systemctl start officium-monthly-expenses.service
systemctl status officium-monthly-expenses.timer --no-pager
systemctl list-timers officium-monthly-expenses.timer --no-pager
journalctl -u officium-monthly-expenses.service -n 100 --no-pager
```

Для проверки без ожидания нового месяца задайте целевой месяц явно. Повторите
команду дважды: во втором результате `Generated` должен быть `0`, а записи должны
перейти в `Skipped`.

```bash
cd /var/www/debt_manager
target_month=$(TZ=Europe/Moscow date +%Y-%m)
sudo -u officium venv/bin/python -m flask --app run.py generate-monthly-expenses --month "$target_month"
```

Указывайте только текущий или уже наступивший месяц при production-проверке:
CLI технически принимает явный месяц для тестов и аварийного восстановления.

## 8. Откат

Миграции проекта намеренно не полагаются на автоматический downgrade для
операций с пользовательскими финансами. При проблеме:

1. остановите запись в приложение;
2. сохраните отдельную копию проблемной БД и журналов;
3. верните предыдущий проверенный релиз кода;
4. восстановите предрелизный дамп в новую пустую базу;
5. переключите приложение на восстановленную базу;
6. повторите smoke-проверки до открытия доступа.

Не откатывайте production командой `db downgrade` без отдельного проверенного
плана: часть старых миграций запрещает downgrade, чтобы не терять данные.
