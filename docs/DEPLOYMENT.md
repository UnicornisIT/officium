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
  shared/                   # root:officium 0750; службе доступен через ReadWritePaths
  shared/.env               # root:officium 0640, OFFICIUM_INSTANCE_PATH=<APP_DIR>/shared/instance
  shared/instance/          # officium:officium 0750 — единственный каталог записи приложения
  shared/instance/bank_imports/  # officium 0700, предпросмотры импорта (раздел 12)
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
| HTTP 500 при импорте выписки, в журнале `OSError: [Errno 30] Read-only file system` или `PermissionError` в `.../releases/<release>/instance/bank_imports` | до исправления предпросмотр писался в `current_app.instance_path` = каталог релиза, а он root-owned, без write bits и закрыт `ProtectSystem=strict` | исправлено: приложение пишет в `shared/instance` (раздел 12). Если `shared/instance` нет, импорт отвечает 503 с пояснением; создать каталоги: `sudo officium-update latest` или `install-officium.sh units` |

## 12. Ресурсы сервера: Free Tier (1 vCPU, 1 ГиБ RAM, 10 ГБ диск)

Значения по умолчанию рассчитаны на этот тариф. Цифры ниже помечены, откуда
они взяты; на production они не измерялись.

### 12.1. Хранилище импорта

- Каталог: `OFFICIUM_INSTANCE_PATH` (установщик пишет
  `<APP_DIR>/shared/instance` в `shared/.env`). Если переменной нет, релиз с
  маркером `.officium-release-ready` сам находит `../../shared/instance` —
  так работают установки, обновлённые кнопкой до появления переменной.
  Локальная разработка по-прежнему использует `<checkout>/instance`.
- Внутри `bank_imports/` (0700): `u<user id>-<token>.json` — предпросмотр,
  `*.json.claimed` — подтверждение идёт сейчас, `.tmp-*` — запись идёт.
  В файле нет содержимого выписки: владелец, время, число строк и id
  ежемесячного расхода для строк, которые его обновят (сотни байт).
  Названия и суммы, как и раньше, приходят в форме подтверждения.
- Запись атомарная (`.tmp-*` → `fsync` → `rename`); подтверждение сначала
  переименовывает файл в `.claimed`, поэтому второй запрос с тем же токеном
  получает 410/409 и дубликатов не создаёт. Ошибка проверки или БД
  возвращает файл обратно — форму можно исправить и отправить ещё раз.
  Токен чужого пользователя не находится (имя файла содержит user id).
- Ошибки: нет места / квота → 507, каталог недоступен (`EACCES`, `EROFS`),
  разбор другой выписки → 503, файл больше `MAX_CONTENT_LENGTH` → 413,
  предпросмотр устарел или уже подтверждён → 410. В журнал пишутся errno,
  тип исключения и стек — без текста исключения, где могут быть данные
  выписки.

| Переменная `.env` | По умолчанию | Назначение |
|---|---|---|
| `MAX_CONTENT_LENGTH` | 10 МиБ | размер запроса (не меняется); `client_max_body_size 10m` в nginx |
| `IMPORT_PREVIEW_TTL_MINUTES` | 240 | срок жизни неподтверждённого предпросмотра |
| `IMPORT_STORAGE_MAX_BYTES` / `_MAX_FILES` | 32 МиБ / 500 | квота всего каталога (запас ×1000 к реальному размеру файлов) |
| `IMPORT_MAX_PENDING_PER_USER` | 5 | новая загрузка удаляет самые старые неподтверждённые предпросмотры этого пользователя |
| `IMPORT_DISK_RESERVE_BYTES` | 512 МиБ | меньше свободного места — новый предпросмотр не пишется (БД, журналы, ОС) |
| `IMPORT_MAX_CONCURRENT_PARSES` / `IMPORT_PARSE_WAIT_SECONDS` | 1 / 20 | одновременных разборов на процесс; ожидание слота до ответа 503 |
| `IMPORT_MAX_ROWS` | 500 | расходов в одном предпросмотре (как раньше) |
| `IMPORT_MAX_SOURCE_ROWS` | 20 000 | строк таблицы/PDF, которые вообще читаются (годовая выписка — 1–5 тыс.) |
| `DISK_WARN_FREE_BYTES` / `DISK_CRITICAL_FREE_BYTES` | 1,5 ГиБ / 750 МиБ | пороги `flask storage-check` |

Предварительная проверка свободного места не гарантирует успешную запись:
`ENOSPC` во время записи обрабатывается отдельно, недописанный файл удаляется.

