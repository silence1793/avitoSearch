import os
import random
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
    image_url: Optional[str] = None


REGION_ALIASES = {
    "дагестане": "dagestan",
    "дагестан": "dagestan",
    "республике дагестан": "dagestan",
    "республика дагестан": "dagestan",
    "москве": "moskva",
    "москва": "moskva",
    "спб": "sankt-peterburg",
    "питере": "sankt-peterburg",
    "петербурге": "sankt-peterburg",
    "санкт-петербург": "sankt-peterburg",
    "санкт петербург": "sankt-peterburg",
    "россии": "rossiya",
    "россия": "rossiya",
}

REGION_LABELS = {
    "dagestan": "Дагестан",
    "moskva": "Москва",
    "sankt-peterburg": "Санкт-Петербург",
    "rossiya": "Россия",
}


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

    def build_search_url(self, query: str, region: str, max_price: Optional[int] = None) -> str:
        encoded_query = quote_plus(query)
        url = f"{AVITO_BASE}/{region}?q={encoded_query}&s=104"
        if max_price:
            url += f"&pmax={max_price}"
        return url

    def fetch(self, query: str, region: str, max_price: Optional[int] = None) -> Tuple[str, List[Listing]]:
        search_url = self.build_search_url(query, region, max_price)
        headers = {"User-Agent": DEFAULT_UA, "Accept-Language": "ru-RU,ru;q=0.9"}
        response = requests.get(search_url, headers=headers, timeout=20)
        response.raise_for_status()

        soup = BeautifulSoup(response.text, "html.parser")
        candidates = []

        for a in soup.select("a[data-marker='item-title']"):
            href = (a.get("href") or "").strip()
            title = " ".join(a.get_text(" ", strip=True).split())
            image_url = self._extract_image_url(a)
            candidates.append((href, title, image_url))

        if not candidates:
            for a in soup.find_all("a", href=True):
                href = (a.get("href") or "").strip()
                title = " ".join(a.get_text(" ", strip=True).split())
                if "/" not in href:
                    continue
                image_url = self._extract_image_url(a)
                candidates.append((href, title, image_url))

        listings: List[Listing] = []
        seen_ids = set()

        for href, title, image_url in candidates:
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

            listings.append(Listing(item_id=item_id, title=title, url=full_url, image_url=image_url))

        return search_url, listings

    @staticmethod
    def _extract_image_url(link_tag) -> Optional[str]:
        parent = link_tag.parent
        grandparent = parent.parent if parent else None
        for node in (link_tag, parent, grandparent):
            if node is None:
                continue
            img = node.find("img")
            if not img:
                continue
            for attr in ("src", "data-src", "srcset", "data-srcset"):
                raw = (img.get(attr) or "").strip()
                if not raw:
                    continue
                if attr in ("srcset", "data-srcset"):
                    raw = raw.split(",")[0].strip().split(" ")[0].strip()
                if raw.startswith("//"):
                    raw = "https:" + raw
                elif raw.startswith("/"):
                    raw = urljoin(AVITO_BASE, raw)
                if raw.startswith("http"):
                    return raw
        return None


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

    def send_link_card(self, chat_id: str, title: str, url: str) -> None:
        payload = {
            "chat_id": chat_id,
            "text": title,
            "disable_web_page_preview": True,
            "reply_markup": {
                "inline_keyboard": [[{"text": "Открыть на Avito", "url": url}]],
            },
        }
        resp = requests.post(f"{self.base_url}/sendMessage", json=payload, timeout=20)
        resp.raise_for_status()

    def send_photo_card(self, chat_id: str, listing: Listing) -> None:
        if listing.image_url:
            payload = {
                "chat_id": chat_id,
                "photo": listing.image_url,
                "caption": listing.title,
                "reply_markup": {
                    "inline_keyboard": [[{"text": "Открыть на Avito", "url": listing.url}]],
                },
            }
            resp = requests.post(f"{self.base_url}/sendPhoto", json=payload, timeout=25)
            if resp.ok:
                return
        self.send_link_card(chat_id, listing.title, listing.url)


