#!/bin/bash
# Запуск Discord бота
# Установите зависимости: pip install -r requirements.txt
# Настройте config.json: вставьте токен бота

cd "$(dirname "$0")"

# Проверка зависимостей
if ! python3 -c "import discord" 2>/dev/null; then
    echo "Установка зависимостей..."
    pip install -r requirements.txt
fi

echo "Запуск бота..."
python3 main.py
