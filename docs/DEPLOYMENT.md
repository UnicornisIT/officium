# Установка, обновление и восстановление Officium

Документ описывает управляемую схему Ubuntu для публичного GitHub repository:
версионированные каталоги релизов,
атомарную ссылку `current`, Waitress только на loopback, MySQL/SQLite backup,
ежедневный timer и обновление через локальный Unix socket. Production нельзя
обновлять командами из этого документа без отдельного решения владельца.

## 1. Схема файлов и привилегий

Пример с `APP_DIR=/opt/officium`:

```text
/opt/officium/
  current -> /opt/officium/releases/vX.Y.Z-<commit>
  releases/                 # root:officium; только одноразовый .build-* app-owned
  shared/.env               # root:officium 0640
  shared/instance/          # данные приложения, если они нужны локально
/etc/officium/updater.env   # root:root 0600
/usr/local/sbin/officium-update-runner       # root:root 0755
/usr/local/lib/officium/deploy-release       # root:root 0755
/run/officium/updater.sock  # root:officium 0660
/var/lib/officium/          # root:officium 0750, status операции
/var/backups/officium/      # root:root 0750
```

Flask работает от `APP_USER`, с `NoNewPrivileges=true`, не запускает `sudo` и
не получает root-команды. `officium-updater.socket` принимает только локальные
соединения, а runner проверяет Linux `SO_PEERCRED`: разрешены UID root и ровно
настроенный `APP_USER`. JSON-протокол допускает только `check`,
`integration_check`, `get_status`, `start_latest_update`; лишние поля,
произвольные команды и пути отклоняются.

Root-служба сама повторно проверяет GitHub repository, последний stable release,
точный tag/commit и блокировку конкурентной операции. Привилегированный runner и
deploy script обновляются только из того exact-tag checkout, чей commit уже
сверен с GitHub. Перед атомарной заменой фиксированных root-owned путей служба
проверяет тип файлов и синтаксис; затем перезапускает updater и выполняет IPC
self-test от `APP_USER`. При ошибке старый bundle восстанавливается, а код
приложения возвращается на предыдущую release-ссылку. Браузер не может передать
произвольный путь или содержимое bundle.

После завершения непривилегированной сборки root снимает write bits, меняет
владельца всего candidate на `root:APP_GROUP`, повторно сверяет commit с remote
tag и проверяет tracked tree с отключёнными Git replace refs/external diff.
Только после этого candidate может стать `current` и источником privileged
bundle. Сам `releases/` не writable для `APP_USER`: builder получает отдельный
одноразовый `.build-*`, который root перемещает и удаляет после завершения.

Граница доверия остаётся явной: процесс с UID `APP_USER` может запросить только
проверку, статус или установку последнего stable release, поэтому компрометация
веб-процесса может вызвать отказ в обслуживании через повторные допустимые
запросы, но не передать root-службе команду, URL или путь. Root-конфигурация
фиксирует GitHub repository; контроль этого repository и его stable releases —
trust anchor. Отдельной криптографической подписи release manifest пока нет,
поэтому защита GitHub-организации, branch/tag policy и release approvals входят
в обязательную операционную модель.

Private repository сейчас намеренно не поддерживается: release metadata и Git
clone должны работать без передачи GitHub token веб-процессу или `APP_USER`.
Для private source нужна отдельная read-only credential architecture и новый
release gate; добавление token только в `.env` не считается поддержкой.

## 2. Подготовка `.env`

Скопируйте `.env.example` во внешний временный файл и заполните его до запуска
установщика. Обязательны уникальный `SECRET_KEY`, явный `APP_TIMEZONE`, база и
настройки production. Для примера Москвы:

```dotenv
OFFICIUM_ENV=production
SECRET_KEY=<случайное значение не короче 32 символов>
SESSION_COOKIE_SECURE=true
APP_TIMEZONE=Europe/Moscow
DB_ENGINE=mysql
DB_HOST=127.0.0.1
DB_PORT=3306
DB_USER=officium
DB_PASSWORD=<пароль>
DB_NAME=officium
SERVER_UPDATE_ENABLED=true
SERVER_UPDATE_REPOSITORY=UnicornisIT/officium
SERVER_UPDATE_SOCKET=/run/officium/updater.sock
SERVER_UPDATE_APP_DIR=/opt/officium/current
SERVER_UPDATE_STATUS_PATH=/var/lib/officium/server-update-status.json
```

