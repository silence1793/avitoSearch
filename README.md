# ChatGPT Telegram Bot

Бот работает как обычный чат с ChatGPT (без кнопок и меню старого проекта).

## Команды

- `/start`
- `/help`
- `/new` - очистить контекст диалога

Для генерации картинки просто напиши фразой:
`сгенерируй ...`

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
