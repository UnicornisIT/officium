# Этап 6: Telegram-напоминания

Реализованы ежедневная сводка по пробегу, обслуживание по автомобилям,
документы по отдельности. Команд ввода нет. Плашка дашборда не добавлялась.

## Запуск

После миграции `flask db upgrade` задайте в shared/.env:

```dotenv
VEHICLES_ENABLED=true
VEHICLE_NOTIFICATIONS_ENABLED=true
VEHICLE_NOTIFICATIONS_HOUR=10
VEHICLE_NOTIFICATIONS_BASE_URL=https://your-domain.example
APP_TIMEZONE=Europe/Moscow
TELEGRAM_BOT_TOKEN=<токен>
```

Базовый URL иначе берётся из OFFICIUM_PUBLIC_URL. Настройка по умолчанию
выключена, существующим пользователям предпочтение включается миграцией.
`flask send-vehicle-notifications` выполняет пакетную отправку; альтернативно
POST /internal/vehicle-notifications/run (только peer 127.0.0.1).
В reverse proxy запретите внешний доступ к /internal/ (например,
`location /internal/ { deny all; }`), не переписывайте WSGI REMOTE_ADDR
из непроверенных заголовков.

Таймер использует CLI по образцу monthly-expenses, не требует HTTP-сервера.
Установка после основного installer:

```bash
sudo env VEHICLE_NOTIFICATIONS_ENABLED=true VEHICLE_NOTIFICATIONS_HOUR=10 APP_TIMEZONE=Europe/Moscow bash deployment/install-vehicle-notifications.sh
systemctl list-timers officium-vehicle-notifications.timer
```

APP_ROOT, APP_USER, APP_GROUP, VENV_DIR должны совпадать с основной установкой.
Параметры часа и зоны передайте установщику такими же, как в .env; после
изменения переустановите таймер. Persistent=true догоняет пропущенный запуск.
Для выключения вызовите установщик с VEHICLE_NOTIFICATIONS_ENABLED=false
и выключите флаг в .env. CLI также проверяет флаг при каждом запуске.

## Семантика

Хэш документа включает ID документа: kind|vehicle_id|document_id|local_date.
Для остальных типов: kind|vehicle_id|local_date (None для сводки).
Резервирование с unique constraint коммитится перед Telegram API. Ошибка
или авария между резервированием и отправкой не вызывает повтор в тот же
день; следующий день допускает новую попытку. Это гарантия не более одной
попытки в день, а не гарантированной доставки. Смена дня во всём сборе
использует одно значение now в APP_TIMEZONE.

Обслуживание перечисляет только активные позиции soon/overdue. Если общий
technical_condition повышен только документами, пустое сообщение об
обслуживании не отправляется. Документы: включительно 0–14 дней.

## Проверка

Автотесты: tests/test_vehicle_notifications.py,
tests/test_vehicle_notification_runner.py,
tests/test_vehicle_notification_migration.py.
Проверяют сбор, границы документов, архив, приватность, дневной хэш,
идемпотентность, ошибки API, профиль, source IP и миграции SQLite/MySQL DDL.

Ручная проверка на сервере остаётся обязательной:
- [ ] systemd-analyze verify на отрендеренных юнитах, list-timers и Persistent.
- [ ] Получить тестовые сообщения с вымышленными данными в Telegram и
      приложить реальные скриншоты. Из этой сессии сообщения не отправлялись.
- [ ] Профиль: 390/1440 px, обе темы.

Скриншоты и успешный запуск systemd не имитируются.

## Результаты 2026-11-01

- [x] 7 целевых проверок: сбор/границы/приватность/архив,
  дневная идемпотентность и ошибка API, предпочтение профиля,
  source IP внутреннего runner, пакетная загрузка/snooze,
  SQLite upgrade существующего пользователя + metadata check,
  MySQL DDL и forward-only downgrade.
- [x] bash -n deployment/install-vehicle-notifications.sh.
- [x] git diff --check.
- [ ] systemd-analyze недоступен на Windows (WSL не установлен).
