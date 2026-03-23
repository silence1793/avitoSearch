import asyncio
import os
import time
from datetime import datetime
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

from src.db import Store

HELP_TEXT = (
    "Планировщик: покупки, фокус, финансы, контент.\n\n"
    "Быстрый старт:\n"
    "1) Покупки: /wish add наушники | 12000\n"
    "2) Фокус: /focus set задача1 ; задача2 ; задача3\n"
    "3) Финансы: /spent 790 еда обед\n"
    "4) Контент: /idea add идея поста\n\n"
    "Команды:\n"
    "/list - inbox заметки\n"
    "/wish ... - wishlist\n"
    "/focus ... - дневной фокус\n"
    "/spent ... и /budget ... - финансы\n"
    "/idea ... и /draft ... - контент\n"
    "/help - помощь"
)


def _month_bounds(now: datetime) -> tuple[int, int]:
    start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    if start.month == 12:
        next_month = start.replace(year=start.year + 1, month=1)
    else:
        next_month = start.replace(month=start.month + 1)
    return int(start.timestamp()), int(next_month.timestamp())


def _parse_int(value: str):
    s = "".join(ch for ch in value if ch.isdigit())
    return int(s) if s else None


def _focus_day_key(tz_name: str) -> str:
    tz = ZoneInfo(tz_name)
    return datetime.now(tz).strftime("%Y-%m-%d")


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text("Планировщик запущен.\n\n" + HELP_TEXT)


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(HELP_TEXT)


# Inbox
async def add_task_from_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.message.text:
        return
    text = update.message.text.strip()
    if text.startswith("/"):
        return
    store: Store = context.bot_data["store"]
    task_id = store.add_task(update.effective_chat.id, text)
    await update.message.reply_text(f"Добавил в inbox #{task_id}")


async def list_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    store: Store = context.bot_data["store"]
    tasks = store.list_tasks(update.effective_chat.id, include_done=False)
    if not tasks:
        await update.message.reply_text("Inbox пуст.")
        return
    lines = ["Inbox:"]
    for t in tasks[:30]:
        lines.append(f"• {t.id}: {t.text}")
    await update.message.reply_text("\n".join(lines))


# Shopping / wishlist
async def wish_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        await update.message.reply_text(
            "Используй:\n"
            "/wish add <вещь> | <бюджет>\n"
            "/wish list\n"
            "/wish done <id>\n"
            "/wish del <id>"
        )
        return

    store: Store = context.bot_data["store"]
    chat_id = update.effective_chat.id
    sub = context.args[0].lower()

    if sub == "list":
        wishes = store.list_wishes(chat_id)
        if not wishes:
            await update.message.reply_text("Wishlist пуст.")
            return
        lines = ["Wishlist:"]
        for w in wishes[:30]:
            budget = f"до {w.budget} ₽" if w.budget else "без бюджета"
            lines.append(f"• {w.id}: {w.item} ({budget})")
        await update.message.reply_text("\n".join(lines))
        return

    if sub in {"done", "del"}:
        if len(context.args) < 2 or not context.args[1].isdigit():
            await update.message.reply_text(f"Используй: /wish {sub} <id>")
            return
        wish_id = int(context.args[1])
        if sub == "done":
            ok = store.mark_wish_found(chat_id, wish_id)
            await update.message.reply_text("Отметил как найдено ✅" if ok else "Не нашел id.")
        else:
            ok = store.delete_wish(chat_id, wish_id)
            await update.message.reply_text("Удалил." if ok else "Не нашел id.")
        return

    if sub == "add":
        payload = " ".join(context.args[1:]).strip()
        if not payload:
            await update.message.reply_text("Используй: /wish add <вещь> | <бюджет>")
            return
        if "|" in payload:
            item, budget_raw = [x.strip() for x in payload.split("|", 1)]
            budget = _parse_int(budget_raw)
        else:
            item = payload
            budget = None
        wish_id = store.add_wish(chat_id, item, budget)
        await update.message.reply_text(f"Добавил в wishlist #{wish_id}")
        return

    await update.message.reply_text("Неизвестная подкоманда /wish")