Пароль БД не передаётся в argv. Backup создаёт временный mode `0600` MySQL
option-file, запускает `mysqldump --single-transaction --quick
--no-tablespaces --routines --triggers --events`, проверяет exit code и
ненулевой размер, затем удаляет option-file. Пользователю БД не нужен глобальный
`PROCESS`; права на routines/events должны быть выданы только в базе приложения.

Для SQLite updater использует `sqlite3.Connection.backup()`, что согласовано с
активным WAL и безопаснее копирования файла.

## 3. Preflight без изменений

Создайте системного пользователя заранее и подготовьте пустую MySQL database с
ограниченным пользователем. Установщик не принимает root-пароль MySQL и не
расширяет grants.

```bash
sudo groupadd --system officium
sudo useradd --system --gid officium --home /opt/officium --shell /usr/sbin/nologin officium

sudo env \
  RELEASE_TAG=<точный-проверенный-tag> \
  ENV_FILE_SOURCE=/root/officium.env \
  APP_DIR=/opt/officium \
  APP_USER=officium APP_GROUP=officium \
  VENV_DIR=/opt/officium/current/venv \
  SERVICE_NAME=officium \
  HOST=127.0.0.1 PORT=5000 \
  TRUSTED_PROXY=127.0.0.1 \
  APP_TIMEZONE=Europe/Moscow \
  DB_ENGINE=mysql DB_NAME=officium \
  BACKUP_DIR=/var/backups/officium \
  bash deployment/install-officium.sh preflight
```

Preflight проверяет Ubuntu, команды и `python3-venv`, exact remote tag, минимум
1 GiB, имена/пути, loopback bind/proxy, явный timezone, `.env`, MySQL client и
членство пользователя в группе. В режиме `preflight` файловые изменения не
выполняются.

## 4. Чистая установка

После PASS повторите ту же команду с `install`. Не используйте тег, который не
прошёл release gate.

```bash
sudo env <те же переменные> bash deployment/install-officium.sh install
```

Установщик:

1. создаёт только нужные каталоги с минимальными правами;
2. клонирует exact tag в `.staging-*`, сверяет remote commit;
3. создаёт отдельный `venv` или `.venv`, ставит зависимости;
4. выполняет `db-preflight`, migrations и импорт `run:app` до активации;
5. атомарно создаёт `current`;
6. генерирует и проверяет app/timer/updater units через
   `systemd-analyze verify`;
7. запускает Waitress и timer;
8. вызывает `integration_check` через тот же socket от `APP_USER`;
9. запускает read-only `post-deploy-smoke.sh`.

Waitress unit формирует один аргумент proxy headers:

```ini
ExecStart=/opt/officium/current/venv/bin/waitress-serve \
  --listen=127.0.0.1:5000 --threads=4 --trusted-proxy=127.0.0.1 \
  "--trusted-proxy-headers=x-forwarded-for x-forwarded-host x-forwarded-proto x-forwarded-port" \
  run:app
```

Фактический unit — одна строка; переносы выше только для чтения. `app:app` не
используется. Forwarded-заголовки принимаются только от явно заданного proxy.

## 5. Обновление managed-установки

Панель superadmin автоматически показывает GitHub release и выполняет две
проверки: статический preflight и интеграционный IPC. Кнопка доступна только при
успехе обеих. После подтверждения состояние проходит реальные этапы:

`preflight → backup → checkout → dependencies → migrations → restart → healthcheck`.

`operation_id`, status и диагностический код живут в `/var/lib/officium`, поэтому
рестарт Flask не теряет операцию. Повторный POST и конкурентный update
блокируются в root-службе.

CLI-эквивалент для аварийной процедуры (не из Flask):

```bash
sudo /usr/local/sbin/officium-update-runner --check
sudo -u officium /usr/local/bin/officium-update-client integration_check
sudo /usr/local/sbin/officium-update-runner --start-latest
sudo journalctl -fu officium-update-*.service
```

`scripts/deploy.sh --release <tag>` — совместимая root-only оболочка. Она не
делает checkout текущего дерева.

## 6. Миграция старого `/opt/officium` или `/var/www/...` checkout

