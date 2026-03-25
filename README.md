# ChatGPT Telegram Bot

Бот полностью работает как чат с ChatGPT.

## Команды

- `/start`
- `/help`
- `/model` - показать текущую модель
- `/new` - очистить контекст диалога

## Запуск

```bash
cd /Users/rahman/Downloads/avitoBot
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# заполни TELEGRAM_BOT_TOKEN и OPENAI_API_KEY
python bot.py
```