# Focus
async def focus_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    store: Store = context.bot_data["store"]
    tz_name: str = context.bot_data["timezone"]
    chat_id = update.effective_chat.id
    day_key = _focus_day_key(tz_name)

    if not context.args:
        await update.message.reply_text(
            "Используй:\n"
            "/focus set задача1 ; задача2 ; задача3\n"
            "/focus today\n"
            "/focus done <1|2|3>\n"
            "/focus report"
        )
        return

    sub = context.args[0].lower()

    if sub == "set":
        raw = " ".join(context.args[1:]).strip()
        parts = [p.strip() for p in raw.split(";") if p.strip()]
        if len(parts) != 3:
            await update.message.reply_text("Нужно ровно 3 задачи через ';'")
            return
        store.upsert_focus_day(chat_id, day_key, parts[0], parts[1], parts[2])
        await update.message.reply_text("Фокус дня сохранен ✅")
        return

    row = store.get_focus_day(chat_id, day_key)
    if sub in {"today", "report"}:
        if not row:
            await update.message.reply_text("Фокус дня еще не задан. Используй /focus set ...")
            return
        lines = [f"Фокус на {day_key}:"]
        for i in (1, 2, 3):
            mark = "✅" if row[f"done{i}"] else "•"
            lines.append(f"{mark} {i}. {row[f'item{i}']}")
        done_cnt = row["done1"] + row["done2"] + row["done3"]
        lines.append(f"Выполнено: {done_cnt}/3")
        await update.message.reply_text("\n".join(lines))
        return

    if sub == "done":
        if len(context.args) < 2 or context.args[1] not in {"1", "2", "3"}:
            await update.message.reply_text("Используй: /focus done <1|2|3>")
            return
        if not row:
            await update.message.reply_text("Сначала задай /focus set ...")
            return
        ok = store.set_focus_done(chat_id, day_key, int(context.args[1]))
        await update.message.reply_text("Отметил ✅" if ok else "Не получилось")
        return

    await update.message.reply_text("Неизвестная подкоманда /focus")


# Finance
async def spent_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if len(context.args) < 2:
        await update.message.reply_text("Используй: /spent <сумма> <категория> [комментарий]")
        return

    amount = _parse_int(context.args[0])
    if not amount or amount <= 0:
        await update.message.reply_text("Сумма должна быть больше 0")
        return

    category = context.args[1]
    note = " ".join(context.args[2:]) if len(context.args) > 2 else ""
    store: Store = context.bot_data["store"]
    chat_id = update.effective_chat.id

    entry_id = store.add_spent(chat_id, amount, category, note)

    tz_name: str = context.bot_data["timezone"]
    now = datetime.now(ZoneInfo(tz_name))
    start_ts, end_ts = _month_bounds(now)
    total = store.spent_total_for_month(chat_id, start_ts, end_ts)

    limit_raw = store.get_setting(chat_id, "monthly_budget")
    if limit_raw and limit_raw.isdigit():
        limit = int(limit_raw)
        left = limit - total
        await update.message.reply_text(
            f"Записал трату #{entry_id}: {amount} ₽ ({category}).\n"
            f"За месяц: {total} ₽ / {limit} ₽. Осталось: {left} ₽"
        )
    else:
        await update.message.reply_text(f"Записал трату #{entry_id}: {amount} ₽ ({category}). За месяц: {total} ₽")