### 12.2. Автоматическая очистка

`officium-maintenance.timer` раз в час (без постоянно работающего процесса)
запускает `flask import-cleanup` и `flask storage-check` от `APP_USER` с той
же защитой systemd, что у веб-службы. Удаляются **только** обычные файлы с
именами из 12.1 (симлинки и каталоги не трогаются) старше TTL, у `.tmp-*`
— старше 15 минут; `.claimed` защищён TTL с момента начала подтверждения.
То же выполняется при каждом старте службы. Базы, резервные копии,
`shared/.env` и неизвестные файлы не удаляются никогда.

```bash
systemctl list-timers officium-maintenance.timer
sudo systemctl start officium-maintenance.service      # внеочередной запуск
journalctl -u officium-maintenance.service --since today
```

При свободном месте ниже критического порога `storage-check` пишет запись
`CRITICAL` и завершается с кодом 2 — unit виден в `systemctl --failed`.

### 12.3. Обновление: проверка ресурсов, хранение релизов

До резервной копии и сборки runner считает потребность по реальным размерам:
новый релиз = размер `current` × 1,25 (не меньше 256 МиБ), резервная копия =
SQLite-файл × 1,1 или последний `officium-*.sql` × 1,25 (иначе каталог базы в
`/var/lib/mysql`), плюс `UPDATE_MIN_FREE_MB` (750 МиБ), который должен
остаться свободным. Если релизы и копии на одной файловой системе, суммы
складываются. Память: `MemAvailable + SwapFree ≥ UPDATE_MIN_MEMORY_MB`
(200 МиБ), из них не меньше половины — RAM. Не хватает — статус `failed`,
код `insufficient_resources`, работающий релиз и база не тронуты.

Зависимости ставятся только из готовых wheel (`--only-binary=:all:`,
`--no-cache-dir`): компилятор на сервере не нужен, кэш pip не растёт.
Проверено `pip download` для Python 3.10/3.12, x86_64 и aarch64: wheel есть
у всех 38 пакетов.

После успешного обновления удаляются релизы сверх `RELEASES_KEEP=3`.
Текущий и предыдущий (цель автоматического отката) не удаляются никогда;
среди остальных первыми уходят релизы, ни разу не прошедшие проверку
здоровья (`/var/lib/officium/release-history.json`), например откатившийся.
Удаляются только каталоги `<tag>-<12 hex>` с маркером и владельцем root, а
также `.build-*`/`.staging-*` старше 6 часов.

```bash
sudo officium-update-runner --disk-report          # бюджет диска, уровень ok/warning/critical
sudo officium-update-runner --check-resources      # хватит ли ресурсов на обновление
sudo officium-update-runner --prune-releases --dry-run
```

### 12.4. Резервные копии

Перед каждым обновлением runner делает дамп базы и копию конфигурации в
`/var/backups/officium` и **не удаляет их автоматически**. Требования:

- держать не меньше двух последних копий базы (последняя и предыдущая
  удачная) и хотя бы одну копию вне сервера — на том же диске копия не
  защищает от потери диска;
- перед очисткой проверить копию: `mysql` в пустую тестовую базу или
  `sqlite3 <file> 'PRAGMA integrity_check'`;
- удалять старые копии вручную, после проверки и выгрузки:
  `sudo ls -lt /var/backups/officium`.

Пока политика хранения не согласована, копии учитываются в `--disk-report`
и в проверке места перед обновлением.

### 12.5. Бюджет памяти

Измерения на синтетических данных (Windows dev-машина, RSS процесса
Waitress, SQLite; на Linux значения близки, но это не production):

| Сценарий | Пик RSS |
|---|---|
| после запуска | 89–90 МиБ |
| 55 последовательных и 88 параллельных запросов страниц | 94–96 МиБ |
| импорт 500 строк CSV и подтверждение, повторный импорт | 97–105 МиБ |
| XLSX (лист 4 МиБ), PDF 8 и 200 страниц | 98–111 МиБ |
| CSV 9,5 МиБ, отклонён по лимиту строк | 135 МиБ |
| два тяжёлых предпросмотра одновременно | 114 МиБ |
| разбор XLSX у предела 50 МиБ: до / после потокового разбора | 819 → 73 МиБ |

