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
  releases/<tag>-<commit>/.env -> /opt/officium/shared/.env   # всегда абсолютная ссылка
/etc/officium/updater.env   # root:root 0600
/usr/local/sbin/officium-update              # root:root 0755, команда оператора
/usr/local/sbin/officium-update-runner       # root:root 0755
/usr/local/bin/officium-update-client        # root:root 0755
/usr/local/lib/officium/deploy-release       # root:root 0755
/etc/tmpfiles.d/officium.conf  # d /run/officium 0750 root officium -
/run/officium/              # root:officium 0750 (создаёт tmpfiles.d)
/run/officium/updater.sock  # root:officium 0660
/var/log/officium/update-<время>.log         # журнал officium-update без секретов
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

Waitress запускается через `python -m waitress`, proxy headers — один аргумент:

```ini
ExecStart=/opt/officium/current/venv/bin/python -m waitress \
  --listen=127.0.0.1:5000 --threads=4 --trusted-proxy=127.0.0.1 \
  "--trusted-proxy-headers=x-forwarded-for x-forwarded-host x-forwarded-proto x-forwarded-port" \
  run:app
```

Фактический unit — одна строка; переносы выше только для чтения. `app:app` не
используется. Forwarded-заголовки принимаются только от явно заданного proxy.
Console-скрипты venv (`waitress-serve`, `flask`, `pip`) нигде не вызываются:
они хранят абсолютный путь каталога сборки и ломаются после перемещения
релиза. Сборка и runner проверяют, что ни один файл `venv/bin/` не ссылается
за пределы своего релиза.

## 5. Обновление

### 5.1. Одна команда с сервера

```bash
sudo officium-update check          # только диагностика, ничего не меняет
sudo officium-update latest         # обновить до последнего GitHub Release
sudo officium-update --tag vX.Y.Z   # обновить до конкретного опубликованного тега
sudo officium-update backup         # только резервная копия БД и конфигурации
sudo officium-update rollback       # вернуть код на предыдущий релиз (БД не меняется)
```

Флаги: `--yes` — без интерактивного подтверждения, `--dry-run` — показать план
и ничего не делать. Коды выхода: `0` — успех, `1` — обновление не прошло и
предыдущий релиз восстановлен, `2` — нужно ручное вмешательство (команда
печатает, что именно сделать).

Команда сама определяет раскладку (managed или legacy checkout) и берёт
параметры из существующих `shared/.env` (или legacy `.env`), unit
(`systemctl show -p ExecStart`, включая drop-ins) и `/etc/officium/updater.env`.
Если чего-то не хватает, она останавливается до любых изменений и перечисляет
недостающие параметры. Порядок обновления:

1. Проверка root, `cd /`, минимум 1 GiB свободного места.
2. Проверенная копия нужного тега во временном `/var/tmp/officium-update.*`
   (commit сверяется с удалённым тегом).
3. Резервная копия в `/var/backups/officium/<время>-<с>-to-<на>/`:
   `mysqldump --single-transaction --no-tablespaces` через временный option-file
   `0600` (пароль не попадает в argv и вывод; дамп обязан заканчиваться строкой
   `-- Dump completed`) или SQLite `.backup`; копии `.env`, unit, всех drop-ins,
   nginx vhost, `updater.env` и `tmpfiles.d`, права `0600`.
4. Managed: установка updater-bundle и unit-файлов из нового тега, затем
   `officium-update-runner --start` — тот же путь, что у кнопки. Legacy:
   `install-officium.sh upgrade` из проверенной копии (см. раздел 6).
5. Проверка здоровья (active, `NRestarts` не растёт 10 с, HTTP 200 на
   `HEALTHCHECK_URL`), smoke-тест со статикой через nginx, `integration_check`.
6. Итоговая сводка: версия, путь `current`, статус сайта и кнопки обновления,
   бэкапы, legacy-каталог, предупреждения.

Полный журнал без секретов: `/var/log/officium/update-<время>.log`.

Если команды ещё нет на сервере, возьмите её из нужного релиза:

```bash
curl -fsSLo /root/officium-update \
  https://raw.githubusercontent.com/UnicornisIT/officium/<tag>/scripts/officium-update
sudo bash /root/officium-update --tag <tag>
```