async def budget_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    store: Store = context.bot_data["store"]
    chat_id = update.effective_chat.id

    if not context.args:
        await update.message.reply_text("Используй: /budget set <сумма> или /budget status")
        return

    sub = context.args[0].lower()
    if sub == "set":
        if len(context.args) < 2:
            await update.message.reply_text("Используй: /budget set <сумма>")
            return
        amount = _parse_int(context.args[1])
        if not amount:
            await update.message.reply_text("Неверная сумма")
            return
        store.set_setting(chat_id, "monthly_budget", str(amount))
        await update.message.reply_text(f"Месячный лимит установлен: {amount} ₽")
        return

    if sub == "status":
        tz_name: str = context.bot_data["timezone"]
        now = datetime.now(ZoneInfo(tz_name))
        start_ts, end_ts = _month_bounds(now)
        total = store.spent_total_for_month(chat_id, start_ts, end_ts)
        limit_raw = store.get_setting(chat_id, "monthly_budget")
        if limit_raw and limit_raw.isdigit():
            limit = int(limit_raw)
            await update.message.reply_text(f"За месяц: {total} ₽ / {limit} ₽. Осталось: {limit - total} ₽")
        else:
            await update.message.reply_text(f"За месяц потрачено: {total} ₽. Лимит не задан.")
        return

    await update.message.reply_text("Неизвестная подкоманда /budget")


async def spent_list_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    store: Store = context.bot_data["store"]
    chat_id = update.effective_chat.id
    limit = 20
    if context.args and context.args[0].isdigit():
        limit = min(100, max(1, int(context.args[0])))
    rows = store.list_spent(chat_id, limit)
    if not rows:
        await update.message.reply_text("Трат пока нет.")
        return
    lines = ["Последние траты:"]
    for r in rows:
        note = f" - {r.note}" if r.note else ""
        lines.append(f"• {r.id}: {r.amount} ₽ [{r.category}]{note}")
    await update.message.reply_text("\n".join(lines))


# Content
async def idea_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    store: Store = context.bot_data["store"]
    chat_id = update.effective_chat.id

    if not context.args:
        await update.message.reply_text("Используй: /idea add <текст> | /idea list | /idea done <id> | /idea del <id>")
        return

    sub = context.args[0].lower()
    if sub == "add":
        text = " ".join(context.args[1:]).strip()
        if not text:
            await update.message.reply_text("Пустая идея")
            return
        item_id = store.add_content(chat_id, "idea", text, "")
        await update.message.reply_text(f"Идея сохранена #{item_id}")
        return

    if sub == "list":
        rows = store.list_content(chat_id, kind="idea", include_done=False)
        if not rows:
            await update.message.reply_text("Идей пока нет.")
            return
        lines = ["Идеи:"]
        for r in rows[:40]:
            lines.append(f"• {r.id}: {r.title}")
        await update.message.reply_text("\n".join(lines))
        return

    if sub in {"done", "del"}:
        if len(context.args) < 2 or not context.args[1].isdigit():
            await update.message.reply_text(f"Используй: /idea {sub} <id>")
            return
        item_id = int(context.args[1])
        if sub == "done":
            ok = store.mark_content_done(chat_id, item_id)
            await update.message.reply_text("Отметил идею ✅" if ok else "Не нашел id")
        else:
            ok = store.delete_content(chat_id, item_id)
            await update.message.reply_text("Удалил." if ok else "Не нашел id")
        return

    await update.message.reply_text("Неизвестная подкоманда /idea")


async def draft_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    store: Store = context.bot_data["store"]
    chat_id = update.effective_chat.id

    if not context.args:
        await update.message.reply_text(
            "Используй: /draft add <заголовок> | <текст> | /draft list | /draft done <id> | /draft del <id>"
        )
        return

    sub = context.args[0].lower()

    if sub == "add":
        payload = " ".join(context.args[1:]).strip()
        if not payload:
            await update.message.reply_text("Используй: /draft add <заголовок> | <текст>")
            return
        if "|" in payload:
            title, body = [x.strip() for x in payload.split("|", 1)]
        else:
            title, body = payload, ""
        item_id = store.add_content(chat_id, "draft", title, body)
        await update.message.reply_text(f"Черновик сохранен #{item_id}")
        return

    if sub == "list":
        rows = store.list_content(chat_id, kind="draft", include_done=False)
        if not rows:
            await update.message.reply_text("Черновиков пока нет.")
            return
        lines = ["Черновики:"]
        for r in rows[:30]:
            body_short = (r.body[:60] + "...") if len(r.body) > 60 else r.body
            tail = f" | {body_short}" if body_short else ""
            lines.append(f"• {r.id}: {r.title}{tail}")
        await update.message.reply_text("\n".join(lines))
        return

    if sub in {"done", "del"}:
        if len(context.args) < 2 or not context.args[1].isdigit():
            await update.message.reply_text(f"Используй: /draft {sub} <id>")
            return
        item_id = int(context.args[1])
        if sub == "done":
            ok = store.mark_content_done(chat_id, item_id)
            await update.message.reply_text("Отметил черновик ✅" if ok else "Не нашел id")
        else:
            ok = store.delete_content(chat_id, item_id)
            await update.message.reply_text("Удалил." if ok else "Не нашел id")
        return

    await update.message.reply_text("Неизвестная подкоманда /draft")


