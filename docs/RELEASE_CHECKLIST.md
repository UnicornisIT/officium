# Release checklist

## Именование альфа-релизов

- Работать в текущей ветке. Создание или переименование ветки, коммит,
  push, тег и публикация требуют прямого указания владельца.
- Тег: `vX.Y.Z-alpha` (например, `v0.0.14-alpha`); `VERSION` содержит тот же
  тег, заголовок в `CHANGELOG.md` — `## [X.Y.Z-alpha] - ГГГГ-ММ-ДД`.
- Заголовок релиза на GitHub: `Release vX.Y.Z-alpha`.
- Публиковать как обычный релиз, **без** галочки «Set as a pre-release».
  Кнопка обновления, `officium-update-runner` и `scripts/officium-update`
  берут релиз из `GET /repos/{owner}/{repo}/releases/latest`, а GitHub не
  отдаёт через него pre-release: такой релиз сервер не увидит.
- Стадию сервер определяет по суффиксу тега: на странице обновления
  показываются метка «Альфа-версия» и предупреждение перед установкой.
  `vX.Y.Z-alpha` считается новее `vX.Y.(Z-1)` и старше стабильного `vX.Y.Z`.

Stable release запрещён, пока любой критический пункт имеет `FAIL`, `BLOCKED`
или `NOT RUN`. Release candidate допустим только с явно опубликованными
ограничениями и без production deployment.

- [ ] Чистая Ubuntu VM: install без ручного edit unit/path/owner/SQL.
- [ ] `systemd-analyze verify` и effective app unit используют `run:app`, один quoted proxy-header argument, loopback и `NoNewPrivileges=yes`.
- [ ] Legacy v0.0.6 → v0.0.7 → candidate проверен с нестандартным APP_DIR.
- [ ] Superadmin нажал реальную кнопку: base QA release → latest QA release через IPC.
- [ ] Flask не имеет sudo/root, wrong peer и protocol spoofing отклонены.
- [ ] Restricted MySQL user без PROCESS: dump nonempty, restore empty DB, counts/sums match.
- [ ] Failure injection не меняет active release; restart/health failure возвращает link.
- [ ] Ограничения rollback MySQL DDL проверены и приняты владельцем.
- [ ] SQLite WAL backup восстановлен.
- [ ] Alembic SQLite + MySQL single head/check PASS.
- [ ] Monthly timer после reboot/catch-up не создаёт duplicate.
- [ ] Финансы/import/debts/payments/Decimal/user isolation PASS.
- [ ] `/profile`: независимые формы, CSRF, изоляция, IANA/DST и rate limit теста бота PASS.
- [ ] В одной карточке «Уведомления» сначала ежемесячные расходы, затем автомобили;
  между подразделами разделитель, настройки сохраняются независимо.
- [ ] Миграция `20261124_monthly_reminders`: upgrade/downgrade/upgrade без потери
  существующих расходов и настроек; один Alembic head и `flask db check` PASS.
- [ ] Ежемесячные напоминания: короткие месяцы, часовые пояса, окно 6 часов,
  сводное сообщение, журнал успешных отправок, параллельные запуски и dry-run PASS.
- [ ] Первый деплой и повторная установка сохраняют существующий `shared/.env`;
  прокси в шаблоне закомментирован, настроенные прокси и receive mode не меняются.
- [ ] Webhook и polling: setup сохраняет очередь; включена только нужная служба;
  обновление, первый переход со старого updater и откат восстанавливают службы.
- [ ] При токене включается monthly-reminders timer независимо от команд бота
  и автомобильных уведомлений; без токена выключен, дублирующего cron нет.
- [ ] Скрытие сумм: начальный рендер, динамические данные/подсказки/графики, клавиатура; ввод и CSV не изменены.
- [ ] Установлены миграции идентификаторов автомобиля и личных настроек, `tzdata`; напоминания проверяются каждые 5 минут.
- [ ] Все иллюстрации `/documentation` пересняты на фиктивных данных, манифест и версии URL обновлены, 12 разделов доступны.
- [ ] Telegram Login/Mini App/bot synthetic security PASS.
- [ ] Команды бота включаются и выключаются из панели без перезапуска;
  уведомления работают независимо; webhook и polling учитывают настройку из БД.
- [ ] Обращения: подтверждение, отмена, защита от повторных и чужих подтверждений,
  роли администратора, CSRF/XSS, фильтры, статусы и мобильная вёрстка PASS.
- [ ] Миграция `20261125_feedback`: upgrade/check/downgrade без потери существующих
  пользователей; один Alembic head и `flask db check` PASS.
- [ ] Telegram iOS/Android real checks отмечены MANUAL с доказательством.
- [ ] Chromium/Firefox/WebKit × все viewports/themes/routes PASS; screenshots reviewed.
- [ ] Contacts CRUD/role/CSRF/XSS/unsafe URI/publication PASS.
- [ ] Dependency audit имеет 0 blocking findings либо оформлен risk acceptance.
- [ ] Performance 10/50/100 VU проведён только на staging и приложены metrics.
- [ ] Read-only post-deploy smoke PASS, critical journal errors = 0.
- [ ] QA report содержит команды, commit, артефакты, PASS/FAIL/BLOCKED/NOT RUN/MANUAL.
- [ ] Production deployment и release publication отдельно подтверждены владельцем.
