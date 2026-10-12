# QA test plan

## Принципы

- Каждый production-инцидент имеет отдельный regression test.
- Coverage считается только по application-коду через `.coveragerc`: начальный
  блокирующий порог 65% при измеренной branch coverage 68%. Тесты и migrations
  не попадают в числитель; порог повышается по мере роста покрытия.
- SQLite unit tests не заменяют MySQL migrations/backup/restore.
- Flask test client не заменяет Waitress/systemd/Unix peer credentials.
- Browser checks фиксируют console/page/network errors, geometry и screenshots.
- Telegram mock подтверждает криптографию и маршруты, но не реальный iOS/Android.
- Production smoke только read-only; финансовые записи и load test — staging.

## Матрица

| Функция | Окружение | Сценарий | Ожидаемый результат | Доказательство |
|---|---|---|---|---|
| WSGI/Waitress | local + Ubuntu | quoted proxy headers, `run:app` | процесс стартует, `/login` 200, нет `Specify one application only` | unittest output, staging journal |
| Update IPC | unit + Ubuntu systemd | check/integration/status/start, wrong fields/UID | только allowlist; wrong peer/fields rejected | `test_server_update.py`, socket journal |
| Concurrent update | unit + staging | double POST и параллельные clients | одна operation, остальные `update_busy` | status JSON + Playwright trace |
| Atomic release | staging | git/pip/migration/restart/health failure | active link не меняется либо возвращается; operation_id/error сохранены | injected-failure log + symlink target |
| MySQL backup | MySQL 8.4 | user без PROCESS; dump and restore empty DB | nonempty dump, counts/sums match | `MYSQL_BACKUP_RESTORE_RESULT=PASS` |
| SQLite backup | unit/staging | active WAL | consistent backup API result | runner unit/integration log |
| Migrations | SQLite + MySQL | empty/baseline/ready/partial | safe state accepted; partial rejected | Alembic output |
| Monthly expenses | SQLite + MySQL staging | 29/30/31, leap, missed months, parallel/retry/user isolation | correct clamping, no duplicate | `test_monthly_expenses.py` + MySQL run |
| Money | unit + MySQL | Decimal rounding, partial/late/early payments, imports | cent-exact and isolated | financial/import tests |
| Telegram Login | unit | valid/expired/forged signed payload | only valid accepted | auth tests |
| Telegram Mini App | unit + browser mock | signed initData, replay/age/CSRF | secure session only | mini-app tests/trace |
| Telegram real | iOS/Android MANUAL | Login, Mini App, bot and reminder separately | all four effective states verified | manual checklist/video (not mock) |
| Contacts | unit + browser | CRUD, role, CSRF, XSS, unsafe URI, location/order | safe immediate publication | contact tests/trace |
| Admin UI | Chromium/Firefox/WebKit | 375/390/768/1024/1280/1440/1920, browser zoom 100/110/125/150/200%, light/dark, all routes | no page overflow/clipping/error; navbar becomes icon-only before labels wrap; login/empty states remain complete and symmetric | screenshots/traces/JUnit |
| Security | unit + audit | role/IDOR/CSRF/XSS/SQLi/link/cookies/dependencies | deny by default, no secrets | security tests + pip-audit JSON |
| Timer/reboot | Ubuntu VM | daily 00:05 timezone, Persistent, reboot | catch-up exactly once | systemctl list-timers + DB rows |
| Performance | disposable staging | 10/50/100 virtual users | thresholds set from staging resource profile; no prod traffic | k6/Locust report |

## Fault injection

Обязательные cases: MySQL без PROCESS; root-owned `static/images`; недостаток
места; GitHub недоступен; invalid migration; pip failure; crash staging checkout;
health timeout; Flask restart; duplicate/concurrent POST; stale helper protocol.
Каждый case проверяет не только error code, но неизменность active release и
наличие безопасной диагностики по `operation_id`.

## Browser visual review

Playwright сохраняет полностраничные screenshots как artifacts, но не обновляет
эталоны автоматически. После первого одобренного Ubuntu run владелец переносит
одобренные снимки в baseline отдельным review-коммитом; любое последующее
изменение сравнивается вручную до включения pixel gate. Отсутствие baseline в
текущем отчёте — `BLOCKED`, а не `PASS`.

## Performance thresholds

До измерения staging hardware пороги не выдумываются. После фиксации CPU/RAM/DB
профиля release checklist должен содержать p50/p95/p99, error rate и saturation
для 10/50/100 VU. Production load test запрещён.

Read-only профиль находится в `tests/performance/k6-readonly.js` и намеренно
отказывается запускаться без HTTPS staging URL и измеренных порогов:

```bash
STAGING_BASE_URL=https://qa.example.test \
STAGING_P95_MS=<принятый-p95> STAGING_MAX_ERROR_RATE=<принятый-rate> \
k6 run --summary-export=test-results/k6-summary.json tests/performance/k6-readonly.js
```
