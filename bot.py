import os
import re
import sqlite3
import time
from dataclasses import dataclass
from typing import List, Optional, Tuple
from urllib.parse import quote_plus, urljoin

import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv

AVITO_BASE = "https://www.avito.ru"
DEFAULT_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


@dataclass
class Listing:
    item_id: str
    title: str
    url: str


class StateStore:
    def __init__(self, db_path: str) -> None:
        self.conn = sqlite3.connect(db_path)
        self.conn.row_factory = sqlite3.Row
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        cur = self.conn.cursor()
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS seen_items (
                query TEXT NOT NULL,
                item_id TEXT NOT NULL,
                title TEXT NOT NULL,
                url TEXT NOT NULL,
                first_seen_ts INTEGER NOT NULL,
                PRIMARY KEY(query, item_id)
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS query_state (
                query TEXT PRIMARY KEY,
                bootstrapped INTEGER NOT NULL DEFAULT 0
            )
            """
        )
        self.conn.commit()

    def get_setting(self, key: str, default: Optional[str] = None) -> Optional[str]:
        cur = self.conn.cursor()
        cur.execute("SELECT value FROM settings WHERE key = ?", (key,))
        row = cur.fetchone()
        return row["value"] if row else default

    def set_setting(self, key: str, value: str) -> None:
        cur = self.conn.cursor()
        cur.execute(
            """
            INSERT INTO settings(key, value)
            VALUES(?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            (key, value),
        )
        self.conn.commit()

    def is_seen(self, query: str, item_id: str) -> bool:
        cur = self.conn.cursor()
        cur.execute(
            "SELECT 1 FROM seen_items WHERE query = ? AND item_id = ?",
            (query, item_id),
        )
        return cur.fetchone() is not None

    def mark_seen(self, query: str, listing: Listing) -> None:
        cur = self.conn.cursor()
        cur.execute(
            """
            INSERT OR IGNORE INTO seen_items(query, item_id, title, url, first_seen_ts)
            VALUES (?, ?, ?, ?, ?)
            """,
            (query, listing.item_id, listing.title, listing.url, int(time.time())),
        )
        self.conn.commit()

    def is_query_bootstrapped(self, query: str) -> bool:
        cur = self.conn.cursor()
        cur.execute("SELECT bootstrapped FROM query_state WHERE query = ?", (query,))
        row = cur.fetchone()
        return bool(row["bootstrapped"]) if row else False

    def mark_query_bootstrapped(self, query: str) -> None:
        cur = self.conn.cursor()
        cur.execute(
            """
            INSERT INTO query_state(query, bootstrapped)
            VALUES (?, 1)
            ON CONFLICT(query) DO UPDATE SET bootstrapped = 1
            """,
            (query,),
        )
        self.conn.commit()


class AvitoMonitor:
    ITEM_ID_RE = re.compile(r"_(\d+)(?:$|\?|#)")

    def __init__(self, region: str = "rossiya") -> None:
        self.region = region

    def build_search_url(self, query: str) -> str:
        encoded_query = quote_plus(query)
        return f"{AVITO_BASE}/{self.region}?q={encoded_query}&s=104"

    def fetch(self, query: str) -> Tuple[str, List[Listing]]:
        search_url = self.build_search_url(query)
        headers = {"User-Agent": DEFAULT_UA, "Accept-Language": "ru-RU,ru;q=0.9"}
        response = requests.get(search_url, headers=headers, timeout=20)
        response.raise_for_status()

        soup = BeautifulSoup(response.text, "html.parser")
        candidates = []

        for a in soup.select("a[data-marker='item-title']"):
            href = (a.get("href") or "").strip()
            title = " ".join(a.get_text(" ", strip=True).split())
            candidates.append((href, title))

        if not candidates:
            for a in soup.find_all("a", href=True):
                href = (a.get("href") or "").strip()
                title = " ".join(a.get_text(" ", strip=True).split())
                if "/" not in href:
                    continue
                candidates.append((href, title))

        listings: List[Listing] = []
        seen_ids = set()

        for href, title in candidates:
            full_url = href if href.startswith("http") else urljoin(AVITO_BASE, href)
            if "avito.ru" not in full_url:
                continue

            match = self.ITEM_ID_RE.search(full_url)
            if not match:
                continue

            item_id = match.group(1)
            if item_id in seen_ids:
                continue
            seen_ids.add(item_id)

            if not title:
                title = "Новое объявление"

            listings.append(Listing(item_id=item_id, title=title, url=full_url))

        return search_url, listings


class TelegramClient:
    def __init__(self, token: str) -> None:
        self.base_url = f"https://api.telegram.org/bot{token}"

    def get_updates(self, offset: int, timeout: int = 15) -> list:
        resp = requests.get(
            f"{self.base_url}/getUpdates",
            params={"offset": offset, "timeout": timeout},
            timeout=timeout + 5,
        )
        resp.raise_for_status()
        data = resp.json()
        if not data.get("ok"):
            raise RuntimeError(f"Telegram getUpdates failed: {data}")
        return data.get("result", [])

    def send_message(self, chat_id: str, text: str) -> None:
        resp = requests.post(
            f"{self.base_url}/sendMessage",
            json={"chat_id": chat_id, "text": text, "disable_web_page_preview": False},
            timeout=20,
        )
        resp.raise_for_status()


HELP_TEXT = (
    "Команды:\n"
    "/track <запрос> - начать отслеживать запрос\n"
    "/stop - остановить мониторинг\n"
    "/status - показать текущие настройки\n"
    "/interval <сек> - сменить интервал проверки (минимум 30)\n"
    "/help - показать помощь\n\n"
    "Пример: /track iphone 13 128"
)


def parse_config() -> dict:
    load_dotenv()
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        raise ValueError("TELEGRAM_BOT_TOKEN не задан в .env")

    cfg = {
        "token": token,
        "db_path": os.getenv("STATE_DB_PATH", "state.sqlite3").strip(),
        "default_interval": max(30, int(os.getenv("POLL_INTERVAL_SECONDS", "120"))),
        "max_notifications": max(1, int(os.getenv("MAX_NOTIFICATIONS_PER_CYCLE", "10"))),
        "admin_chat_id": os.getenv("ADMIN_CHAT_ID", "").strip() or None,
        "region": os.getenv("AVITO_REGION", "rossiya").strip(),
    }
    return cfg


def normalize_text(text: str) -> str:
    return " ".join((text or "").strip().split())


def is_authorized(chat_id: str, store: StateStore, env_admin_chat_id: Optional[str]) -> bool:
    if env_admin_chat_id:
        return chat_id == env_admin_chat_id

    saved = store.get_setting("admin_chat_id")
    if saved:
        return chat_id == saved

    # First user who writes to the bot becomes owner when ADMIN_CHAT_ID is not set.
    store.set_setting("admin_chat_id", chat_id)
    return True


def get_runtime_settings(store: StateStore, default_interval: int) -> Tuple[bool, Optional[str], int]:
    enabled = store.get_setting("enabled", "0") == "1"
    query = store.get_setting("query", None)
    interval = int(store.get_setting("interval", str(default_interval)) or default_interval)
    interval = max(30, interval)
    return enabled, query, interval


def set_runtime_settings(store: StateStore, enabled: bool, query: Optional[str], interval: int) -> None:
    store.set_setting("enabled", "1" if enabled else "0")
    if query is not None:
        store.set_setting("query", query)
    store.set_setting("interval", str(max(30, interval)))


def handle_command(
    text: str,
    chat_id: str,
    tg: TelegramClient,
    store: StateStore,
    default_interval: int,
    monitor: AvitoMonitor,
) -> None:
    clean = normalize_text(text)
    if not clean:
        return

    enabled, query, interval = get_runtime_settings(store, default_interval)

    if clean.startswith("/start"):
        tg.send_message(chat_id, "Бот готов к работе.\\n" + HELP_TEXT)
        return

    if clean.startswith("/help"):
        tg.send_message(chat_id, HELP_TEXT)
        return

    if clean.startswith("/stop"):
        set_runtime_settings(store, False, query, interval)
        tg.send_message(chat_id, "Мониторинг остановлен.")
        return

    if clean.startswith("/status"):
        if query:
            url = monitor.build_search_url(query)
            msg = (
                f"Статус: {'включен' if enabled else 'выключен'}\\n"
                f"Запрос: {query}\\n"
                f"Интервал: {interval} сек\\n"
                f"URL: {url}"
            )
        else:
            msg = f"Статус: {'включен' if enabled else 'выключен'}\\nЗапрос еще не задан."
        tg.send_message(chat_id, msg)
        return

    if clean.startswith("/interval"):
        parts = clean.split(maxsplit=1)
        if len(parts) != 2 or not parts[1].isdigit():
            tg.send_message(chat_id, "Используй: /interval 120")
            return
        new_interval = max(30, int(parts[1]))
        set_runtime_settings(store, enabled, query, new_interval)
        tg.send_message(chat_id, f"Интервал обновлен: {new_interval} сек")
        return

    if clean.startswith("/track"):
        parts = clean.split(maxsplit=1)
        if len(parts) != 2:
            tg.send_message(chat_id, "Используй: /track iphone 13")
            return

        new_query = normalize_text(parts[1])
        set_runtime_settings(store, True, new_query, interval)
        tg.send_message(
            chat_id,
            "Запрос сохранен. Мониторинг включен.\\n"
            f"Запрос: {new_query}\\n"
            f"URL: {monitor.build_search_url(new_query)}",
        )
        return

    # Если пользователь пишет просто текст без команды, считаем это новым запросом
    set_runtime_settings(store, True, clean, interval)
    tg.send_message(
        chat_id,
        "Принял как поисковый запрос и включил мониторинг.\\n"
        f"Запрос: {clean}\\n"
        f"URL: {monitor.build_search_url(clean)}",
    )


def process_updates(
    tg: TelegramClient,
    store: StateStore,
    env_admin_chat_id: Optional[str],
    default_interval: int,
    monitor: AvitoMonitor,
) -> None:
    offset = int(store.get_setting("tg_offset", "0") or "0")

    updates = tg.get_updates(offset=offset, timeout=12)
    for upd in updates:
        update_id = int(upd.get("update_id", 0))
        message = upd.get("message") or {}
        chat = message.get("chat") or {}
        chat_id = str(chat.get("id", ""))
        text = message.get("text", "")

        if chat_id and text and is_authorized(chat_id, store, env_admin_chat_id):
            handle_command(text, chat_id, tg, store, default_interval, monitor)

        offset = max(offset, update_id + 1)

    store.set_setting("tg_offset", str(offset))


def run_monitor_cycle(
    tg: TelegramClient,
    store: StateStore,
    monitor: AvitoMonitor,
    max_notifications: int,
    default_interval: int,
) -> None:
    enabled, query, _interval = get_runtime_settings(store, default_interval)
    if not enabled or not query:
        return

    admin_chat_id = store.get_setting("admin_chat_id")
    if not admin_chat_id:
        return

    search_url, listings = monitor.fetch(query)
    new_items = [item for item in listings if not store.is_seen(query, item.item_id)]

    if not store.is_query_bootstrapped(query):
        for item in new_items:
            store.mark_seen(query, item)
        store.mark_query_bootstrapped(query)
        tg.send_message(
            admin_chat_id,
            f"Мониторинг активен. Базово сохранено {len(new_items)} текущих объявлений.\\n"
            f"Дальше будут приходить только новые.\\n{search_url}",
        )
        return

    notified = 0
    for item in new_items:
        if notified < max_notifications:
            tg.send_message(
                admin_chat_id,
                f"Найдено новое объявление:\\n{item.title}\\n{item.url}",
            )
            notified += 1
        store.mark_seen(query, item)

    if notified > 0:
        tg.send_message(admin_chat_id, f"Новых объявлений: {len(new_items)}, отправлено: {notified}")


def main() -> None:
    cfg = parse_config()
    store = StateStore(cfg["db_path"])
    tg = TelegramClient(cfg["token"])
    monitor = AvitoMonitor(region=cfg["region"])

    print("Bot started")
    print("Waiting for Telegram commands...")

    next_check_ts = 0.0

    while True:
        try:
            process_updates(
                tg=tg,
                store=store,
                env_admin_chat_id=cfg["admin_chat_id"],
                default_interval=cfg["default_interval"],
                monitor=monitor,
            )

            enabled, query, interval = get_runtime_settings(store, cfg["default_interval"])
            now = time.time()
            if enabled and query and now >= next_check_ts:
                run_monitor_cycle(
                    tg=tg,
                    store=store,
                    monitor=monitor,
                    max_notifications=cfg["max_notifications"],
                    default_interval=cfg["default_interval"],
                )
                next_check_ts = now + interval
            elif not enabled:
                next_check_ts = now + 5

        except Exception as exc:
            print(f"[error] {type(exc).__name__}: {exc}")
            time.sleep(5)


if __name__ == "__main__":
    main()