HELP_TEXT = (
    "Пиши обычным текстом:\n"
    "Пример: найди мне playstation 5 slim за 36000 рублей в дагестане\n\n"
    "Команды:\n"
    "/stop - остановить мониторинг\n"
    "/status - показать текущие настройки\n"
    "/interval <сек> - сменить интервал проверки (минимум 30)\n"
    "/help - показать помощь"
)


def parse_config() -> dict:
    load_dotenv()
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        raise ValueError("TELEGRAM_BOT_TOKEN не задан в .env")

    soft_mode = os.getenv("SOFT_MODE", "true").strip().lower() in {"1", "true", "yes"}
    min_interval = 600 if soft_mode else 30

    cfg = {
        "token": token,
        "db_path": os.getenv("STATE_DB_PATH", "state.sqlite3").strip(),
        "default_interval": max(min_interval, int(os.getenv("POLL_INTERVAL_SECONDS", "120"))),
        "max_notifications": max(1, int(os.getenv("MAX_NOTIFICATIONS_PER_CYCLE", "10"))),
        "admin_chat_id": os.getenv("ADMIN_CHAT_ID", "").strip() or None,
        "default_region": os.getenv("AVITO_REGION", "rossiya").strip(),
        "soft_mode": soft_mode,
        "min_interval": min_interval,
        "jitter_seconds": max(0, int(os.getenv("SOFT_JITTER_SECONDS", "180"))),
    }
    return cfg


def normalize_text(text: str) -> str:
    return " ".join((text or "").strip().split())


def parse_price(text: str) -> Optional[int]:
    compact = text.lower().replace("\xa0", " ")
    price_patterns = [
        r"(?:до|не\s+дороже|не\s+выше|максимум|до\s+цены)\s*(\d[\d\s]{2,})",
        r"(?:за|около|примерно)\s*(\d[\d\s]{2,})\s*(?:₽|р|руб|руб\.|рублей|рубля)?",
        r"(\d[\d\s]{2,})\s*(?:₽|р|руб|руб\.|рублей|рубля)",
    ]

    for pattern in price_patterns:
        match = re.search(pattern, compact, flags=re.IGNORECASE)
        if not match:
            continue
        digits = re.sub(r"[^\d]", "", match.group(1))
        if digits:
            return int(digits)

    k_match = re.search(r"(\d{2,3})\s*[кk]\b", compact)
    if k_match:
        return int(k_match.group(1)) * 1000

    return None


def parse_region(text: str, default_region: str) -> Tuple[str, str]:
    lowered = text.lower()
    for alias in sorted(REGION_ALIASES, key=len, reverse=True):
        if alias in lowered:
            slug = REGION_ALIASES[alias]
            return slug, REGION_LABELS.get(slug, slug)
    return default_region, REGION_LABELS.get(default_region, default_region)


