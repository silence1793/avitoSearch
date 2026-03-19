# avitoSearch

Telegram-бот для постоянного мониторинга новых объявлений на Avito по запросу, который задается прямо в чате.

## Как пользоваться

Пиши боту обычным текстом, например:

- `найди мне playstation 5 slim за 36000 рублей в дагестане`

Бот сам разберет:
- товар (`playstation 5 slim`)
- максимальную цену (`36000`)
- регион (`дагестан`)

И начнет мониторинг по этой ссылке.

## Команды

- `/track <запрос>` - тоже работает (если удобнее)
- `/stop` - остановить мониторинг
- `/status` - текущий статус
- `/interval <сек>` - интервал проверки (в мягком режиме минимум 600)
- `/help` - помощь

Если отправить обычный текст, бот воспримет его как новый поисковый запрос.

## Локальный запуск

```bash
cd /Users/rahman/Downloads/avitoBot
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# укажи TELEGRAM_BOT_TOKEN
python bot.py
```

## Поведение

1. Бот получает команды через Telegram API (`getUpdates`).
2. Сохраняет текущий запрос в SQLite.
3. Опрашивает Avito по интервалу.
4. На первом запуске для запроса делает тихую инициализацию без спама.
5. Далее отправляет только новые объявления аккуратной карточкой: фото + кнопка перехода на Avito.

## Мягкий режим

По умолчанию включен `SOFT_MODE=true`:
- минимальный интервал проверки: 600 секунд;
- добавляется случайный джиттер между проверками;
- при 429 бот делает длинную паузу и пробует позже.

## Продакшен (systemd)

```ini
[Unit]
Description=Avito Search Bot
After=network.target

[Service]
Type=simple
WorkingDirectory=/opt/avitoSearch
ExecStart=/opt/avitoSearch/.venv/bin/python /opt/avitoSearch/bot.py
Restart=always
RestartSec=5
User=root

[Install]
WantedBy=multi-user.target
```

## Безопасность

- `.env` не коммитится (добавлен в `.gitignore`).
- Если токен утек, перевыпусти его через `@BotFather`.