После первого запуска команда установлена в `/usr/local/sbin/officium-update` и
обновляется вместе с runner.

**С версии ≤ v0.0.7 обновляться только командой с сервера.** В этих версиях
кнопка вызывала `sudo` из Flask и всегда падала из-за `NoNewPrivileges`, а
установленный тогда runner мог отклонять корректные теги. На v0.0.8–v0.0.9
первое обновление тоже лучше выполнить командой: старый runner собирает релиз
со ссылкой `.env` через `current` и `waitress-serve` в unit, а `officium-update`
сначала ставит исправленный runner и unit из нового тега.

### 5.2. Кнопка в админке

Панель superadmin показывает GitHub release и выполняет две проверки:
статический preflight и интеграционный IPC. Кнопка доступна только при успехе
обеих. После подтверждения состояние проходит реальные этапы:

`preflight → backup → checkout → dependencies → migrations → restart → healthcheck`.

`operation_id`, status и диагностический код живут в `/var/lib/officium`, поэтому
рестарт Flask не теряет операцию. Повторный POST и конкурентный update
блокируются в root-службе. Кнопка принимает только последний stable release;
конкретный тег — только из root CLI (`--start-tag`).

Низкоуровневые команды runner (их вызывает `officium-update`):

```bash
sudo /usr/local/sbin/officium-update-runner --check       # в т.ч. connect() от APP_USER и версии компонентов
sudo /usr/local/sbin/officium-update-runner --components  # runner/client/deploy-release против текущего релиза
sudo /usr/local/sbin/officium-update-runner --start-latest
sudo /usr/local/sbin/officium-update-runner --verify-health
sudo journalctl -fu 'officium-update-*'
```

`scripts/deploy.sh --release <tag>` — совместимая root-only оболочка. Она не
делает checkout текущего дерева.

## 6. Миграция старого `/opt/officium` или `/var/www/...` checkout

Рекомендуемый путь — `sudo officium-update --tag <tag>` (раздел 5.1): команда
сама соберёт параметры и запустит установщик из проверенной копии тега. Ручной
эквивалент:

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
- собирает release в соседнем `$APP_DIR.managed.*`; ссылка `.env` релиза сразу
  указывает на постоянный `$APP_DIR/shared/.env`, пути venv переписываются на
  финальный каталог;
- останавливает старую службу только после успешной сборки/migrations;
- переименовывает старый каталог в `$APP_DIR.legacy.<timestamp>` и активирует
  managed tree;
- рендерит unit с `python -m waitress`, убирает устаревшие drop-ins с
  `ExecStart=` (копия — в `$BACKUP_DIR/units.legacy.*`);
- при неуспешной проверке здоровья возвращает старый каталог, unit и drop-ins
  (код выхода 10, у `officium-update` — 1);
- после успеха: updater, проверка nginx, smoke-тест, затем `integration_check`.
  Сбой IPC — только предупреждение с командой исправления; в конце всегда
  печатается сводка.

Untracked пользовательские файлы остаются в сохранённом legacy-каталоге и не
подмешиваются в release. Проверите их вручную до удаления каталога.

## 7. Nginx, HTTPS, cookies и Telegram

`ENABLE_NGINX=true SERVER_NAME=finance.example.org` создаёт server block из
`deployment/nginx-officium.conf.in` и выполняет `nginx -t`. Waitress остаётся на
loopback. Получите сертификат обычным контролируемым способом, затем включите
redirect HTTP→HTTPS и оставьте `SESSION_COOKIE_SECURE=true`.