def build_search_query(text: str) -> str:
    cleaned = text.lower()
    cleaned = re.sub(r"^(найди(?:\s+мне)?|ищу|хочу|нужен|нужна|нужно)\s+", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(
        r"\b(именно|пожалуйста|желательно|в\s+регионе|в\s+городе)\b",
        " ",
        cleaned,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(r"\b(дагестане|дагестан|республике\s+дагестан|республика\s+дагестан)\b", " ", cleaned)
    cleaned = re.sub(r"\b(москве|москва|спб|питере|петербурге|санкт[- ]петербург|россии|россия)\b", " ", cleaned)
    cleaned = re.sub(
        r"(?:до|не\s+дороже|не\s+выше|максимум|за|около|примерно)\s*\d[\d\s]*(?:₽|р|руб|руб\.|рублей|рубля)?",
        " ",
        cleaned,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(r"\d[\d\s]*(?:₽|р|руб|руб\.|рублей|рубля)\b", " ", cleaned, flags=re.IGNORECASE)
    cleaned = normalize_text(cleaned)

    # Remove leftover prepositions/particles after stripping region/price fragments.
    stop_tokens = {"в", "на", "по", "с", "со", "из", "для", "к", "у", "и", "а"}
    tokens = [tok for tok in cleaned.split(" ") if tok and tok not in stop_tokens]
    return " ".join(tokens)


def parse_human_request(text: str, default_region: str) -> Tuple[str, str, str, Optional[int]]:
    region_slug, region_label = parse_region(text, default_region)
    max_price = parse_price(text)
    query = build_search_query(text)
    if not query:
        query = normalize_text(text)
    return query, region_slug, region_label, max_price


def tracking_key(query: str, region: str, max_price: Optional[int]) -> str:
    return f"{region}|{max_price or 0}|{query.lower()}"


def get_rate_limit_until_ts(store: StateStore) -> int:
    return int(store.get_setting("rate_limited_until_ts", "0") or "0")


def set_rate_limit_until_ts(store: StateStore, ts: int) -> None:
    store.set_setting("rate_limited_until_ts", str(max(0, ts)))


def calc_retry_after_seconds(http_exc: requests.HTTPError, fallback_seconds: int) -> int:
    if http_exc.response is not None:
        header = http_exc.response.headers.get("Retry-After", "").strip()
        if header.isdigit():
            return max(60, int(header))
    return fallback_seconds


def send_initial_preview(
    tg: TelegramClient,
    store: StateStore,
    chat_id: str,
    monitor: AvitoMonitor,
    query: str,
    region: str,
    max_price: Optional[int],
) -> None:
    now_ts = int(time.time())
    rate_limited_until = get_rate_limit_until_ts(store)
    if now_ts < rate_limited_until:
        wait_min = max(1, (rate_limited_until - now_ts) // 60)
        tg.send_message(chat_id, f"Avito ограничил запросы. Попробую снова примерно через {wait_min} мин.")
        return

    try:
        _url, listings = monitor.fetch(query, region, max_price)
    except requests.HTTPError as exc:
        status_code = exc.response.status_code if exc.response is not None else None
        if status_code == 429:
            retry_after = calc_retry_after_seconds(exc, fallback_seconds=3600)
            set_rate_limit_until_ts(store, int(time.time()) + retry_after)
            tg.send_message(chat_id, "Avito временно ограничил запросы (429). Повторю позже автоматически.")
        else:
            tg.send_message(chat_id, "Не удалось получить объявления с Avito прямо сейчас. Повторю позже.")
        return
    except Exception:
        tg.send_message(chat_id, "Не удалось получить объявления с Avito прямо сейчас. Повторю позже.")
        return

    if not listings:
        tg.send_message(chat_id, "Пока не вижу объявлений по этому запросу.")
        return

    tg.send_message(chat_id, f"Сейчас на Avito есть {len(listings)} объявлений. Показываю первые 3:")
    for item in listings[:3]:
        tg.send_photo_card(chat_id, item)


def is_authorized(chat_id: str, store: StateStore, env_admin_chat_id: Optional[str]) -> bool:
    if env_admin_chat_id:
        return chat_id == env_admin_chat_id

    saved = store.get_setting("admin_chat_id")
    if saved:
        return chat_id == saved

    # First user who writes to the bot becomes owner when ADMIN_CHAT_ID is not set.
    store.set_setting("admin_chat_id", chat_id)
    return True


def get_runtime_settings(
    store: StateStore, default_interval: int, default_region: str, min_interval: int
) -> Tuple[bool, Optional[str], int, str, Optional[int]]:
    enabled = store.get_setting("enabled", "0") == "1"
    query = store.get_setting("query", None)
    interval = int(store.get_setting("interval", str(default_interval)) or default_interval)
    interval = max(min_interval, interval)
    region = store.get_setting("region", default_region) or default_region
    if region == "respublika_dagestan":
        region = "dagestan"
    max_price_raw = store.get_setting("max_price", "")
    max_price = int(max_price_raw) if max_price_raw and max_price_raw.isdigit() else None
    return enabled, query, interval, region, max_price


def set_runtime_settings(
    store: StateStore,
    enabled: bool,
    query: Optional[str],
    interval: int,
    region: Optional[str] = None,
    max_price: Optional[int] = None,
    min_interval: int = 30,
) -> None:
    store.set_setting("enabled", "1" if enabled else "0")
    if query is not None:
        store.set_setting("query", query)
    store.set_setting("interval", str(max(min_interval, interval)))
    if region is not None:
        store.set_setting("region", region)
    store.set_setting("max_price", str(max_price) if max_price else "")


def handle_command(
    text: str,
    chat_id: str,
    tg: TelegramClient,
    store: StateStore,
    default_interval: int,
    default_region: str,
    monitor: AvitoMonitor,
) -> None:
    clean = normalize_text(text)
    if not clean:
        return

    min_interval = 600 if default_interval >= 600 else 30
    enabled, query, interval, region, max_price = get_runtime_settings(
        store, default_interval, default_region, min_interval
    )

    if clean.startswith("/start"):
        tg.send_message(chat_id, "Бот готов к работе.\n" + HELP_TEXT)
        return

    if clean.startswith("/help"):
        tg.send_message(chat_id, HELP_TEXT)
        return

    if clean.startswith("/stop"):
        set_runtime_settings(
            store, False, query, interval, region=region, max_price=max_price, min_interval=min_interval
        )
        tg.send_message(chat_id, "Мониторинг остановлен.")
        return

    if clean.startswith("/status"):
        if query:
            url = monitor.build_search_url(query, region, max_price)
            region_label = REGION_LABELS.get(region, region)
            price_line = f"\nЦена до: {max_price} ₽" if max_price else ""
            msg = (
                f"Статус: {'включен' if enabled else 'выключен'}\n"
                f"Запрос: {query}\n"
                f"Регион: {region_label}"
                f"{price_line}\n"
                f"Интервал: {interval} сек\n"
                f"URL: {url}"
            )
        else:
            msg = f"Статус: {'включен' if enabled else 'выключен'}\nЗапрос еще не задан."
        tg.send_message(chat_id, msg)
        return

    if clean.startswith("/interval"):
        parts = clean.split(maxsplit=1)
        if len(parts) != 2 or not parts[1].isdigit():
            tg.send_message(chat_id, f"Используй: /interval {max(120, min_interval)}")
            return
        new_interval = max(min_interval, int(parts[1]))
        set_runtime_settings(
            store, enabled, query, new_interval, region=region, max_price=max_price, min_interval=min_interval
        )
        tg.send_message(chat_id, f"Интервал обновлен: {new_interval} сек")
        return

    if clean.startswith("/track"):
        parts = clean.split(maxsplit=1)
        if len(parts) != 2 or not normalize_text(parts[1]):
            tg.send_message(chat_id, "Пример: /track playstation 5 slim за 36000 в дагестане")
            return

        parsed_query, parsed_region, parsed_region_label, parsed_max_price = parse_human_request(
            parts[1], default_region
        )
        set_runtime_settings(
            store,
            True,
            parsed_query,
            interval,
            region=parsed_region,
            max_price=parsed_max_price,
            min_interval=min_interval,
        )
        price_line = f"\nЦена до: {parsed_max_price} ₽" if parsed_max_price else ""
        tg.send_message(
            chat_id,
            "Ок, включил мониторинг.\n"
            f"Запрос: {parsed_query}\n"
            f"Регион: {parsed_region_label}"
            f"{price_line}\n"
            f"Ссылка: {monitor.build_search_url(parsed_query, parsed_region, parsed_max_price)}",
        )
        send_initial_preview(tg, store, chat_id, monitor, parsed_query, parsed_region, parsed_max_price)
        return

    parsed_query, parsed_region, parsed_region_label, parsed_max_price = parse_human_request(clean, default_region)
    set_runtime_settings(
        store,
        True,
        parsed_query,
        interval,
        region=parsed_region,
        max_price=parsed_max_price,
        min_interval=min_interval,
    )
    price_line = f"\nЦена до: {parsed_max_price} ₽" if parsed_max_price else ""
    tg.send_message(
        chat_id,
        "Ок, ищу.\n"
        f"Запрос: {parsed_query}\n"
        f"Регион: {parsed_region_label}"
        f"{price_line}\n"
        f"Ссылка: {monitor.build_search_url(parsed_query, parsed_region, parsed_max_price)}",
    )
    send_initial_preview(tg, store, chat_id, monitor, parsed_query, parsed_region, parsed_max_price)


def process_updates(
    tg: TelegramClient,
    store: StateStore,
    env_admin_chat_id: Optional[str],
    default_interval: int,
    default_region: str,
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
            handle_command(text, chat_id, tg, store, default_interval, default_region, monitor)

        offset = max(offset, update_id + 1)

    store.set_setting("tg_offset", str(offset))


def run_monitor_cycle(
    tg: TelegramClient,
    store: StateStore,
    monitor: AvitoMonitor,
    max_notifications: int,
    default_interval: int,
    default_region: str,
    min_interval: int,
) -> None:
    enabled, query, _interval, region, max_price = get_runtime_settings(
        store, default_interval, default_region, min_interval
    )
    if not enabled or not query:
        return

    admin_chat_id = store.get_setting("admin_chat_id")
    if not admin_chat_id:
        return

    search_key = tracking_key(query, region, max_price)
    search_url, listings = monitor.fetch(query, region, max_price)
    new_items = [item for item in listings if not store.is_seen(search_key, item.item_id)]

    if not store.is_query_bootstrapped(search_key):
        for item in new_items:
            store.mark_seen(search_key, item)
        store.mark_query_bootstrapped(search_key)
        region_label = REGION_LABELS.get(region, region)
        price_line = f"\nЦена до: {max_price} ₽" if max_price else ""
        tg.send_message(
            admin_chat_id,
            f"Мониторинг активен. Базово сохранено {len(new_items)} текущих объявлений.\n"
            f"Дальше будут приходить только новые.\n"
            f"Запрос: {query}\n"
            f"Регион: {region_label}"
            f"{price_line}\n"
            f"{search_url}",
        )
        return

    notified = 0
    for item in new_items:
        if notified < max_notifications:
            tg.send_photo_card(admin_chat_id, item)
            notified += 1
        store.mark_seen(search_key, item)

    if notified > 0:
        tg.send_message(admin_chat_id, f"Новых объявлений: {len(new_items)}, отправлено: {notified}")


def main() -> None:
    cfg = parse_config()
    store = StateStore(cfg["db_path"])
    tg = TelegramClient(cfg["token"])
    monitor = AvitoMonitor()

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
                default_region=cfg["default_region"],
                monitor=monitor,
            )

            enabled, query, interval, _region, _max_price = get_runtime_settings(
                store, cfg["default_interval"], cfg["default_region"], cfg["min_interval"]
            )
            now = time.time()
            rate_limited_until = get_rate_limit_until_ts(store)

            if enabled and query and now >= next_check_ts:
                if int(now) < rate_limited_until:
                    next_check_ts = max(float(rate_limited_until), now + 60)
                    continue
                try:
                    run_monitor_cycle(
                        tg=tg,
                        store=store,
                        monitor=monitor,
                        max_notifications=cfg["max_notifications"],
                        default_interval=cfg["default_interval"],
                        default_region=cfg["default_region"],
                        min_interval=cfg["min_interval"],
                    )
                    jitter = random.randint(0, cfg["jitter_seconds"]) if cfg["soft_mode"] else 0
                    next_check_ts = now + interval + jitter
                except requests.HTTPError as http_exc:
                    code = http_exc.response.status_code if http_exc.response is not None else None
                    admin_chat_id = store.get_setting("admin_chat_id")
                    if code == 429:
                        retry_after = calc_retry_after_seconds(http_exc, fallback_seconds=max(interval * 6, 1800))
                        until_ts = int(now) + retry_after
                        set_rate_limit_until_ts(store, until_ts)
                        next_check_ts = float(until_ts)
                        last_notice_ts = int(store.get_setting("last_rate_limit_notice_ts", "0") or "0")
                        if admin_chat_id and (time.time() - last_notice_ts > 3600):
                            tg.send_message(
                                admin_chat_id,
                                "Avito временно ограничил запросы (429). "
                                "Я включил паузу и попробую снова позже.",
                            )
                            store.set_setting("last_rate_limit_notice_ts", str(int(time.time())))
                    else:
                        next_check_ts = now + max(interval * 2, 600)
                    print(f"[error] HTTPError: {http_exc}")
            elif not enabled:
                next_check_ts = now + 10

        except Exception as exc:
            print(f"[error] {type(exc).__name__}: {exc}")
            time.sleep(5)


if __name__ == "__main__":
    main()
