# Функциональность "Ежемесячные расходы" — Руководство

## 📋 Обзор изменений

Добавлена функциональность для автоматического переноса расходов на следующие месяцы. Пользователь может отметить расход как "ежемесячный" (с иконкой замка), и этот расход будет автоматически создаваться на каждый новый месяц.

## 📝 Файлы, измененные или добавленные

### Модели и миграции
- **[app/models.py](app/models.py)** — Добавлены поля к модели `Expense`:
  - `is_monthly` (Boolean) — флаг, что расход ежемесячный
  - `monthly_group_id` (String) — UUID для связи всех копий одного расхода
  - `generated_from_id` (Integer, FK) — ссылка на исходный расход
  - `generated_for_month` (String, YYYY-MM) — месяц, для которого создана копия
  - `monthly_anchor_day` (SmallInteger) — исходный день 1–31, который не теряется при сокращённом феврале
  - `monthly_settings_updated_at` (DateTime) — версия настроек серии для будущих месяцев

- **[migrations/versions/20260517_add_monthly_expenses_fields.py](migrations/versions/20260517_add_monthly_expenses_fields.py)** — Миграция БД для добавления новых полей
- **[migrations/versions/20261009_monthly_settings.py](migrations/versions/20261009_monthly_settings.py)** — безопасно добавляет метаданные серии и индекс активных серий

### Логика приложения
- **[app/routes/expenses.py](app/routes/expenses.py)** — Обновлена логика создания и редактирования расходов:
  - При создании расхода проверяется флаг `is_monthly`
  - При ежемесячном расходе создается `monthly_group_id` (UUID)
  - При редактировании можно включать/выключать флаг ежемесячности

- **[app/services/monthly_expenses_service.py](app/services/monthly_expenses_service.py)** — Новая сервисная функция:
  - `generate_monthly_expenses()` — генерирует копии расходов на новый месяц
  - `create_monthly_expenses_for_new_month()` — удобная функция для периодического вызова
  - Логика обработки дней месяца (31 января → 28 февраля)
  - Проверка дубликатов (идемпотентность)

### Интерфейс пользователя
- **[templates/expenses.html](templates/expenses.html)** — Обновлена форма и таблица расходов:
  - Добавлен переключатель "Ежемесячный расход" с иконкой замка
  - В таблице истории показывается замок для ежемесячных расходов

- **[static/css/style.css](static/css/style.css)** — Минимальные CSS стили для переключателя и иконки замка

### Тесты
- **[tests/test_monthly_expenses.py](tests/test_monthly_expenses.py)** — Автоматические тесты календаря, истории и идемпотентности:
  - Обычный расход создается без флага ежемесячности
  - Ежемесячный расход сохраняется с `monthly_group_id`
  - При генерации следующего месяца создается копия
  - Повторный запуск генерации не создает дубликаты
  - День месяца сохраняется корректно
  - День 31 корректно обрабатывается для месяцев с <31 днями
  - Генерация работает для конкретного пользователя
  - Все поля расхода копируются корректно

### CLI и утилиты
- **[app/__init__.py](app/__init__.py)** — Добавлена CLI команда `generate-monthly-expenses`

- **[deployment/officium-monthly-expenses.timer](../deployment/officium-monthly-expenses.timer)** — production-расписание systemd с `Persistent=true` и ежедневным восстановительным запуском

- **[requirements.txt](requirements.txt)** — Добавлена зависимость `python-dateutil`

## 🚀 Как применить функциональность

### 1. Применить миграцию базы данных

```bash
# Linux/Mac
flask db upgrade

# Windows
python -m flask db upgrade
```

Или если используется alembic напрямую:
```bash
alembic upgrade head
```

### 2. Обновить dependencies

```bash
pip install python-dateutil
```

## 📖 Как использовать

### Создание ежемесячного расхода

1. На странице `/expenses` заполните форму:
   - Категория: например "Аренда"
   - Название: "Арендная плата"
   - Сумма: 25000
   - Дата: 15 число каждого месяца
   - Включите переключатель "Ежемесячный расход" 🔒

2. Нажмите "Сохранить расход"

3. Расход будет автоматически переноситься на следующие месяцы

### Редактирование ежемесячного расхода

1. Нажмите "Изм." на нужном расходе
2. Измените поля (сумму, названиеи т.д.)
3. Переключатель "Ежемесячный расход" всегда доступен
4. Нажмите "Сохранить изменения"