# Background loops
async def reminder_loop(app: Application) -> None:
    store: Store = app.bot_data["store"]
    while True:
        try:
            due = store.due_reminders(int(time.time()))
            for r in due:
                task = store.get_task(r.chat_id, r.task_id)
                text = task.text if task else "(задача удалена)"
                await app.bot.send_message(r.chat_id, f"⏰ Напоминание: #{r.task_id} — {text}")
                store.mark_reminder_sent(r.id)
        except Exception as exc:
            print(f"[reminder-loop] {type(exc).__name__}: {exc}")
        await asyncio.sleep(20)


async def focus_ping_loop(app: Application) -> None:
    tz_name: str = app.bot_data["timezone"]
    tz = ZoneInfo(tz_name)
    store: Store = app.bot_data["store"]

    while True:
        now = datetime.now(tz)
        for chat_id_str in os.getenv("FOCUS_CHAT_IDS", "").split(","):
            chat_id_str = chat_id_str.strip()
            if not chat_id_str:
                continue
            chat_id = int(chat_id_str)

            day_key = now.strftime("%Y-%m-%d")
            morning_key = f"focus_morning_sent_{day_key}"
            evening_key = f"focus_evening_sent_{day_key}"

            if now.hour == 9 and store.get_setting(chat_id, morning_key) != "1":
                await app.bot.send_message(chat_id, "Доброе утро. Сформулируй фокус дня: /focus set задача1 ; задача2 ; задача3")
                store.set_setting(chat_id, morning_key, "1")

            if now.hour == 21 and store.get_setting(chat_id, evening_key) != "1":
                row = store.get_focus_day(chat_id, day_key)
                if row:
                    done_cnt = row["done1"] + row["done2"] + row["done3"]
                    await app.bot.send_message(chat_id, f"Вечерний отчет: сегодня выполнено {done_cnt}/3. /focus report")
                else:
                    await app.bot.send_message(chat_id, "Вечерний чек: фокус дня не задан. Завтра начнем заново 💪")
                store.set_setting(chat_id, evening_key, "1")

        await asyncio.sleep(60)


def build_app(token: str, db_path: str, tz_name: str) -> Application:
    app = Application.builder().token(token).build()
    app.bot_data["store"] = Store(db_path)
    app.bot_data["timezone"] = tz_name

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("list", list_cmd))

    app.add_handler(CommandHandler("wish", wish_cmd))
    app.add_handler(CommandHandler("focus", focus_cmd))

    app.add_handler(CommandHandler("spent", spent_cmd))
    app.add_handler(CommandHandler("spent_list", spent_list_cmd))
    app.add_handler(CommandHandler("budget", budget_cmd))

    app.add_handler(CommandHandler("idea", idea_cmd))
    app.add_handler(CommandHandler("draft", draft_cmd))

    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, add_task_from_text))

    async def _on_start(app_: Application) -> None:
        app_.create_task(reminder_loop(app_))
        app_.create_task(focus_ping_loop(app_))

    app.post_init = _on_start
    return app


def main() -> None:
    load_dotenv()
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        raise ValueError("TELEGRAM_BOT_TOKEN не задан в .env")

    db_path = os.getenv("STATE_DB_PATH", "state.sqlite3").strip()
    tz_name = os.getenv("TZ_NAME", "Europe/Moscow").strip() or "Europe/Moscow"
    app = build_app(token, db_path, tz_name)
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