Укажите фактический legacy path в `APP_DIR` и вызовите `upgrade` из проверенной
копии нового installer:

```bash
sudo env \
  RELEASE_TAG=<проверенный-новый-tag> \
  APP_DIR=/opt/officium APP_USER=officium APP_GROUP=officium \
  VENV_DIR=/opt/officium/current/venv SERVICE_NAME=officium \
  HOST=127.0.0.1 PORT=5000 TRUSTED_PROXY=127.0.0.1 \
  APP_TIMEZONE=Europe/Moscow DB_ENGINE=mysql DB_NAME=officium \
  REPOSITORY=UnicornisIT/officium \
  bash deployment/install-officium.sh upgrade
```

Если `APP_DIR` — Git checkout без `current`, installer автоматически:

- отказывается продолжать при tracked/staged local changes, но ничего не
  удаляет и не вызывает `stash`, `reset --hard` или `clean`;
- делает database/config backup через новый runner;
- собирает release в соседнем `$APP_DIR.managed.*`;
- останавливает старую службу только после успешной сборки/migrations;
- переименовывает старый каталог в `$APP_DIR.legacy.<timestamp>` и активирует
  managed tree;
- при неуспешном restart возвращает старый каталог и unit.

Untracked пользовательские файлы остаются в сохранённом legacy-каталоге и не
подмешиваются в release. Проверите их вручную до удаления каталога.

## 7. Nginx, HTTPS, cookies и Telegram

`ENABLE_NGINX=true SERVER_NAME=finance.example.org` создаёт server block из
`deployment/nginx-officium.conf.in` и выполняет `nginx -t`. Waitress остаётся на
loopback. Получите сертификат обычным контролируемым способом, затем включите
redirect HTTP→HTTPS и оставьте `SESSION_COOKIE_SECURE=true`.

Reverse proxy обязан передавать Host, X-Forwarded-For/Host/Proto/Port. Не
доверяйте `0.0.0.0/0` и внешнему адресу proxy. Маршруты `/login`,
`/telegram-app`, `/auth/telegram-mini-app` и Telegram callback должны указывать
на публичный HTTPS hostname. Login, Mini App и bot имеют отдельные настройки и
отдельные индикаторы «включено / конфигурация корректна / проверено».

## 8. Ежемесячные расходы

Сгенерированный timer выполняется ежедневно в `00:05 APP_TIMEZONE`, имеет
`Persistent=true` и запускает CLI из venv текущего release. Повторный запуск
идемпотентен; уникальный индекс блокирует параллельный duplicate. Production
smoke не вызывает генератор и не создаёт финансовые записи.

```bash
systemctl list-timers officium-monthly-expenses.timer
journalctl -u officium-monthly-expenses.service --since today
```

## 9. Recovery и ограничения rollback

Код можно переключить на завершённый release:

```bash
sudo env APP_DIR=/opt/officium SERVICE_NAME=officium \
  ROLLBACK_RELEASE=/opt/officium/releases/<release-dir> \
  bash deployment/install-officium.sh recover
```

Healthcheck failure сразу после update автоматически возвращает прежнюю ссылку
и перезапускает службу. Это **не** обещает rollback MySQL DDL: MySQL DDL не
всегда транзакционен. При несовместимой миграции остановите запись, сохраните
проблемную БД/журналы, восстановите предрелизный dump в новую пустую БД,
сверьте таблицы/суммы, затем переключите `.env`. Не выполняйте без плана
`flask db downgrade`.

Если IPC неисправен: сохраните `operation_id`, выполните root `--check`,
проверьте `systemctl status officium-updater.socket`, права socket/config и
запустите только `--start-latest`. Не включайте sudoers и не отключайте
`NoNewPrivileges` у Flask.

## 10. Read-only post-deploy smoke

```bash
sudo bash deployment/post-deploy-smoke.sh \
  --service officium \
  --app-dir /opt/officium/current \
  --url http://127.0.0.1:5000/login \
  --timer officium-monthly-expenses.timer
```

Скрипт не пишет финансовые данные: проверяет version/commit, active units,
`NoNewPrivileges`, импорт `run:app`, read-only Alembic state, локальный HTTP,
статику и число critical journal events. Секреты не печатаются.