Бюджет 1 ГиБ (оценка: ОС и nginx по типичным значениям Ubuntu 24.04,
MySQL — по настройкам ниже, приложение — по измерениям): ядро и системные
службы 150–200 МиБ, nginx 10–20 МиБ, MySQL 250–330 МиБ, Waitress до
140 МиБ, таймер обслуживания до 90 МиБ раз в час, сборка релиза (pip) —
порядка 100–150 МиБ во время обновления. Пик ≈ 700–800 МиБ из 960–990 МиБ,
доступных на «1 ГБ» VPS; это работоспособно, но без большого запаса.

Рекомендации:

- Waitress: оставить `--threads=4`. Замер 4 и 2 потоков: разница RAM в
  пределах шума (< 5 МиБ), параллельные страницы с 4 потоками быстрее
  (2,4 с против 4,1 с). Память ограничивает слот разбора, а не число потоков.
- MySQL на том же сервере (`/etc/mysql/mysql.conf.d/zz-officium.cnf`):
  `innodb_buffer_pool_size=64M`, `performance_schema=OFF`,
  `max_connections=30`, `key_buffer_size=8M`, `tmp_table_size=16M`,
  `max_heap_table_size=16M`, `innodb_log_buffer_size=8M`. Значения по
  умолчанию MySQL 8 (buffer pool 128 МиБ + performance_schema ≈ 400+ МиБ)
  съедают половину RAM. Это изменение production, применять отдельно.
- Swap 512 МиБ: имеет смысл как защита от OOM-kill во время обновления
  (pip, миграции), но не как память для работы. Перед созданием проверить
  место (`df -h /`): 512 МиБ из 10 ГБ допустимы, если после этого свободно
  больше 1,5 ГиБ. Рекомендуемо `vm.swappiness=10`. Автоматически не
  создаётся.
- Журналы: `SystemMaxUse=200M` в `/etc/systemd/journald.conf.d/officium.conf`
  (по умолчанию journald может занять до 10 % диска, т.е. до 1 ГБ).

### 12.6. Бюджет диска (10 ГБ)

| Что | Размер | Источник |
|---|---|---|
| Ubuntu 24.04 server minimal + пакеты (python3-venv, nginx, mysql-server, git) | 3–4 ГБ | оценка, проверить `df -h /` |
| один релиз (код 4 МиБ + `.git` 4 МиБ + venv 58 МиБ без байткода) | 85–100 МиБ | измерено по wheel для Linux |
| три релиза (`RELEASES_KEEP=3`) | 260–300 МиБ | расчёт |
| временно при обновлении: новый релиз + сборка | до 256 МиБ | оценка runner |
| `shared/instance/bank_imports` | килобайты, максимум 32 МиБ | квота |
| MySQL данные + binlog | зависит от данных; binlog — `binlog_expire_logs_seconds=259200` | проверить `du -sh /var/lib/mysql` |
| резервные копии | (размер дампа) × число копий | `--disk-report` |
| journald | до 200 МиБ при рекомендации выше | `journalctl --disk-usage` |

Пороги: меньше 1,5 ГиБ свободно — предупреждение, меньше 750 МиБ —
критично и обновление не начинается. После измерений на сервере пороги
уточняются переменными из 12.1 и 12.3.

### 12.7. Достаточно ли Free Tier

Для нескольких пользователей при MySQL с настройками из 12.5 — да, но с
небольшим запасом по RAM: одновременно работают только один разбор выписки
и одна сборка обновления. Без настройки MySQL (значения по умолчанию) и без
swap запас памяти во время обновления близок к нулю. Если после измерения
на сервере `MemAvailable` в покое меньше 300 МиБ или во время обновления
случается OOM-kill, минимальное увеличение — 2 ГиБ RAM (диск 10 ГБ
достаточен при ручной ротации резервных копий).

### 12.8. Проверка после развёртывания

```bash
sudo bash /opt/officium/current/deployment/post-deploy-smoke.sh --service officium \
  --app-dir /opt/officium/current --url http://127.0.0.1:5000/login --timer officium-monthly-expenses.timer
grep OFFICIUM_INSTANCE_PATH /opt/officium/shared/.env
sudo stat -c '%U:%G %a %n' /opt/officium/shared/instance /opt/officium/shared/instance/bank_imports
systemctl list-timers officium-maintenance.timer
sudo officium-update-runner --disk-report
journalctl -u officium --since today | grep -E 'Import storage|bank_imports|Traceback'
```

Затем в браузере: «Расходы» → «Импорт выписки» → небольшой файл → проверить
предпросмотр → подтвердить; повторная загрузка того же файла помечает строки
как дубликаты. После подтверждения в `bank_imports/` файла этого импорта нет.
