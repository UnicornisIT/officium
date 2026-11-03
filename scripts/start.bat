@echo off
chcp 65001 > nul

cd /d "%~dp0.."

echo ================================
echo Запуск officium
echo ================================

if not exist .venv (
    echo Создаю виртуальное окружение...
    python -m venv .venv
    if errorlevel 1 exit /b 1
)

call .venv\Scripts\activate.bat

echo Устанавливаю зависимости...
python -m pip install -r requirements.txt
if errorlevel 1 (
    echo Не удалось установить зависимости. Приложение не запущено.
    exit /b 1
)

if not exist .env (
    echo Файл .env не найден.
    echo Создаю .env из .env.example...
    copy /Y .env.example .env > nul
    powershell -NoProfile -ExecutionPolicy Bypass -Command ^
      "$path = '.env'; $content = Get-Content -LiteralPath $path -Raw; " ^
      "$content = $content -replace '(?m)^OFFICIUM_ENV=.*$', 'OFFICIUM_ENV=development'; " ^
      "$content = $content -replace '(?m)^DB_ENGINE=.*$', 'DB_ENGINE=sqlite'; " ^
      "$content = $content -replace '(?m)^DEV_LOGIN_ENABLED=.*$', 'DEV_LOGIN_ENABLED=true'; " ^
      "$content = $content -replace '(?m)^FLASK_DEBUG=.*$', 'FLASK_DEBUG=true'; " ^
      "if ($content -notmatch '(?m)^HOST=') { $content += [Environment]::NewLine + 'HOST=127.0.0.1' + [Environment]::NewLine }; " ^
      "[IO.File]::WriteAllText($path, $content, (New-Object Text.UTF8Encoding($false)))"
    if errorlevel 1 (
        echo Не удалось подготовить локальный файл .env.
        exit /b 1
    )
    echo.
    echo Создан локальный .env для development и SQLite.
)

echo Обновляю схему локальной базы данных...
python -m flask --app run:app db upgrade
if errorlevel 1 (
    echo Не удалось применить миграции. Приложение не запущено.
    echo Проверьте настройки базы данных и сообщение об ошибке выше.
    exit /b 1
)

echo Запускаю приложение...
echo Откройте в браузере: http://127.0.0.1:5000
python run.py

pause
