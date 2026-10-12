# Release checklist

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
- [ ] Telegram Login/Mini App/bot synthetic security PASS.
- [ ] Telegram iOS/Android real checks отмечены MANUAL с доказательством.
- [ ] Chromium/Firefox/WebKit × все viewports/themes/routes PASS; screenshots reviewed.
- [ ] Contacts CRUD/role/CSRF/XSS/unsafe URI/publication PASS.
- [ ] Dependency audit имеет 0 blocking findings либо оформлен risk acceptance.
- [ ] Performance 10/50/100 VU проведён только на staging и приложены metrics.
- [ ] Read-only post-deploy smoke PASS, critical journal errors = 0.
- [ ] QA report содержит команды, commit, артефакты, PASS/FAIL/BLOCKED/NOT RUN/MANUAL.
- [ ] Production deployment и release publication отдельно подтверждены владельцем.
