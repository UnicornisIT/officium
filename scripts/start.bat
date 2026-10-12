@echo off
chcp 65001 > nul

cd /d "%~dp0.."

echo ================================
echo Запуск officium
echo ================================

if not exist .venv (
    echo Создаю виртуальное окружение...
    python -m venv .venv
)

call .venv\Scripts\activate.bat

echo Устанавливаю зависимости...
python -m pip install -r requirements.txt

if not exist .env (
    echo Файл .env не найден.
    echo Создаю .env из .env.example...
    copy /Y .env.example .env > nul
    powershell -NoProfile -ExecutionPolicy Bypass -Command ^
      "$path = '.env'; $content = Get-Content -LiteralPath $path -Raw; " ^
      "$content = $content -replace '(?m)^OFFICIUM_ENV=.*$', 'OFFICIUM_ENV=development'; " ^
      "$content = $content -replace '(?m)^DB_ENGINE=.*$', 'DB_ENGINE=sqlite'; " ^
      "$content = $content -replace '(?m)^DEV_LOGIN_ENABLED=.*$', 'DEV_LOGIN_ENABLED=true'; " ^
      "Set-Content -LiteralPath $path -Value $content -Encoding UTF8 -NoNewline"
    if errorlevel 1 (
        echo Не удалось подготовить локальный файл .env.
        exit /b 1
    )
    echo.
    echo Создан локальный .env для development и SQLite.
)

echo Запускаю приложение...
echo Откройте в браузере: http://127.0.0.1:5000
python run.py

pause