Статику (`/static/`) отдаёт приложение через proxy с `Cache-Control` и
`nosniff`. Не добавляйте `location /static/ { alias ...; }`: nginx (`www-data`)
не может читать дерево `root:officium 0750`, а alias на старый checkout
перестаёт работать после миграции. Legacy-миграция находит такие `alias`/`root`
в `/etc/nginx`, делает их копию и печатает точную команду исправления;
автоматически nginx правится только с `FIX_NGINX=true`. Smoke-тест проверяет
`/static/css/style.css` через публичный vhost (`Host:` из `server_name`).

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
sudo officium-update rollback                         # предыдущий релиз
sudo officium-update rollback --to /opt/officium/releases/<release-dir>
```

Откат идёт через runner с той же проверкой здоровья; при неудаче ссылка
`current` возвращается обратно. У релизов, собранных до исправления, ссылка
`.env` чинится на `shared/.env`. База данных не меняется. Прежний вариант:

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

Скрипт не пишет финансовые данные: проверяет version/commit, ссылку
`current/.env`, active units, `NoNewPrivileges`, импорт `run:app`, read-only
Alembic state, локальный HTTP, статику напрямую и через nginx (`--public-host`,
по умолчанию `auto`) и число critical journal events **текущего запуска**
сервиса (`InvocationID`), а не последних 10 минут. Любая неудача печатается как
`FAIL <проверка>: <причина>`, успех — `SMOKE PASS`. Секреты не печатаются.

## 11. Типовые сбои

Все случаи ниже воспроизводились на production при обновлении v0.0.7 → v0.0.9;
начиная с этой версии их ловят установщик, runner и `officium-update check`.

| Симптом | Причина | Что делать |
|---|---|---|
| `officium.service: Failed to execute .../venv/bin/waitress-serve: No such file or directory`, `status=203/EXEC`, бесконечный рестарт | console-скрипты venv хранят путь каталога сборки, а релиз после сборки перемещается | `sudo officium-update latest`: unit переходит на `python -m waitress`. Проверка: `sudo officium-update check` (строки `service command`, `venv paths`) |
| Drop-in `/etc/systemd/system/officium.service.d/*.conf` с `ExecStart=` перекрывает новый unit | ручной обход прошлого сбоя | установщик сохраняет его в `$BACKUP_DIR/units.*` и удаляет, если он запускает то же, что новый unit, или несуществующую/старую программу; иначе оставляет и предупреждает |
| `RuntimeError: SECRET_KEY must be ... in production` у `flask db ...`, smoke-теста, `integration_check` (`configuration_invalid`) | ссылка `releases/<tag>/.env` указывала на временный `/opt/officium.managed.<PID>` или на `current/.env` (ссылка сама на себя) | ссылка всегда абсолютная на `<APP_ROOT>/shared/.env`; проверка `realpath -e current/.env`. Для старого релиза: `sudo ln -sfn /opt/officium/shared/.env /opt/officium/releases/<release>/.env` (или `officium-update rollback`, он чинит ссылку сам) |
| `fatal: failed to stat '/root/officium-vX.Y.Z': Permission denied` при сборке | `runuser` наследует рабочий каталог, недоступный `APP_USER` | исправлено: все скрипты делают `cd /` |
| `PermissionError` у `officium-update-client` и у кнопки | `/run/officium` создавался как `root:root 0750` | `install-officium-updater.sh` ставит `/etc/tmpfiles.d/officium.conf`; проверка: `sudo officium-update-runner --check` |
| Установщик пишет PASS, а сайт лежит | одиночный `is-active` проходит во время крэш-лупа | здоровье = active + `NRestarts` не растёт 10 с + HTTP 200; иначе автоматический откат |
| Traceback `integration_check` после успешного рестарта, непонятно, обновился ли сервер | IPC проверялся раньше smoke-теста | порядок: health → smoke → IPC; сбой IPC — предупреждение, в конце всегда сводка |
| Сайт без стилей, CSS отдаёт 404 или 403 | `location /static/ { alias /opt/officium/static/; }` в nginx | закомментировать блок (команду печатает установщик) или `FIX_NGINX=true`; статику отдаёт приложение |
| Smoke-тест молча завершается или ещё 10 минут после починки пишет FAIL | `set -e` без сообщения; ошибки журнала считались за 10 минут | каждая проверка печатает своё имя и причину; ошибки считаются с текущего запуска |
| Кнопка падает с `sudo: The "no new privileges" flag is set`; старый runner отвечает `Release tag is unsafe or unsupported`; нет `officium-update-client` | updater из v0.0.7 и раньше | обновляться только `sudo officium-update`; `officium-update-runner --check` сообщает об устаревших и отсутствующих компонентах |