Изменённая запись становится снимком настроек для **будущих** месяцев. Уже
созданные записи других месяцев не переписываются. Изменение даты меняет день
будущего повторения; например, после выбора 31-го февраля используется последний
день месяца, а в марте снова будет 31-е.

### Отключение ежемесячности

1. Отредактируйте расход
2. Отключите переключатель "Ежемесячный расход"
3. Новые расходы не будут создаваться в следующих месяцах

## 🔧 CLI Команды

### Генерировать ежемесячные расходы для текущего месяца

```bash
flask generate-monthly-expenses
```

### Генерировать для конкретного пользователя

```bash
flask generate-monthly-expenses --user-id 123
```

### Генерировать за конкретный месяц

```bash
flask generate-monthly-expenses --month 2026-06
```

### Генерировать за конкретный месяц для конкретного пользователя

```bash
flask generate-monthly-expenses --user-id 123 --month 2026-06
```

Команда заполняет все отсутствующие месяцы от начала каждой активной серии до
целевого месяца, поэтому подходит для восстановления после простоя. Повторный и
параллельный запуск безопасны: используется уникальность
`(user_id, monthly_group_id, generated_for_month)` на уровне БД.

## ⏱ Production-расписание

Не запускайте генератор фоновым потоком Flask/Waitress. На Linux установите
поставляемые systemd units (пути и пользователя при необходимости замените):

```bash
sudo install -o root -g root -m 0644 deployment/officium-monthly-expenses.service /etc/systemd/system/
sudo install -o root -g root -m 0644 deployment/officium-monthly-expenses.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now officium-monthly-expenses.timer
sudo systemctl start officium-monthly-expenses.service
systemctl list-timers officium-monthly-expenses.timer --no-pager
journalctl -u officium-monthly-expenses.service -n 100 --no-pager
```

Timer запускается ежедневно в 00:05 `Europe/Moscow`. Ежедневный запуск нужен как
повторная попытка после сбоя; новые строки всё равно создаются только для ещё не
заполненных календарных месяцев. `APP_TIMEZONE` в `.env` должен совпадать с
временной зоной расписания.

## 🧪 Запуск тестов

```bash
# Все тесты ежемесячных расходов
python -m unittest tests.test_monthly_expenses -v

# Конкретный тест
python -m unittest tests.test_monthly_expenses.MonthlyExpensesTestCase.test_generate_monthly_expenses_no_duplicates -v
```

В конце ожидается `OK` без ошибок.

## ⚙️ Техническая информация

### Логика генерации

1. **Проверка дубликатов:** Перед созданием копии проверяется, что для данного `monthly_group_id` и месяца запись еще не существует
2. **Сохранение дня:** Если в исходном расходе 15 число, копия создается на 15 число нового месяца
3. **Обработка переполнения:** Если день не существует (31 янв → февраль), используется последний день месяца
4. **Копируемые поля:** Сумма, категория, название, способ оплаты, комментарий, пользователь
5. **Отслеживание:** В копии сохраняется `generated_from_id` (ссылка на исходный расход) и `generated_for_month` (месяц, для которого создана)

### Архитектура

- Сервисный слой ([app/services/monthly_expenses_service.py](app/services/monthly_expenses_service.py)) содержит бизнес-логику
- Маршруты ([app/routes/expenses.py](app/routes/expenses.py)) обрабатывают UI и создание расходов
- CLI команда позволяет запускать генерацию вручную или по расписанию
- Все операции идемпотентны (безопасны для повторного запуска)

## 🎨 UX Улучшения

- 🔒 Иконка замка показывает ежемесячные расходы в таблице
- 📋 Переключатель с надписью "Ежемесячный расход"
- ⚠️ Никаких разрывов существующей функциональности
- 🌙 Полная совместимость с темной темой

## ❌ Возможные проблемы и решения

### Миграция не применяется

```bash
# Проверьте версию Alembic
flask db current

# Примените миграции
flask db upgrade

# Или вручную через Alembic
alembic upgrade head
```

### Команда `flask generate-monthly-expenses` не работает

```bash
# Проверьте, что приложение видит команду
flask --help | grep generate-monthly

# Убедитесь, что FLASK_APP установлена
set FLASK_APP=run.py
flask generate-monthly-expenses
```

### Тесты не запускаются

```bash
# Убедитесь, что python-dateutil установлена
pip install python-dateutil

# Запустите тесты
python -m unittest tests.test_monthly_expenses -v
```

---

**Версия:** 2.0

**Дата:** 9 октября 2026 г.

**Статус:** production-ready после применения миграции и включения systemd timer
