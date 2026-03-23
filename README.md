# Планировщик (2-5)

Бот объединяет 4 модуля:
- 2) Покупки/поиск вещей (wishlist + бюджет)
- 3) Дневной фокус (3 главные задачи на день)
- 4) Финансы (траты и месячный лимит)
- 5) Контент (идеи и черновики)

## Запуск

```bash
cd /Users/rahman/Downloads/avitoBot
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# укажи TELEGRAM_BOT_TOKEN
python bot.py
```

## Покупки

- `/wish add наушники | 12000`
- `/wish list`
- `/wish done 3`
- `/wish del 3`

Кнопки:
- `🛒 Покупки`
- `🎯 Фокус`
- `💸 Финансы`
- `✍️ Контент`
- `📥 Inbox`
- `ℹ️ Помощь`
- `/menu` — показать меню снова

## Фокус дня

- `/focus set задача1 ; задача2 ; задача3`
- `/focus today`
- `/focus done 1`
- `/focus report`

Если в `.env` задан `FOCUS_CHAT_IDS`, бот сам пингует:
- утром (09:00) — задать фокус
- вечером (21:00) — короткий отчет

## Финансы

- `/spent 790 еда обед`
- `/spent_list`
- `/budget set 45000`
- `/budget status`

## Контент

- `/idea add идея поста про ...`
- `/idea list`
- `/idea done 2`
- `/draft add Заголовок | текст черновика`
- `/draft list`
- `/draft done 5`

## Inbox

Любой текст без `/` бот сохраняет как inbox-заметку.
- `/list` — показать inbox
