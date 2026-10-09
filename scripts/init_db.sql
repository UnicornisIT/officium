-- Creates only the MySQL database container.
-- The application schema is owned exclusively by Alembic migrations.
-- After this script, configure .env and run: python -m flask db upgrade

CREATE DATABASE IF NOT EXISTS debt_manager
    CHARACTER SET utf8mb4
    COLLATE utf8mb4_unicode_ci;
