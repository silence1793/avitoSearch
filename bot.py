import asyncio
import os
from collections import defaultdict
from typing import Dict, List

from dotenv import load_dotenv
from openai import OpenAI
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

SYSTEM_DEFAULT = "Ты полезный и краткий русскоязычный ассистент."


class GPTBridge:
    def __init__(self, api_key: str, model: str, system_prompt: str, max_pairs: int) -> None:
        self.client = OpenAI(api_key=api_key) if api_key else None
        self.model = model
        self.system_prompt = system_prompt
        self.max_pairs = max(1, max_pairs)
        self.histories: Dict[int, List[dict]] = defaultdict(list)

    def clear_chat(self, chat_id: int) -> None:
        self.histories.pop(chat_id, None)

    def _build_input(self, chat_id: int, user_text: str) -> List[dict]:
        history = self.histories[chat_id]
        messages = [{"role": "system", "content": self.system_prompt}]
        messages.extend(history[-2 * self.max_pairs :])
        messages.append({"role": "user", "content": user_text})
        return messages

    def ask(self, chat_id: int, user_text: str) -> str:
        if not self.client:
            return "OpenAI API ключ не настроен. Добавь OPENAI_API_KEY в .env на сервере."

        payload = self._build_input(chat_id, user_text)
        resp = self.client.responses.create(model=self.model, input=payload)
        answer = (resp.output_text or "").strip() or "Не получилось получить ответ."

        history = self.histories[chat_id]
        history.append({"role": "user", "content": user_text})
        history.append({"role": "assistant", "content": answer})
        self.histories[chat_id] = history[-2 * self.max_pairs :]
        return answer


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "Готово. Я ChatGPT-бот. Просто пиши сообщение.\\n"
        "Команды: /new (очистить контекст), /model (показать модель), /help"
    )


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text("Пиши сообщение текстом. /new — новый диалог.")


async def model_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    bridge: GPTBridge = context.bot_data["bridge"]
    await update.message.reply_text(f"Текущая модель: {bridge.model}")


async def new_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    bridge: GPTBridge = context.bot_data["bridge"]
    bridge.clear_chat(update.effective_chat.id)
    await update.message.reply_text("Контекст очищен. Начинаем новый диалог.")


async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.message.text:
        return
    text = update.message.text.strip()
    if not text:
        return

    bridge: GPTBridge = context.bot_data["bridge"]
    chat_id = update.effective_chat.id

    msg = await update.message.reply_text("Думаю...")
    try:
        answer = await asyncio.to_thread(bridge.ask, chat_id, text)
    except Exception as exc:
        answer = f"Ошибка: {type(exc).__name__}: {exc}"

    await msg.edit_text(answer[:4096])


def build_app() -> Application:
    load_dotenv()

    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        raise ValueError("TELEGRAM_BOT_TOKEN не задан в .env")

    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    model = os.getenv("OPENAI_MODEL", "gpt-4.1-mini").strip() or "gpt-4.1-mini"
    system_prompt = os.getenv("SYSTEM_PROMPT", SYSTEM_DEFAULT).strip() or SYSTEM_DEFAULT
    max_pairs = int(os.getenv("MAX_HISTORY_PAIRS", "8"))

    app = Application.builder().token(token).build()
    app.bot_data["bridge"] = GPTBridge(api_key, model, system_prompt, max_pairs)

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("model", model_cmd))
    app.add_handler(CommandHandler("new", new_cmd))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
    return app


def main() -> None:
    app = build_app()
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
