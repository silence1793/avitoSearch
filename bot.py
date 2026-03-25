import asyncio
import base64
import io
import os
from collections import defaultdict
from typing import Dict, List

from dotenv import load_dotenv
from openai import OpenAI
from telegram import InputFile, ReplyKeyboardRemove, Update
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

SYSTEM_DEFAULT = "Ты полезный и краткий русскоязычный ассистент."


class GPTBridge:
    def __init__(
        self,
        api_key: str,
        model: str,
        image_model: str,
        image_size: str,
        system_prompt: str,
        max_pairs: int,
    ) -> None:
        self.client = OpenAI(api_key=api_key) if api_key else None
        self.model = model
        self.image_model = image_model
        self.image_size = image_size
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

    def _looks_like_image_request(self, user_text: str) -> bool:
        text = user_text.lower()
        markers = [
            "сгенерируй",
            "создай картинку",
            "создай изображение",
            "нарисуй",
            "generate image",
            "draw",
            "image of",
        ]
        return any(marker in text for marker in markers)

    def _generate_image(self, prompt: str) -> dict:
        resp = self.client.images.generate(
            model=self.image_model,
            prompt=prompt,
            size=self.image_size,
        )
        data = resp.data[0]
        b64 = getattr(data, "b64_json", None)
        if not b64:
            raise RuntimeError("OpenAI не вернул изображение в base64.")
        image_bytes = base64.b64decode(b64)
        return {
            "kind": "image",
            "bytes": image_bytes,
            "caption": "Готово. Нажми на фото, чтобы открыть в полном размере.",
        }

    def ask(self, chat_id: int, user_text: str) -> dict:
        if not self.client:
            return {
                "kind": "text",
                "text": "OpenAI API ключ не настроен. Добавь OPENAI_API_KEY в .env на сервере.",
            }

        if self._looks_like_image_request(user_text):
            image = self._generate_image(user_text)
            history = self.histories[chat_id]
            history.append({"role": "user", "content": user_text})
            history.append({"role": "assistant", "content": "[Изображение сгенерировано]"})
            self.histories[chat_id] = history[-2 * self.max_pairs :]
            return image

        payload = self._build_input(chat_id, user_text)
        resp = self.client.responses.create(model=self.model, input=payload)
        answer = (resp.output_text or "").strip() or "Не получилось получить ответ."

        history = self.histories[chat_id]
        history.append({"role": "user", "content": user_text})
        history.append({"role": "assistant", "content": answer})
        self.histories[chat_id] = history[-2 * self.max_pairs :]
        return {"kind": "text", "text": answer}


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "Готово. Я обычный чат-бот как ChatGPT.\n"
        "Просто пиши сообщение без команд.\n"
        "Для нового диалога: /new",
        reply_markup=ReplyKeyboardRemove(),
    )


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "Пиши как обычному ChatGPT.\n"
        "Если нужна картинка, просто напиши: сгенерируй ...\n"
        "/new — очистить контекст",
        reply_markup=ReplyKeyboardRemove(),
    )


async def new_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    bridge: GPTBridge = context.bot_data["bridge"]
    bridge.clear_chat(update.effective_chat.id)
    await update.message.reply_text(
        "Контекст очищен. Начинаем новый диалог.",
        reply_markup=ReplyKeyboardRemove(),
    )


async def _send_long_text(update: Update, text: str) -> None:
    for i in range(0, len(text), 4096):
        await update.message.reply_text(text[i : i + 4096], reply_markup=ReplyKeyboardRemove())


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
        result = await asyncio.to_thread(bridge.ask, chat_id, text)
    except Exception as exc:
        result = {"kind": "text", "text": f"Ошибка: {type(exc).__name__}: {exc}"}

    await msg.delete()
    if result["kind"] == "image":
        photo = InputFile(io.BytesIO(result["bytes"]), filename="image.png")
        await update.message.reply_photo(
            photo=photo,
            caption=result["caption"],
            reply_markup=ReplyKeyboardRemove(),
        )
        return
    await _send_long_text(update, result["text"])


def build_app() -> Application:
    load_dotenv()

    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        raise ValueError("TELEGRAM_BOT_TOKEN не задан в .env")

    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    model = os.getenv("OPENAI_MODEL", "gpt-4.1-mini").strip() or "gpt-4.1-mini"
    image_model = os.getenv("OPENAI_IMAGE_MODEL", "gpt-image-1").strip() or "gpt-image-1"
    image_size = os.getenv("OPENAI_IMAGE_SIZE", "1024x1024").strip() or "1024x1024"
    system_prompt = os.getenv("SYSTEM_PROMPT", SYSTEM_DEFAULT).strip() or SYSTEM_DEFAULT
    max_pairs = int(os.getenv("MAX_HISTORY_PAIRS", "8"))

    app = Application.builder().token(token).build()
    app.bot_data["bridge"] = GPTBridge(
        api_key=api_key,
        model=model,
        image_model=image_model,
        image_size=image_size,
        system_prompt=system_prompt,
        max_pairs=max_pairs,
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("new", new_cmd))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
    return app


def main() -> None:
    app = build_app()
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
