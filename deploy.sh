#!/bin/bash

set -e

cd /var/www/debt_manager

echo "Обновляем код из GitHub..."
git pull origin master

echo "Активируем виртуальное окружение..."
source venv/bin/activate

echo "Устанавливаем зависимости..."
pip install -r requirements.txt

echo "Применяем миграции базы данных..."
export FLASK_APP=run.py
stamp_needed=$(python - <<'PY'
from app import app
from extensions import db
from sqlalchemy import inspect

with app.app_context():
    inspector = inspect(db.engine)
    tables = inspector.get_table_names()
    has_alembic = 'alembic_version' in tables
    has_schema_tables = bool([t for t in tables if t != 'alembic_version'])
    if has_schema_tables and not has_alembic:
        print('yes')
    else:
        print('no')
PY
)
if [ "$stamp_needed" = "yes" ]; then
    echo "Существующая схема найдена, ставим метку initial migration 73459c8513a1"
    flask db stamp 73459c8513a1
else
    echo "Шаблон метки alembic_version не требуется или уже установлен."
fi
flask db upgrade

echo "Перезапускаем сервис..."
sudo systemctl reset-failed debt_manager
sudo systemctl restart debt_manager

echo "Статус сервиса:"
sudo systemctl status debt_manager --no-pager