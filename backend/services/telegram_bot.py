import os
import sys
import asyncio
import datetime
import json
from services.nlp import get_today, get_yesterday
import io
import uuid

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

import httpx
from dotenv import load_dotenv

from services.nlp import (
    process_ai_data_ingestion,
    register_owner_event,
    detect_intent,
    load_database,
    save_database,
    _get_restaurant,
    _do_generate_forecast,
    process_image_upload,
)
from services.ai_provider import call_ai
from services.file_processor import process_upload
from services import auth
from services.bom_ai import ask_bom_conversational
from services.send_queue import get_send_queue, get_update_dedup
from services.tg_http import get_tg_client, recycle_tg_client as recycle_tg_client_async

load_dotenv()

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
TG_API = f"https://api.telegram.org/bot{TELEGRAM_TOKEN }"
MAX_FILE_BYTES = 5_000_000
ALLOWED_DOC_EXT = {".csv", ".txt", ".xlsx", ".xls"}
ALLOWED_IMG_EXT = {".jpg", ".jpeg", ".png", ".webp"}

GREETINGS = {
    "hi",
    "hello",
    "hey",
    "hye",
    "yo",
    "sup",
    "start",
    "hai",
    "helo",
    "heelo",
    "helo",
    "hii",
    "hiii",
    "oi",
    "woi",
    "good morning",
    "good afternoon",
    "good evening",
    "selamat pagi",
    "selamat petang",
    "selamat malam",
    "assalamualaikum",
    "salam",
}

_session_state: dict = {}
_session_data: dict = {}

# Fast typo-tolerant greeting detection ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Â runs in <1ms, no AI needed.
_GREETING_PREFIXES = ("hel", "hay", "hey", "hiy", "hye", "hee", "good ")


def _is_greeting(text: str) -> bool:
    """Return True if the text is a greeting, tolerating common typos."""
    tl = text.strip().lower()
    if tl in GREETINGS:
        return True
    # Single-word short messages: check prefix and length
    if " " not in tl and len(tl) <= 8:
        for pfx in _GREETING_PREFIXES:
            if tl.startswith(pfx):
                return True
    return False


# ---------------------------------------------------------------------------
# Local semantic intent classifier (sentence-transformers)
# Classifies user messages in ~5ms locally ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Â no API call, no hardcoding.
# Falls back to full AI detect_intent only when confidence is low.
# ---------------------------------------------------------------------------

_clf_model = None
_clf_mutex = threading.Lock()
_clf_seed_cache: dict = {}  # intent -> precomputed embedding array

# One example phrase per intent ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Â the model generalises from these semantically.
# Typos (forcast), Hindi, and Malay phrases included for real-world coverage.
_INTENT_SEEDS: dict[str, list[str]] = {
    "fetch_sales": [
        "what are my sales today",
        "how many items did I sell",
        "show me today's sales",
        "what is the today's lassi sale",
        "what is today's lassi sale",
        "how many lassi sold today",
        "how many samosas sold",
        "how many lasi sold today",
        "how many kachori did I sell today",
        "sales report",
        "aaj kitna bika",
        "what did I sell today",
        "how many portions of lassi sold",
        "show my sales for today",
        "today's sale of lassi",
        "how much did I sell today",
        "how many units sold today",
        "sales on 18 may",
        "what were my sales on 2026-05-18",
        "show sales for yesterday",
        "kal kitni lassi biki",
        "aaj ki lassi ki sale",
        "how many chai sold",
        "how many mirchi bada sold today",
        "total sales today",
    ],
    "forecast": [
        "what should I prepare today",
        "how much to cook today",
        "today's AI forecast",
        "how many portions should I make",
        "forcast for today",
        "aaj kitna banana hai",
        "ramalan untuk hari ini",
        "tell me today's preparation plan",
    ],
    "menu_show": [
        "show me the menu",
        "what's on my menu",
        "list my menu items",
        "what items do I sell",
        "display all my dishes",
        "menu dikhao",
        "sab items batao",
        "tunjukkan menu saya",
    ],
    "sales": [
        "sold 50 samosas today",
        "I sold this much today",
        "reporting my daily sales",
        "we had leftovers",
        "aaj 80 biryani becha",
        "bik gaya sab kuch",
        "sales data update for today",
    ],
    "event": [
        "tomorrow is a wedding with 200 guests",
        "birthday party next week",
        "100 guests are coming tomorrow",
        "festival event this weekend",
        "kal shaadi hai 200 log aayenge",
        "function hai kal bade order ke saath",
    ],
    "menu_add": [
        "add samosa to my menu",
        "add dosa to menu",
        "add dosa to my menu",
        "I want to add a new dish",
        "add a new item to the menu",
        "put a new item on the menu",
        "include biryani in my offerings",
        "add Milo Ais to menu",
        "add new item",
        "menu mein samosa add karo",
        "naya item daalo menu mein",
        "add this to the menu",
        "I want to sell a new dish",
        "list a new item",
    ],
    "menu_remove": [
        "remove samosa from the menu",
        "delete this item from menu",
        "stop selling dosa",
        "take this off the menu",
        "menu se item hatao",
        "yeh item band karo",
    ],
    "causal_analysis": [
        "why did my sales drop yesterday",
        "what caused poor performance",
        "root cause of low revenue",
        "why was business bad today",
        "kyun sales kam thi",
        "analyse what went wrong with sales",
    ],
    "menu_engineering": [
        "which items perform best for me",
        "analyse my menu profitability",
        "BCG matrix for my dishes",
        "which items should I promote or remove",
        "rank my menu items by profit",
        "menu analysis karo",
    ],
    "inventory": [
        "how much stock is left today",
        "what's remaining right now",
        "check my remaining inventory",
        "how many portions are still left",
        "baki kitna hai abhi",
        "leftover stock check karo",
        "kitna bacha hai aaj",
    ],
    "orders": [
        "show my orders today",
        "pending marketplace orders",
        "who has ordered from me today",
        "list all customer orders",
        "aaj ke orders dikhao",
        "koi order hai kya aaj",
        "check delivery orders status",
    ],
    "profit": [
        "how much did I earn today",
        "today's revenue summary",
        "what is my profit today",
        "daily earnings report",
        "aaj kitna kamaya",
        "show me today's income",
        "kitni kamai hui aaj",
    ],
    "security": [
        "who is logged into my account",
        "show active sessions",
        "manage my logged in devices",
        "logout from other devices",
        "kaun login hai mere account mein",
        "account security check karo",
    ],
    "help": [
        "how do I use this bot",
        "what can you do for me",
        "show me all commands",
        "guide me through the features",
        "bot kaise use kare",
        "main kya kya kar sakta hoon yahan",
    ],
    "general": [
        "I have a random question",
        "tell me something",
        "what do you think",
    ],
}



def _load_clf_model():
    """Load sentence-transformers model once (thread-safe). Returns model or None."""
    global _clf_model
    with _clf_mutex:
        if _clf_model is not None:
            return _clf_model
        try:
            from sentence_transformers import SentenceTransformer
            print("[LocalCLF] Loading all-MiniLM-L6-v2 (first time)ÃƒÂ¢Ã¢â€šÂ¬Ã‚Â¦")
            _clf_model = SentenceTransformer("all-MiniLM-L6-v2")
            # Pre-compute seed embeddings once
            for intent, seeds in _INTENT_SEEDS.items():
                _clf_seed_cache[intent] = _clf_model.encode(seeds, convert_to_tensor=True)
            print("[LocalCLF] Model ready.")
            return _clf_model
        except Exception as e:
            print(f"[LocalCLF] Could not load: {e}")
            return None


def _prewarm_classifier() -> None:
    """Pre-load the sentence-transformer model in a background daemon thread.

    Eliminates the 5-10s cold-start delay on the first user message.
    Called once when this module is imported (i.e. at uvicorn startup).
    """
    
    def _do_load():
        try:
            _load_clf_model()
            print("[LocalCLF] Pre-warm complete ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Â model ready for instant classification.")
        except Exception as _e:
            print(f"[LocalCLF] Pre-warm failed (non-fatal): {_e}")

    t = threading.Thread(target=_do_load, daemon=True, name="clf-prewarm")
    t.start()


# Kick off pre-warm immediately when this module is imported at uvicorn startup.
_prewarm_classifier()


def _classify_intent_local(text: str) -> tuple[str, float]:
    """
    Classify intent using local sentence-transformers (~5ms after first load).
    Returns (intent_name, confidence_0_to_1).
    Confidence < 0.45 means ambiguous ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Â caller should fall back to AI.
    """
    # FAST PATH: If the model is currently downloading/loading in the background,
    # skip local classification and fall back to Gemini immediately!
    # This completely eliminates the 60-second block on the very first message.
    if _clf_model is None:
        if not _clf_mutex.acquire(blocking=False):
            print("[LocalCLF] Model is loading in background. Bypassing to Gemini for instant response.")
            return "general", 0.0
        else:
            _clf_mutex.release()

    model = _load_clf_model()
    if model is None:
        return "general", 0.0
    try:
        from sentence_transformers import util as st_util
        text_emb = model.encode(text, convert_to_tensor=True)
        best_intent, best_score = "general", 0.0
        for intent, seed_embs in _clf_seed_cache.items():
            scores = st_util.cos_sim(text_emb, seed_embs)[0]
            max_score = float(scores.max())
            if max_score > best_score:
                best_score = max_score
                best_intent = intent
        return best_intent, best_score
    except Exception as e:
        print(f"[LocalCLF] Classify error: {e}")
        return "general", 0.0


def _otp_minutes_note() -> str:
    return f"(valid for {auth.OTP_TTL_SECONDS} seconds)"


# ---------------------------------------------------------------------------
# Telegram HTTP client ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Â shared module (services/tg_http.py)
# Both telegram_bot.py and send_queue.py use the SAME persistent client so
# the startup warm-up benefit applies to message delivery immediately.
# ---------------------------------------------------------------------------

# Re-export for backward compatibility (main.py imports these names)
def _get_tg_client() -> httpx.AsyncClient:
    """Return the shared persistent Telegram HTTP client."""
    return get_tg_client()


async def _reset_tg_client() -> httpx.AsyncClient:
    """Force-close and recycle the shared client. Called by the warm-up loop."""
    return await recycle_tg_client_async()


# Exceptions that are worth retrying (transient network issues)
_RETRYABLE_ERRORS = (
    httpx.ConnectTimeout,
    httpx.ReadTimeout,
    httpx.WriteTimeout,
    httpx.ConnectError,
    httpx.RemoteProtocolError,
)


async def _api(client: httpx.AsyncClient, method: str, **kwargs) -> dict:
    bridge_url = os.environ.get("BRIDGE_URL", "").rstrip("/")
    timeout = httpx.Timeout(connect=40.0, read=30.0, write=15.0, pool=10.0)

    if bridge_url:
        internal_secret = os.environ.get("INTERNAL_SECRET", "")
        headers = {"X-Internal-Secret": internal_secret} if internal_secret else {}
        resp = await client.post(
            f"{bridge_url}/api/forward",
            json={"method": method, "params": kwargs},
            headers=headers,
            timeout=timeout
        )
    else:
        url = f"{TG_API}/{method}"
        resp = await client.post(url, json=kwargs, timeout=timeout)

    return resp.json()


async def _send(
    client: httpx.AsyncClient,
    chat_id: int,
    text: str,
    parse_mode: str = "Markdown",
    reply_markup=None,
) -> None:
    """
    Enqueue a Telegram message for delivery.  Returns immediately ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Â never blocks
    on network I/O.  The send_queue background worker handles retries, circuit
    breaking, and exponential back-off transparently.

    The `client` parameter is kept for API compatibility but is no longer used
    here; all outbound sendMessage calls are owned by the send_queue worker.
    """
    await get_send_queue().enqueue(chat_id, text, parse_mode, reply_markup)



async def _typing(client: httpx.AsyncClient, chat_id: int) -> None:
    """Best-effort typing indicator ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Â never raises, never crashes the handler."""
    try:
        await _api(client, "sendChatAction", chat_id=chat_id, action="typing")
    except Exception:
        pass  # Typing indicator is cosmetic; silently skip on network issues


async def _download_file(client: httpx.AsyncClient, file_id: str) -> bytes | None:
    bridge_url = os.environ.get("BRIDGE_URL", "").rstrip("/")
    if bridge_url:
        internal_secret = os.environ.get("INTERNAL_SECRET", "")
        headers = {"X-Internal-Secret": internal_secret} if internal_secret else {}
        try:
            r = await client.post(
                f"{bridge_url}/api/download",
                json={"file_id": file_id},
                headers=headers,
                timeout=60
            )
            return r.content if r.status_code == 200 else None
        except Exception as e:
            print(f"[Download] Bridge error: {e}")
            return None

    # Fallback to direct Telegram API if no bridge
    resp = await _api(client, "getFile", file_id=file_id)
    if not resp.get("ok"):
        return None
    path = resp["result"]["file_path"]
    
    url = f"{TG_API.replace('/bot', '/file/bot')}/{path}"
    try:
        r = await client.get(url, timeout=60)
        return r.content if r.status_code == 200 else None
    except Exception:
        return None


def _keyboard(rows: list) -> dict:
    return {"keyboard": rows, "resize_keyboard": True, "one_time_keyboard": True}


def _inline_keyboard(rows: list) -> dict:
    return {"inline_keyboard": rows}


def _rest_keyboard() -> dict:
    db = load_database()
    rows = [[{"text": r["name"]}] for r in db.get("restaurants", [])]
    rows.append([{"text": "ÃƒÂ¢Ã…Â¾Ã¢â‚¬Â¢ Register my restaurant"}])
    return _keyboard(rows)


def _get_state(chat_id: int) -> str | None:
    return _session_state.get(chat_id)


def _set_state(chat_id: int, state: str | None) -> None:
    if state is None:
        _session_state.pop(chat_id, None)
    else:
        _session_state[chat_id] = state


def _get_data(chat_id: int) -> dict:
    return _session_data.get(chat_id, {})


def _set_data(chat_id: int, **kwargs) -> None:
    _session_data.setdefault(chat_id, {}).update(kwargs)


def _clear_data(chat_id: int, *keys) -> None:
    if chat_id in _session_data:
        for k in keys:
            _session_data[chat_id].pop(k, None)


def _get_rest_id(chat_id: int) -> str | None:
    stored = _get_data(chat_id).get("restaurant_id")
    if stored:
        return stored

    db = load_database()
    for r in db.get("restaurants", []):
        if r.get("telegram_chat_id") == chat_id:
            _set_data(chat_id, restaurant_id=r["id"])
            return r["id"]

    return None


def _validate_file(filename: str, data: bytes) -> tuple:
    if not filename:
        return False, "File has no name."
    ext = os.path.splitext(filename.lower())[1]
    if ext not in (ALLOWED_DOC_EXT | ALLOWED_IMG_EXT):
        return (
            False,
            f"File type '{ext }' is not supported. Please send: CSV, Excel (.xlsx), JPG, or PNG.",
        )
    if len(data) > MAX_FILE_BYTES:
        return False, f"File is too large ({len (data )/1_000_000 :.1f} MB). Max 5 MB."
    if len(data) < 10:
        return False, "File appears empty or corrupted."
    if ext in {".csv", ".txt"}:
        sample = data[:2000].decode("utf-8", errors="ignore").lower()
        for bad in ["<script", "<?php", "subprocess", "__import__", "exec(", "eval("]:
            if bad in sample:
                return False, "File contains suspicious content and cannot be processed."
    return True, "ok"


def _strip_image_metadata(data: bytes) -> bytes:
    try:
        from PIL import Image

        img = Image.open(io.BytesIO(data))
        if img.mode not in ("RGB", "L"):
            img = img.convert("RGB")
        buf = io.BytesIO()
        img.save(buf, format="JPEG", optimize=True)
        clean = buf.getvalue()
        return clean if len(clean) > 1000 else data
    except Exception:
        return data


PRIVACY_NOTICE = (
    "ÃƒÂ°Ã…Â¸Ã¢â‚¬ÂÃ¢â‚¬â„¢ *Privacy Notice*\n\n"
    "Before we start, here's what WasteWise AI stores:\n\n"
    "ÃƒÂ¢Ã¢â€šÂ¬Ã‚Â¢ Your restaurant name and location type\n"
    "ÃƒÂ¢Ã¢â€šÂ¬Ã‚Â¢ Menu items and daily sales quantities you upload\n"
    "ÃƒÂ¢Ã¢â€šÂ¬Ã‚Â¢ Photos you send (processed immediately, not stored permanently)\n"
    "ÃƒÂ¢Ã¢â€šÂ¬Ã‚Â¢ Telegram chat ID (to send you daily check-ins)\n\n"
    "Your data is used *only* to improve your own forecasts. "
    "Cross-restaurant signals use anonymised category trends ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Â never your restaurant's name or revenue.\n\n"
    "Do you agree to continue?"
)


async def start_registration(client: httpx.AsyncClient, chat_id: int) -> None:
    _set_state(chat_id, "reg_privacy")
    kb = _inline_keyboard(
        [
            [
                {"text": "ÃƒÂ¢Ã…â€œÃ¢â‚¬Â¦ I agree", "callback_data": "reg:privacy_accept"},
                {"text": "ÃƒÂ¢Ã‚ÂÃ…â€™ No thanks", "callback_data": "reg:privacy_decline"},
            ]
        ]
    )
    await _send(client, chat_id, PRIVACY_NOTICE, reply_markup=kb)


async def _reg_step_name(client: httpx.AsyncClient, chat_id: int) -> None:
    _set_state(chat_id, "reg_name")
    await _send(
        client,
        chat_id,
        "Great! Let's set up your restaurant.\n\n"
        "What is your restaurant's name? (e.g. *Ali Pyaaz Kachori*)",
    )


async def _reg_step_owner(client: httpx.AsyncClient, chat_id: int) -> None:
    _set_state(chat_id, "reg_owner")
    await _send(client, chat_id, "What's your name? (Just your first name or nickname is fine)")


async def _reg_step_type(client: httpx.AsyncClient, chat_id: int) -> None:
    _set_state(chat_id, "reg_type")
    kb = _keyboard(
        [
            [{"text": "ÃƒÂ°Ã…Â¸Ã‚ÂÃ¢â‚¬Âº Hawker / Gerai"}, {"text": "ÃƒÂ°Ã…Â¸Ã‚ÂÃ‚Âµ Mamak"}],
            [{"text": "ÃƒÂ¢Ã‹Å“Ã¢â‚¬Â¢ CafÃƒÆ’Ã‚Â© / Kopitiam"}, {"text": "ÃƒÂ°Ã…Â¸Ã‚ÂÃ‚Â½ÃƒÂ¯Ã‚Â¸Ã‚Â Restaurant"}],
            [{"text": "ÃƒÂ°Ã…Â¸Ã‚ÂÃ‚Â¦ Dessert Stall"}, {"text": "ÃƒÂ°Ã…Â¸Ã‚ÂÃ‚Â¡ Other"}],
        ]
    )
    await _send(client, chat_id, "What type of food business is it?", reply_markup=kb)


async def _reg_step_region(client: httpx.AsyncClient, chat_id: int) -> None:
    _set_state(chat_id, "reg_region")
    await _send(
        client,
        chat_id,
        "Which area or city is your restaurant in?\n\n"
        "Just type naturally ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Â for example:\n"
        "_Lajpat Nagar_, _Sadar Bazaar KL_, _Georgetown Penang_, _Setia Alam Shah Alam_",
        reply_markup={"remove_keyboard": True},
    )


async def _reg_confirm(client: httpx.AsyncClient, chat_id: int) -> None:
    data = _get_data(chat_id)
    name = data.get("reg_name", "?")
    owner = data.get("reg_owner", "?")
    rtype = data.get("reg_type", "?")
    region = data.get("reg_region", "?")
    closing = data.get("reg_closing_time", "21:00")
    kb = _inline_keyboard(
        [
            [
                {"text": "ÃƒÂ¢Ã…â€œÃ¢â‚¬Â¦ Looks good!", "callback_data": "reg:confirm"},
                {"text": "ÃƒÂ°Ã…Â¸Ã¢â‚¬ÂÃ‚Â Start over", "callback_data": "reg:restart"},
            ]
        ]
    )
    await _send(
        client,
        chat_id,
        f"*Here's what I've got:*\n\n"
        f"ÃƒÂ°Ã…Â¸Ã‚ÂÃ‚Âª Restaurant: *{name }*\n"
        f"ÃƒÂ°Ã…Â¸Ã¢â‚¬ËœÃ‚Â¤ Owner: *{owner }*\n"
        f"ÃƒÂ°Ã…Â¸Ã¢â‚¬Å“Ã¢â‚¬Â¹ Type: *{rtype }*\n"
        f"ÃƒÂ°Ã…Â¸Ã¢â‚¬Å“Ã‚Â Area: *{region }*\n"
        f"ÃƒÂ¢Ã‚ÂÃ‚Â° Closing Time: *{closing }*\n\n"
        "Is this correct?",
        reply_markup=kb,
    )


async def _reg_complete(client: httpx.AsyncClient, chat_id: int) -> None:
    data = _get_data(chat_id)
    name = data.get("reg_name", "My Restaurant")
    owner = data.get("reg_owner", "Owner")
    rtype = (
        data.get("reg_type", "hawker")
        .lower()
        .split("/")[0]
        .strip()
        .replace(" ", "_")
        .replace("ÃƒÆ’Ã‚Â©", "e")
    )
    region = data.get("reg_region", "India")
    closing_time = data.get("reg_closing_time", "21:00")

    rest_id = "rest_" + str(uuid.uuid4())[:8]
    new_rest = {
        "id": rest_id,
        "name": name,
        "region": region,
        "type": rtype,
        "owner_name": owner,
        "telegram_chat_id": chat_id,
        "privacy_accepted": True,
        "registered_at": datetime.datetime.now().isoformat(),
        "specialty_weather": "neutral",
        "bom": {},
        "menu": [],
        "recent_feedback_memory": [],
        "active_events": [],
        "daily_records": [],
        "closing_time": closing_time,
        "discount_pct": 30,
        "marketplace_enabled": True,
    }

    db = load_database()
    db.setdefault("restaurants", []).append(new_rest)

    if region not in db.get("regions", {}):
        db.setdefault("regions", {})[region] = {
            "type": "General Area",
            "foot_traffic_baseline": 500,
            "weekend_multiplier": 1.1,
            "holiday_multiplier": 1.0,
            "rain_impact": -0.2,
        }
    save_database(db)

    _set_data(chat_id, restaurant_id=rest_id)
    _set_state(chat_id, None)
    _clear_data(chat_id, "reg_name", "reg_owner", "reg_type", "reg_region", "reg_closing_time")

    await _send(
        client,
        chat_id,
        f"ÃƒÂ°Ã…Â¸Ã…Â½Ã¢â‚¬Â° *Welcome to WasteWise AI, {owner }!*\n\n"
        f"*{name }* is now registered.\n"
        f"ÃƒÂ¢Ã‚ÂÃ‚Â° Closing time set to *{closing_time }* ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Â I'll send you an inventory report automatically!\n\n"
        "Let's get started! Add your first menu items ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Â just tell me what you sell:\n\n"
        "_'I sell Pyaaz Kachori, Masala Chai, and Ghevar'_\n\n"
        "Or upload a CSV/Excel with your menu.\n\n"
        "Once you have at least 3 days of sales data, I'll start making forecasts for you.",
        reply_markup={"remove_keyboard": True},
    )


async def _reg_step_closing_time(client: httpx.AsyncClient, chat_id: int) -> None:
    _set_state(chat_id, "reg_closing_time")
    kb = _keyboard(
        [
            [{"text": "ÃƒÂ°Ã…Â¸Ã¢â‚¬Â¢Ã‚Â 21:00 (9 PM)"}, {"text": "ÃƒÂ°Ã…Â¸Ã¢â‚¬Â¢Ã‚Â 22:00 (10 PM)"}],
            [{"text": "ÃƒÂ°Ã…Â¸Ã¢â‚¬Â¢Ã‚Â 20:00 (8 PM)"}, {"text": "ÃƒÂ°Ã…Â¸Ã¢â‚¬Â¢Ã‚Â 23:00 (11 PM)"}],
            [{"text": "ÃƒÂ°Ã…Â¸Ã¢â‚¬Â¢Ã‚Â 18:00 (6 PM)"}, {"text": "ÃƒÂ°Ã…Â¸Ã¢â‚¬Â¢Ã‚Â 19:00 (7 PM)"}],
        ]
    )
    await _send(
        client,
        chat_id,
        "ÃƒÂ¢Ã‚ÂÃ‚Â° At what time do you usually *close* your shop?\n\n"
        "This is when I'll automatically send you an inventory report and list any remaining food "
        "at a discount on the customer marketplace!\n\n"
        "Pick one or type your own time (e.g. *21:30*)",
        reply_markup=kb,
    )


async def _ask_bom_interactive(client: httpx.AsyncClient, chat_id: int, item_name: str) -> None:
    """Ask the owner about ingredients interactively. Offers 'don't know' option."""
    _set_state(chat_id, f"bom_item:{item_name }")
    await _send(
        client,
        chat_id,
        f"ÃƒÂ°Ã…Â¸Ã‚Â¥Ã‹Å“ What ingredients go into *{item_name }*?\n\n"
        "Example: _'200g rice, 50ml coconut milk, 20g dried anchovies'_\n\n"
        "Or type *don't know* ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Â I'll look up the typical recipe for your area.",
    )


async def _set_bom_for_item(
    client: httpx.AsyncClient, chat_id: int, item_name: str, bom_text: str
) -> None:
    """Parse and save owner-defined BOM for a menu item."""
    rest_id = _get_rest_id(chat_id)
    if not rest_id:
        return
    db = load_database()
    rest = _get_restaurant(db, rest_id)
    if not rest:
        return

    prompt = (
        f"The owner of a indian restaurant defined the ingredients for '{item_name }':\n"
        f"'{bom_text }'\n\n"
        "Extract as a JSON dict where keys are ingredient names (use snake_case with _g for grams, "
        "_ml for millilitres) and values are numbers per serving.\n"
        "Example: {\"rice_g\": 200, \"coconut_milk_ml\": 50, \"egg\": 1}\n"
        "Also estimate cost_rm (raw material cost in indian rupee) per serving.\n"
        "Return ONLY valid JSON with no other text."
    )
    result = call_ai(prompt, json_mode=True)
    try:
        
        if isinstance(result, str):
            bom = json.loads(result)
        else:
            bom = result or {}
    except Exception:
        bom = {}

    if bom:
        rest.setdefault("bom", {})[item_name] = bom
        save_database(db)
        ingredients = [f"{k}: {v}" for k, v in bom.items() if k not in ("cost_rm", "cost_inr")]
        cost = bom.get("cost_inr", bom.get("cost_rm", "?"))
        await _send(
            client,
            chat_id,
            f"ÃƒÂ¢Ã…â€œÃ¢â‚¬Â¦ Saved ingredient ratios for *{item_name}*:\n"
            + "\n".join(f"  ÃƒÂ¢Ã¢â€šÂ¬Ã‚Â¢ {i}" for i in ingredients)
            + f"\n  ÃƒÂ¢Ã¢â€šÂ¬Ã‚Â¢ Estimated cost: ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¹{cost} per serving\n\n"
            "Your shopping list will now be accurate for this item.",
        )
    else:
        await _send(
            client,
            chat_id,
            f"ÃƒÂ¢Ã…Â¡Ã‚Â ÃƒÂ¯Ã‚Â¸Ã‚Â Could not parse that. Please try again with a clearer format:\n"
            f"_'200g rice, 50ml coconut milk, 1 egg'_",
        )


async def _process_csv(client: httpx.AsyncClient, chat_id: int, csv_type: str) -> None:
    data = _get_data(chat_id)
    raw = data.get("pending_csv")
    rest_id = _get_rest_id(chat_id)
    if not raw:
        await _send(client, chat_id, "ÃƒÂ¢Ã…Â¡Ã‚Â ÃƒÂ¯Ã‚Â¸Ã‚Â No file data found. Please upload again.")
        return
    mode_map = {"sales": "none", "add_menu": "append", "replace_menu": "overwrite"}
    labels = {"none": "sales data", "append": "add menu items", "overwrite": "replace menu"}
    mode = mode_map.get(csv_type, "none")
    await _typing(client, chat_id)
    result = process_ai_data_ingestion(rest_id, raw, menu_mode=mode)
    _set_data(chat_id, last_action=f"csv_{csv_type }", last_result=result)
    await _send(client, chat_id, f"ÃƒÂ¢Ã…â€œÃ¢â‚¬Â¦ Processed as *{labels [mode ]}*.\n\n{result }")
    await _typing(client, chat_id)
    forecast = await asyncio.to_thread(_do_generate_forecast, rest_id)
    await _send(client, chat_id, f"ÃƒÂ°Ã…Â¸Ã¢â‚¬ÂÃ¢â‚¬Å¾ *Updated forecast:*\n\n{forecast }")
    _clear_data(chat_id, "pending_csv", "pending_csv_type")
    _set_state(chat_id, None)


async def _process_photo(
    client: httpx.AsyncClient, chat_id: int, photo_data: bytes, intent: str
) -> None:
    rest_id = _get_rest_id(chat_id)
    _set_state(chat_id, None)
    await _send(client, chat_id, "ÃƒÂ°Ã…Â¸Ã¢â‚¬Å“Ã‚Â¸ Analysing your photoÃƒÂ¢Ã¢â€šÂ¬Ã‚Â¦")
    await _typing(client, chat_id)
    clean = _strip_image_metadata(photo_data)
    result = await asyncio.to_thread(process_image_upload, rest_id, clean, "image/jpeg")
    _set_data(chat_id, last_action=f"photo_{intent }", last_result=result)
    _clear_data(chat_id, "pending_photo_data")

    skip = ["Cannot process", "cannot be used", "Low confidence", "not reliable", "no readable"]
    if any(p.lower() in result.lower() for p in skip):
        await _send(client, chat_id, result)
        await _send(
            client,
            chat_id,
            "ÃƒÂ°Ã…Â¸Ã¢â‚¬â„¢Ã‚Â¡ For best results:\n"
            "ÃƒÂ¢Ã¢â€šÂ¬Ã‚Â¢ Make sure numbers are clearly visible\n"
            "ÃƒÂ¢Ã¢â€šÂ¬Ã‚Â¢ Good lighting ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Â not too dark\n"
            "ÃƒÂ¢Ã¢â€šÂ¬Ã‚Â¢ Hold the camera still\n\n"
            "Or type the numbers directly: _'Pyaaz Kachori 95, Masala Chai 62'_",
        )
        return
    await _send(client, chat_id, result)
    await _typing(client, chat_id)
    forecast = await asyncio.to_thread(_do_generate_forecast, rest_id)
    await _send(client, chat_id, f"ÃƒÂ°Ã…Â¸Ã¢â‚¬ÂÃ¢â‚¬Å¾ *Updated forecast:*\n\n{forecast }")


async def handle_callback(client: httpx.AsyncClient, callback: dict) -> None:
    chat_id = callback["message"]["chat"]["id"]
    msg_id = callback["message"]["message_id"]
    data = callback.get("data", "")
    await _api(client, "answerCallbackQuery", callback_query_id=callback["id"])
    await _api(
        client,
        "editMessageReplyMarkup",
        chat_id=chat_id,
        message_id=msg_id,
        reply_markup=json.dumps({"inline_keyboard": []}),
    )

    if data == "reg:privacy_accept":
        await _reg_step_name(client, chat_id)
    elif data == "reg:privacy_decline":
        _set_state(chat_id, None)
        await _send(
            client,
            chat_id,
            "No problem! You can use WasteWise AI without registering by logging in to an existing restaurant.\n"
            "Type *login* to see the list.",
        )
    elif data == "reg:confirm":
        await _reg_complete(client, chat_id)
    elif data == "reg:restart":
        await start_registration(client, chat_id)

    elif data == "delete:keep":
        account = auth.get_account_by_telegram(chat_id)
        primary = next(
            (
                s
                for s in (account or {}).get("sessions", [])
                if s.get("is_primary") and s.get("chat_id") == chat_id
            ),
            None,
        )
        if not primary:
            await _send(client, chat_id, "ÃƒÂ¢Ã‚ÂÃ…â€™ Only the primary account can delete.")
        else:
            _set_state(chat_id, "confirm_delete")
            await _send(
                client,
                chat_id,
                "ÃƒÂ°Ã…Â¸Ã…â€™Ã‚Â¿ *Confirm Anonymise Account*\n\n"
                "Your restaurant name, owner info, and Telegram link will be removed.\n"
                "Anonymised sales history is kept to help improve AI forecasts for other hawkers.\n\n"
                "Type *YES DELETE MY ACCOUNT* to confirm, or anything else to cancel.",
            )
    elif data == "delete:hard":
        account = auth.get_account_by_telegram(chat_id)
        primary = next(
            (
                s
                for s in (account or {}).get("sessions", [])
                if s.get("is_primary") and s.get("chat_id") == chat_id
            ),
            None,
        )
        if not primary:
            await _send(client, chat_id, "ÃƒÂ¢Ã‚ÂÃ…â€™ Only the primary account can delete.")
        else:
            _set_state(chat_id, "confirm_delete_hard")
            await _send(
                client,
                chat_id,
                "ÃƒÂ°Ã…Â¸Ã¢â‚¬â„¢Ã‚Â£ *Confirm Permanent Delete*\n\n"
                "ÃƒÂ¢Ã…Â¡Ã‚Â ÃƒÂ¯Ã‚Â¸Ã‚Â This will erase EVERYTHING ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Â account, restaurant, ALL sales history.\n"
                "This *cannot be undone*.\n\n"
                "Type *YES DELETE MY ACCOUNT* to confirm, or anything else to cancel.",
            )
    elif data == "delete:cancel":
        _set_state(chat_id, None)
        await _send(client, chat_id, "ÃƒÂ¢Ã…â€œÃ¢â‚¬Â¦ Deletion cancelled. Your account is safe. ÃƒÂ°Ã…Â¸Ã…â€™Ã‚Â¿")

    elif data.startswith("chain:create:"):
        chain_name = data[len("chain:create:") :]
        account = auth.get_account_by_telegram(chat_id)
        if not account:
            await _send(client, chat_id, "ÃƒÂ¢Ã‚ÂÃ…â€™ Please login first.")
        else:
            
            chain_id = "chain_" + str(uuid.uuid4())[:8]
            db_ch = load_database()
            db_ch.setdefault("chains", []).append(
                {
                    "chain_id": chain_id,
                    "name": chain_name,
                    "owner_email": account["email"],
                    "chain_type": "franchise",
                    "created_at": datetime.datetime.now().isoformat(),
                }
            )
            save_database(db_ch)
            await _send(
                client,
                chat_id,
                f"ÃƒÂ¢Ã…â€œÃ¢â‚¬Â¦ *Chain created: {chain_name }*\n\n"
                f"Chain ID: `{chain_id }`\n\n"
                "Now link your restaurants:\n"
                f"`add to chain {chain_id }`",
            )
    elif data == "chain:cancel":
        _set_state(chat_id, None)
        await _send(client, chat_id, "ÃƒÂ¢Ã…â€œÃ¢â‚¬Â¦ Chain creation cancelled.")

    elif data.startswith("sec:remove:"):
        sid_prefix = data[len("sec:remove:") :]
        account = auth.get_account_by_telegram(chat_id)
        if not account:
            await _send(client, chat_id, "ÃƒÂ¢Ã‚ÂÃ…â€™ Not logged in.")
        else:
            sessions = auth.get_sessions_for_account(account["email"])

            target = next((s for s in sessions if s["session_id"][:8] == sid_prefix), None)
            if not target:
                await _send(client, chat_id, "ÃƒÂ¢Ã…Â¡Ã‚Â ÃƒÂ¯Ã‚Â¸Ã‚Â Session not found or already removed.")
            elif target.get("is_primary"):
                await _send(client, chat_id, "ÃƒÂ¢Ã‚ÂÃ…â€™ Cannot remove the primary session.")
            else:
                is_primary_caller = any(
                    s.get("is_primary") and s.get("chat_id") == chat_id for s in sessions
                )
                if not is_primary_caller:
                    await _send(
                        client, chat_id, "ÃƒÂ¢Ã‚ÂÃ…â€™ Only the primary account can remove other sessions."
                    )
                else:
                    removed = auth.remove_session(account["email"], target["session_id"])
                    label = target.get("telegram_username") or target.get("label", "session")
                    if removed:
                        await _send(client, chat_id, f"ÃƒÂ¢Ã…â€œÃ¢â‚¬Â¦ Session *{label }* removed successfully.")
                    else:
                        await _send(client, chat_id, "ÃƒÂ¢Ã…Â¡Ã‚Â ÃƒÂ¯Ã‚Â¸Ã‚Â Could not remove session.")

    elif data.startswith("sec:mkprimary:"):
        target_uname = data[len("sec:mkprimary:") :]
        account = auth.get_account_by_telegram(chat_id)
        if not account:
            await _send(client, chat_id, "ÃƒÂ¢Ã‚ÂÃ…â€™ Not logged in.")
        else:
            sessions = auth.get_sessions_for_account(account["email"])
            is_primary_caller = any(
                s.get("is_primary") and s.get("chat_id") == chat_id for s in sessions
            )
            if not is_primary_caller:
                await _send(
                    client,
                    chat_id,
                    "ÃƒÂ¢Ã‚ÂÃ…â€™ Only the current primary account can transfer primary status.",
                )
            else:
                target_s = next(
                    (
                        s
                        for s in sessions
                        if s.get("telegram_username", "").lower() == target_uname.lower()
                        and s.get("type") == "telegram"
                    ),
                    None,
                )
                if not target_s:
                    await _send(
                        client, chat_id, f"ÃƒÂ¢Ã‚ÂÃ…â€™ No Telegram session found for @{target_uname }."
                    )
                elif target_s.get("is_primary"):
                    await _send(client, chat_id, f"ÃƒÂ¢Ã‚Â­Ã‚Â @{target_uname } is already the primary.")
                else:
                    db_p = load_database()
                    acc_p = next(
                        (
                            a
                            for a in db_p.get("accounts", [])
                            if a["email"].lower() == account["email"].lower()
                        ),
                        None,
                    )
                    if acc_p:
                        for s in acc_p.get("sessions", []):
                            s["is_primary"] = s["session_id"] == target_s["session_id"]
                        save_database(db_p)
                        await _send(
                            client,
                            chat_id,
                            f"ÃƒÂ¢Ã…â€œÃ¢â‚¬Â¦ *Primary transferred to @{target_uname }!*\n\nThey now hold primary status.",
                        )
                        new_chat_id = target_s.get("chat_id")
                        if new_chat_id:
                            await _send(
                                client,
                                new_chat_id,
                                f"ÃƒÂ¢Ã‚Â­Ã‚Â *You are now the PRIMARY account!*\n\n"
                                f"Account: {account ['email']}\n"
                                "You can now approve logins and manage account settings.",
                            )
    elif data.startswith("photo:"):
        intent = data.split(":")[1]
        photo_data = _get_data(chat_id).get("pending_photo_data")
        if not photo_data:
            await _send(client, chat_id, "Photo expired. Please send it again.")
            _set_state(chat_id, None)
            return
        labels = {"sales": "ÃƒÂ°Ã…Â¸Ã¢â‚¬Å“Ã…Â  Sales / Receipt", "menu": "ÃƒÂ°Ã…Â¸Ã¢â‚¬Å“Ã¢â‚¬Â¹ Menu Board"}
        await _send(client, chat_id, f"Got it ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Â processing as *{labels .get (intent ,intent )}*ÃƒÂ¢Ã¢â€šÂ¬Ã‚Â¦")
        await _process_photo(client, chat_id, photo_data, intent)
    elif data.startswith("csv:"):
        csv_type = data.split(":")[1]
        labels = {
            "sales": "ÃƒÂ°Ã…Â¸Ã¢â‚¬Å“Ã…Â  Sales Data",
            "add_menu": "ÃƒÂ¢Ã…Â¾Ã¢â‚¬Â¢ Add Menu Items",
            "replace_menu": "ÃƒÂ°Ã…Â¸Ã¢â‚¬ÂÃ¢â‚¬Å¾ Replace Full Menu",
        }
        await _send(
            client, chat_id, f"Got it ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Â processing as *{labels .get (csv_type ,csv_type )}*ÃƒÂ¢Ã¢â€šÂ¬Ã‚Â¦"
        )
        _set_state(chat_id, None)
        await _process_csv(client, chat_id, csv_type)


async def handle_document(client: httpx.AsyncClient, chat_id: int, document: dict) -> None:
    rest_id = _get_rest_id(chat_id)
    if not rest_id:
        await _send(client, chat_id, "Please login or register first.")
        return
    db = load_database()
    rest = _get_restaurant(db, rest_id)
    if not rest:
        await _send(client, chat_id, "Please login or register first.")
        return

    filename = document.get("file_name", "upload")
    file_size = document.get("file_size", 0)
    ext = os.path.splitext(filename.lower())[1]

    if ext not in (ALLOWED_DOC_EXT | ALLOWED_IMG_EXT):
        await _send(
            client,
            chat_id,
            "ÃƒÂ°Ã…Â¸Ã¢â‚¬Å“Ã‚Â Unsupported file type.\n\nI can read: *CSV, Excel (.xlsx), JPG, PNG*",
        )
        return
    if file_size > MAX_FILE_BYTES:
        await _send(
            client, chat_id, f"ÃƒÂ¢Ã‚ÂÃ…â€™ File too large ({file_size /1_000_000 :.1f} MB). Max 5 MB."
        )
        return

    await _send(client, chat_id, "ÃƒÂ°Ã…Â¸Ã¢â‚¬Å“Ã‚Â Downloading your fileÃƒÂ¢Ã¢â€šÂ¬Ã‚Â¦")
    data = await _download_file(client, document["file_id"])
    if not data:
        await _send(client, chat_id, "ÃƒÂ¢Ã…Â¡Ã‚Â ÃƒÂ¯Ã‚Â¸Ã‚Â Could not download the file. Please try again.")
        return

    ok, err = _validate_file(filename, data)
    if not ok:
        await _send(client, chat_id, f"ÃƒÂ¢Ã‚ÂÃ…â€™ {err }")
        return

    if ext in ALLOWED_IMG_EXT:
        clean = _strip_image_metadata(data)
        _set_data(chat_id, pending_photo_data=clean)
        _set_state(chat_id, "choosing_photo_type")
        kb = _inline_keyboard(
            [
                [
                    {"text": "ÃƒÂ°Ã…Â¸Ã¢â‚¬Å“Ã…Â  Sales / Receipt", "callback_data": "photo:sales"},
                    {"text": "ÃƒÂ°Ã…Â¸Ã¢â‚¬Å“Ã¢â‚¬Â¹ Menu Board / New Items", "callback_data": "photo:menu"},
                ],
            ]
        )
        await _send(
            client, chat_id, "ÃƒÂ°Ã…Â¸Ã¢â‚¬Å“Ã‚Â¸ Image file received!\n\nWhat is this a photo of?", reply_markup=kb
        )
        return

    text, fmt = process_upload(filename, data)
    if not text.strip():
        await _send(client, chat_id, "ÃƒÂ¢Ã…Â¡Ã‚Â ÃƒÂ¯Ã‚Â¸Ã‚Â Could not read data from this file. Please check it.")
        return

    _set_data(chat_id, pending_csv=text)
    _set_state(chat_id, "choosing_csv_type")
    lines = len(text.strip().splitlines())
    kb = _inline_keyboard(
        [
            [
                {"text": "ÃƒÂ°Ã…Â¸Ã¢â‚¬Å“Ã…Â  Sales Data", "callback_data": "csv:sales"},
                {"text": "ÃƒÂ¢Ã…Â¾Ã¢â‚¬Â¢ Add Menu Items", "callback_data": "csv:add_menu"},
            ],
            [{"text": "ÃƒÂ°Ã…Â¸Ã¢â‚¬ÂÃ¢â‚¬Å¾ Replace Full Menu", "callback_data": "csv:replace_menu"}],
        ]
    )
    await _send(
        client,
        chat_id,
        f"ÃƒÂ°Ã…Â¸Ã¢â‚¬Å“Ã‚Â *File received!* ({fmt .upper ()}, {lines } rows)\n\n"
        f"What type of data is this for *{rest ['name']}*?",
        reply_markup=kb,
    )


async def handle_photo(client: httpx.AsyncClient, chat_id: int, photos: list) -> None:
    rest_id = _get_rest_id(chat_id)
    if not rest_id:
        await _send(client, chat_id, "Please login or register first.")
        return

    best = max(photos, key=lambda p: p.get("file_size", 0))
    data = await _download_file(client, best["file_id"])
    if not data:
        await _send(client, chat_id, "ÃƒÂ¢Ã…Â¡Ã‚Â ÃƒÂ¯Ã‚Â¸Ã‚Â Could not download photo. Please try again.")
        return

    _set_data(chat_id, pending_photo_data=data)
    _set_state(chat_id, "choosing_photo_type")
    kb = _inline_keyboard(
        [
            [
                {"text": "ÃƒÂ°Ã…Â¸Ã¢â‚¬Å“Ã…Â  Sales / Receipt", "callback_data": "photo:sales"},
                {"text": "ÃƒÂ°Ã…Â¸Ã¢â‚¬Å“Ã¢â‚¬Â¹ Menu Board / New Items", "callback_data": "photo:menu"},
            ],
        ]
    )
    await _send(
        client,
        chat_id,
        "ÃƒÂ°Ã…Â¸Ã¢â‚¬Å“Ã‚Â¸ Photo received!\n\n"
        "What is this a photo of?\n\n"
        "ÃƒÂ¢Ã¢â€šÂ¬Ã‚Â¢ *Sales / Receipt* ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Â Your receipt, whiteboard with today's sales, or handwritten totals\n"
        "ÃƒÂ¢Ã¢â€šÂ¬Ã‚Â¢ *Menu Board / New Items* ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Â A menu board or list of dishes to add to your menu",
        reply_markup=kb,
    )


def _parse_stock_reply(text: str, menu_map: dict) -> tuple[dict, int | None]:
    """
    Parse shopkeeper reply into {canonical_item_name: qty} and optional discount %.
    Supports:
      "Pyaaz Kachori 15, Masala Chai 8"
      "Pyaaz Kachori 15 at 20%, Masala Chai 8"
      "15 Pyaaz Kachori, 8 Masala Chai"
      "Pyaaz Kachori: 15\nMasala Chai: 8"
    Returns: (parsed_dict, custom_discount_pct_or_None)
    """
    
    tl = text.lower()
    custom_pct: int | None = None

    pct_match = re.search(r'(?:at|discount|@)\s*(\d{1,2})\s*%|(\d{1,2})\s*%\s*off', tl)
    if pct_match:
        raw = pct_match.group(1) or pct_match.group(2)
        if raw:
            custom_pct = int(raw)

        text = re.sub(
            r'(?:at|discount|@)\s*\d{1,2}\s*%|\d{1,2}\s*%\s*off', '', text, flags=re.IGNORECASE
        ).strip()

    parsed: dict = {}

    for m in re.finditer(r'([A-Za-z][A-Za-z\s\'/\-]+?)\s*:?\s*(\d+)', text):
        item_raw, qty_raw = m.group(1).strip(), int(m.group(2))
        if qty_raw > 0 and len(item_raw) > 1:

            canonical = _fuzzy_match_menu(item_raw, menu_map)
            if canonical:
                parsed[canonical] = qty_raw

    for m in re.finditer(r'(\d+)\s+([A-Za-z][A-Za-z\s\'/\-]+?)(?:,|;|\n|$)', text):
        qty_raw, item_raw = int(m.group(1)), m.group(2).strip()
        if qty_raw > 0 and len(item_raw) > 1:
            canonical = _fuzzy_match_menu(item_raw, menu_map)
            if canonical and canonical not in parsed:
                parsed[canonical] = qty_raw

    return parsed, custom_pct


def _fuzzy_match_menu(name: str, menu_map: dict) -> str | None:
    """Return canonical menu item name or None."""
    key = name.lower().strip()
    if key in menu_map:
        return menu_map[key]["item"]
    for mk, mv in menu_map.items():
        if key in mk or mk in key:
            return mv["item"]
    return name if len(name) > 1 else None


async def _handle_pre_closing_reply(
    client: httpx.AsyncClient,
    chat_id: int,
    text: str,
    restaurant: dict,
    db: dict,
    today_str: str,
) -> None:
    """
    Shopkeeper replied to the 2-hr-before-close question.
    Parses stock + optional custom discount.
    Posts to marketplace. Saves pre_closing_stock for Stage 2 comparison.
    """
    tl = text.strip().lower()
    menu_map = {m["item"].lower(): m for m in restaurant.get("menu", [])}
    default_disc = restaurant.get("discount_pct", 30)
    marketplace_enabled = restaurant.get("marketplace_enabled", True)

    sold_out_words = {
        "none",
        "nothing",
        "0",
        "zero",
        "all sold",
        "habis",
        "sold out",
        "kosong",
        "tiada",
        "no stock",
        "all gone",
    }
    if tl in sold_out_words or all(w in tl for w in ["all", "sold"]):
        restaurant.pop(f"awaiting_pre_closing_inventory_{today_str }", None)
        save_database(db)
        await _send(
            client,
            chat_id,
            "ÃƒÂ¢Ã…â€œÃ¢â‚¬Â¦ *Impressive ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Â already sold out before closing!* ÃƒÂ°Ã…Â¸Ã…Â½Ã¢â‚¬Â°\n\n"
            "Zero waste today. AI is noting your perfect sell-through.\n"
            "ÃƒÂ°Ã…Â¸Ã¢â‚¬Å“Ã‹â€  _Tomorrow's forecast will stay similar or increase slightly._",
        )
        return

    parsed, custom_pct = _parse_stock_reply(text, menu_map)
    disc_pct = custom_pct if custom_pct is not None else default_disc

    if not parsed:
        await _send(
            client,
            chat_id,
            "ÃƒÂ¢Ã…Â¡Ã‚Â ÃƒÂ¯Ã‚Â¸Ã‚Â Couldn't read that. Try:\n\n"
            "_'Pyaaz Kachori 15, Masala Chai 8'_\n"
            "_'Pyaaz Kachori 15 at 20%, Masala Chai 8'_ (custom discount)\n\n"
            "Or type *none* if sold out.",
        )
        return

    closing_stock = []
    lines = []
    for item_name, qty in parsed.items():
        menu_entry = next(
            (m for m in restaurant.get("menu", []) if m["item"].lower() == item_name.lower()), None
        )
        orig = menu_entry.get("profit_margin_rm", 3.0) if menu_entry else 5.0
        disc_price = round(orig * (1 - disc_pct / 100), 2)
        closing_stock.append(
            {
                "item": item_name,
                "qty_available": qty,
                "original_price_rm": orig,
                "discounted_price_rm": disc_price,
                "discount_pct": disc_pct,
                "source": "shopkeeper",
            }
        )
        lines.append(f"ÃƒÂ¢Ã¢â€šÂ¬Ã‚Â¢ {item_name}: {qty} portions ÃƒÂ¢Ã¢â‚¬Â Ã¢â‚¬â„¢ ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¹{disc_price:.2f} ({disc_pct}% off)")

    if marketplace_enabled:
        restaurant["closing_stock"] = closing_stock
        restaurant["closing_stock_date"] = today_str
        restaurant["closing_stock_time"] = datetime.datetime.now().strftime("%H:%M")
        restaurant["pre_closing_stock"] = {s["item"]: s["qty_available"] for s in closing_stock}
        restaurant["pre_closing_discount_pct"] = disc_pct

    restaurant.pop(f"awaiting_pre_closing_inventory_{today_str }", None)
    save_database(db)

    total = sum(s["qty_available"] for s in closing_stock)
    src_note = f" (your custom {disc_pct }% set)" if custom_pct else f" (your default {disc_pct }%)"
    await _send(
        client,
        chat_id,
        f"ÃƒÂ°Ã…Â¸Ã¢â‚¬ÂºÃ‚ÂÃƒÂ¯Ã‚Â¸Ã‚Â *Marketplace Updated!*\n\n"
        f"ÃƒÂ°Ã…Â¸Ã¢â‚¬Å“Ã‚Â¦ *{total } portions* now live at {disc_pct }% off{src_note }:\n"
        + "\n".join(lines)
        + "\n\nÃƒÂ¢Ã‚ÂÃ‚Â° _At closing time, I'll ask what's actually unsold ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Â that data improves tomorrow's forecast!_",
    )


async def _handle_post_closing_reply(
    client: httpx.AsyncClient,
    chat_id: int,
    text: str,
    restaurant: dict,
    db: dict,
    today_str: str,
) -> None:
    """
    Shopkeeper replied after closing: how much is actually unsold.
    Records: full-price sold, discount sold, zero-profit waste.
    Saves to daily_records for AI learning.
    """
    from services.inventory import record_post_closing_learning, format_post_closing_telegram

    tl = text.strip().lower()
    menu_map = {m["item"].lower(): m for m in restaurant.get("menu", [])}
    pre_stock = restaurant.get("pre_closing_stock", {})

    sold_out_words = {
        "none",
        "nothing",
        "0",
        "zero",
        "all sold",
        "habis",
        "sold out",
        "kosong",
        "tiada",
        "no stock",
        "all gone",
    }
    if tl in sold_out_words or all(w in tl for w in ["all", "sold"]):
        leftover = {item: 0 for item in pre_stock}
    else:
        parsed, _ = _parse_stock_reply(text, menu_map)
        if not parsed and pre_stock:
            await _send(
                client,
                chat_id,
                "ÃƒÂ¢Ã…Â¡Ã‚Â ÃƒÂ¯Ã‚Â¸Ã‚Â Couldn't read that. Reply like:\n_'Pyaaz Kachori 3, Masala Chai 0'_\n\nOr 'none' if everything was sold!",
            )
            return
        leftover = {item: parsed.get(item, parsed.get(item.lower(), 0)) for item in pre_stock}

        for item_name, qty in parsed.items():
            if item_name not in leftover:
                leftover[item_name] = qty

    analysis = record_post_closing_learning(restaurant, leftover)
    restaurant.pop(f"awaiting_post_closing_inventory_{today_str }", None)
    save_database(db)

    report = format_post_closing_telegram(restaurant, analysis)
    await _send(client, chat_id, report)


async def handle_text(
    client: httpx.AsyncClient, chat_id: int, text: str, username: str = ""
) -> None:

    state = _get_state(chat_id)
    tl = text.strip().lower()

    if state == "reg_name":
        if len(text.strip()) < 2:
            await _send(
                client, chat_id, "Restaurant name must be at least 2 characters. Try again:"
            )
            return
        _set_data(chat_id, reg_name=text.strip())
        await _reg_step_owner(client, chat_id)
        return

    if state == "reg_owner":
        _set_data(chat_id, reg_owner=text.strip())
        await _reg_step_type(client, chat_id)
        return

    if state == "reg_type":
        _set_data(chat_id, reg_type=text.strip())
        await _reg_step_region(client, chat_id)
        return

    if state == "reg_region":
        if len(text.strip()) < 3:
            await _send(client, chat_id, "Please enter a valid area or city name:")
            return
        _set_data(chat_id, reg_region=text.strip())
        await _reg_step_closing_time(client, chat_id)
        return

    if state == "reg_closing_time":

        
        match = re.search(r'(\d{1,2}:\d{2})', text.strip())
        if not match:
            await _send(client, chat_id, "Please enter a valid time (e.g. *21:00* or *9:30 PM*).")
            return
        closing_time = match.group(1).zfill(5)
        _set_data(chat_id, reg_closing_time=closing_time)
        await _reg_confirm(client, chat_id)
        return

    if state == "choosing_photo_type":
        if any(w in tl for w in ("sales", "receipt", "sold", "whiteboard", "jualan")):
            photo_data = _get_data(chat_id).get("pending_photo_data")
            if photo_data:
                await _process_photo(client, chat_id, photo_data, "sales")
            else:
                await _send(client, chat_id, "Photo expired. Please send it again.")
                _set_state(chat_id, None)
        elif any(w in tl for w in ("inventory", "stok", "shelf", "stock", "ingredients", "scan")):
            photo_data = _get_data(chat_id).get("pending_photo_data")
            if photo_data:

                rest_id = _get_rest_id(chat_id)
                db = load_database()
                rest = _get_restaurant(db, rest_id) if rest_id else None
                if rest and photo_data:
                    await _typing(client, chat_id)
                    try:
                        from services.computer_vision_inventory import scan_inventory_from_image

                        import asyncio
                        result = await asyncio.to_thread(scan_inventory_from_image, photo_data, rest)
                        ingr = result.get("detected_ingredients", [])
                        summary = result.get("summary", "")
                        lines = ["ÃƒÂ°Ã…Â¸Ã¢â‚¬Å“Ã‚Â¦ *CV Inventory Scan Result*\n"]
                        if ingr:
                            for ing in ingr:
                                name = ing.get("name") or ing.get("ingredient") or str(ing)
                                qty = (
                                    f" ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Â {ing ['quantity']} {ing .get ('unit','')}"
                                    if isinstance(ing, dict) and ing.get("quantity")
                                    else ""
                                )
                                lines.append(f"ÃƒÂ¢Ã¢â€šÂ¬Ã‚Â¢ {name }{qty }")
                        else:
                            lines.append("No ingredients detected. Try a clearer photo.")
                        if summary:
                            lines.append(f"\nÃƒÂ°Ã…Â¸Ã¢â‚¬â„¢Ã‚Â¡ {summary }")
                        await _send(client, chat_id, "\n".join(lines))
                    except Exception as e:
                        await _send(client, chat_id, f"ÃƒÂ¢Ã‚ÂÃ…â€™ Scan error: {e }")
                else:
                    await _send(client, chat_id, "Please login first, then send the photo again.")
                _set_state(chat_id, None)
            else:
                await _send(client, chat_id, "Photo expired. Please send it again.")
                _set_state(chat_id, None)
        elif any(w in tl for w in ("menu", "add", "board", "chalk", "new item")):
            photo_data = _get_data(chat_id).get("pending_photo_data")
            if photo_data:
                await _process_photo(client, chat_id, photo_data, "menu")
            else:
                await _send(client, chat_id, "Photo expired. Please send it again.")
                _set_state(chat_id, None)
        else:
            kb = _inline_keyboard(
                [
                    [
                        {"text": "ÃƒÂ°Ã…Â¸Ã¢â‚¬Å“Ã…Â  Sales / Receipt", "callback_data": "photo:sales"},
                        {"text": "ÃƒÂ°Ã…Â¸Ã¢â‚¬Å“Ã¢â‚¬Â¹ Menu Board / New Items", "callback_data": "photo:menu"},
                    ],
                    [{"text": "ÃƒÂ°Ã…Â¸Ã¢â‚¬Å“Ã‚Â¦ Inventory Scan (CV AI)", "callback_data": "photo:inventory"}],
                ]
            )
            await _send(
                client,
                chat_id,
                "Please tap one of the buttons to tell me what type of photo this is.",
                reply_markup=kb,
            )
        return

    if state == "choosing_csv_type":
        if any(w in tl for w in ("sales", "sold", "sell", "jualan")):
            await _process_csv(client, chat_id, "sales")
        elif any(w in tl for w in ("add", "new", "tambah", "append")):
            await _process_csv(client, chat_id, "add_menu")
        elif any(w in tl for w in ("replace", "overwrite", "full", "ganti")):
            await _process_csv(client, chat_id, "replace_menu")
        else:
            kb = _inline_keyboard(
                [
                    [
                        {"text": "ÃƒÂ°Ã…Â¸Ã¢â‚¬Å“Ã…Â  Sales Data", "callback_data": "csv:sales"},
                        {"text": "ÃƒÂ¢Ã…Â¾Ã¢â‚¬Â¢ Add Menu Items", "callback_data": "csv:add_menu"},
                    ],
                    [{"text": "ÃƒÂ°Ã…Â¸Ã¢â‚¬ÂÃ¢â‚¬Å¾ Replace Full Menu", "callback_data": "csv:replace_menu"}],
                ]
            )
            await _send(client, chat_id, "Please choose the data type:", reply_markup=kb)
        return

    if state == "bot_login_email":
        await _handle_bot_login_email(client, chat_id, text.strip())
        return

    if state == "bot_login_otp":
        email = _get_data(chat_id).get("login_email", "")
        if auth.verify_otp(email, text.strip(), "bot_login"):
            account = auth.get_account_by_email(email)
            if account:
                _set_data(chat_id, restaurant_id=account["restaurant_id"])
                _set_state(chat_id, None)
                await _send(
                    client,
                    chat_id,
                    f"ÃƒÂ¢Ã…â€œÃ¢â‚¬Â¦ Logged in as *{email }*!\n\n" "Say 'forecast' to get today's numbers.",
                    reply_markup={"remove_keyboard": True},
                )
                await _typing(client, chat_id)
                forecast = await asyncio.to_thread(_do_generate_forecast, account["restaurant_id"])
                await _send(client, chat_id, forecast)
            else:
                _set_state(chat_id, None)
                await _send(client, chat_id, "Something went wrong. Please try again.")
        else:
            await _send(client, chat_id, "ÃƒÂ¢Ã‚ÂÃ…â€™ Wrong or expired OTP. Type *login* to try again.")
            _set_state(chat_id, None)
        return

    if state == "bot_awaiting_approval":
        await _send(
            client,
            chat_id,
            "ÃƒÂ¢Ã‚ÂÃ‚Â³ Waiting for approval from your primary account. "
            "They will see a message to approve or deny your login.",
        )
        return

    if state and state.startswith("bom_item:"):
        item_name = state.split(":", 1)[1]
        rest_id = _get_rest_id(chat_id)
        db2 = load_database()
        rest2 = _get_restaurant(db2, rest_id) if rest_id else None
        if rest2:
            region = rest2.get("region", "India")
            rest_type = rest2.get("type", "hawker")
            bom = ask_bom_conversational(item_name, region, rest_type, text.strip())
            if bom:
                rest2.setdefault("bom", {})[item_name] = bom
                save_database(db2)
                cost = bom.get("cost_inr", bom.get("cost_rm", "?"))
                ingr = [f"{k}: {v}" for k, v in bom.items() if k not in ("cost_rm", "cost_inr")]
                await _send(
                    client,
                    chat_id,
                    f"ÃƒÂ¢Ã…â€œÃ¢â‚¬Â¦ Ingredients saved for *{item_name}*:\n"
                    + "\n".join(f"  ÃƒÂ¢Ã¢â€šÂ¬Ã‚Â¢ {i}" for i in ingr)
                    + f"\n  ÃƒÂ¢Ã¢â€šÂ¬Ã‚Â¢ Cost: ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¹{cost} per serving\n\n"
                    "Your shopping list will now be accurate for this item.",
                )
            else:
                await _send(
                    client,
                    chat_id,
                    "ÃƒÂ¢Ã…Â¡Ã‚Â ÃƒÂ¯Ã‚Â¸Ã‚Â Could not parse ingredients. Try: _'200g rice, 50ml coconut milk, 1 egg'_",
                )
        _set_state(chat_id, None)
        return

    if state == "choosing_restaurant":
        db = load_database()
        rest_id = next(
            (
                r["id"]
                for r in db.get("restaurants", [])
                if r["name"].lower() in text.lower() or r["id"] in text
            ),
            None,
        )
        if text.strip().lower() in ("register", "ÃƒÂ¢Ã…Â¾Ã¢â‚¬Â¢ register my restaurant"):
            await start_registration(client, chat_id)
            return
        if not rest_id:
            await _send(
                client,
                chat_id,
                "Could not find that restaurant. Please pick from the list or tap *ÃƒÂ¢Ã…Â¾Ã¢â‚¬Â¢ Register my restaurant*.",
                reply_markup=_rest_keyboard(),
            )
            return
        _set_data(chat_id, restaurant_id=rest_id)
        db_rest = _get_restaurant(db, rest_id)
        if db_rest and not db_rest.get("telegram_chat_id"):
            db_rest["telegram_chat_id"] = chat_id
            save_database(db)
        _set_state(chat_id, None)
        name = db_rest["name"] if db_rest else rest_id
        await _send(
            client,
            chat_id,
            f"ÃƒÂ¢Ã…â€œÃ¢â‚¬Â¦ Logged in as *{name }*!\n\nSay 'forecast' to get today's numbers.",
            reply_markup={"remove_keyboard": True},
        )
        await _typing(client, chat_id)
        forecast = await asyncio.to_thread(_do_generate_forecast, rest_id)
        await _send(client, chat_id, forecast)
        return

    if "ingredients for" in tl or "recipe for" in tl or ("contain" in tl and "g" in tl):
        rest_id = _get_rest_id(chat_id)
        if rest_id:
            db = load_database()
            rest = _get_restaurant(db, rest_id)
            if rest:
                for item in rest.get("menu", []):
                    if item["item"].lower() in tl:
                        remainder = text
                        for sep in [":", "-", "is", "contains", "has"]:
                            if sep in remainder.lower():
                                idx = remainder.lower().index(sep)
                                remainder = remainder[idx + len(sep) :].strip()
                                break
                        await _set_bom_for_item(client, chat_id, item["item"], remainder)
                        return

    rest_id_check = _get_rest_id(chat_id)
    if rest_id_check:
        db_check = load_database()
        rest_check = _get_restaurant(db_check, rest_id_check)
        if rest_check:
            today_str_check = get_today(rest_check).isoformat()
            if rest_check.get(f"awaiting_pre_closing_inventory_{today_str_check }"):
                await _handle_pre_closing_reply(
                    client, chat_id, text, rest_check, db_check, today_str_check
                )
                return

            if rest_check.get(f"awaiting_post_closing_inventory_{today_str_check }"):
                await _handle_post_closing_reply(
                    client, chat_id, text, rest_check, db_check, today_str_check
                )
                return

    _order_cmd_map = {
        "accept": "accepted",
        "reject": "rejected",
        "done": "completed",
        "collected": "completed",
        "miss": "missed",
        "missed": "missed",
    }
    tl_parts = tl.strip().split()
    if len(tl_parts) >= 2 and tl_parts[0].lstrip("/") in _order_cmd_map:
        cmd = tl_parts[0].lstrip("/")
        order_ref = tl_parts[1].lower().lstrip("#")
        new_status = _order_cmd_map[cmd]
        rest_id = _get_rest_id(chat_id)
        if rest_id:
            db_ord = load_database()
            rest_ord = _get_restaurant(db_ord, rest_id)
            matched_order = None
            today_str_ord = get_today(rest_ord).isoformat()
            if rest_ord:
                for o in rest_ord.get("marketplace_orders", []):
                    oid = o.get("order_id", "").lower()

                    match_num = (
                        o.get("date") == today_str_ord and str(o.get("order_num", "")) == order_ref
                    )
                    match_id = oid == order_ref or oid.endswith(order_ref) or order_ref in oid
                    if match_num or match_id:
                        if new_status == "accepted" and o.get("status") != "pending":
                            continue
                        if new_status == "completed" and o.get("status") not in ("pending", "accepted"):
                            continue
                        if new_status == "rejected" and o.get("status") != "pending":
                            continue
                        if new_status == "missed" and o.get("status") not in ("pending", "accepted"):
                            continue
                        matched_order = o
                        break
            if matched_order:
                if new_status == "rejected":
                    matched_order["status"] = "cancelled"
                    matched_order["cancel_reason"] = "Shopkeeper rejected"
                elif new_status == "missed":
                    matched_order["status"] = "cancelled"
                    matched_order["cancel_reason"] = "Customer didn't pick up"
                else:
                    matched_order["status"] = new_status
                
                matched_order["updated_at"] = datetime.datetime.now().isoformat()
                matched_order["updated_by"] = "telegram"
                save_database(db_ord)
                order_label = (
                    f"Order #{matched_order .get ('order_num',matched_order ['order_id'])}"
                )
                if new_status == "completed":
                    await _send(
                        client,
                        chat_id,
                        f"ÃƒÂ¢Ã…â€œÃ¢â‚¬Â¦ *{order_label } confirmed as collected!*\n\n"
                        f"Customer: *{matched_order .get ('customer_name','Customer')}*\n"
                        f"ÃƒÂ°Ã…Â¸Ã¢â‚¬â„¢Ã‚Â° Revenue added.",
                    )
                elif new_status == "accepted":
                    await _send(
                        client,
                        chat_id,
                        f"ÃƒÂ¢Ã…â€œÃ¢â‚¬Â¦ *{order_label} accepted!*\n\n"
                        f"Customer: *{matched_order.get('customer_name','Customer')}*\n"
                        f"Awaiting pickup.",
                    )
                elif new_status == "rejected":
                    await _send(
                        client,
                        chat_id,
                        f"ÃƒÂ¢Ã‚ÂÃ…â€™ *{order_label} rejected.*\n\n"
                        f"Customer: *{matched_order.get('customer_name','Customer')}*\n"
                        f"Order cancelled.",
                    )
                else:
                    await _send(
                        client,
                        chat_id,
                        f"ÃƒÂ¢Ã‚ÂÃ…â€™ *{order_label } marked as missed.*\n\n"
                        f"Customer: *{matched_order .get ('customer_name','Customer')}*\n"
                        f"ÃƒÂ°Ã…Â¸Ã¢â‚¬Å“Ã‚Â¦ Inventory released. No-show recorded for AI learning.",
                    )
            else:
                await _send(
                    client,
                    chat_id,
                    f"ÃƒÂ¢Ã…Â¡Ã‚Â ÃƒÂ¯Ã‚Â¸Ã‚Â Order `{order_ref }` not found for today. "
                    "Check the order number and try again.",
                )
            return

    await handle_natural_language(client, chat_id, text, username=username)


async def handle_natural_language(
    client: httpx.AsyncClient, chat_id: int, text: str, username: str = ""
) -> None:

    linked_account = auth.get_account_by_telegram(chat_id)
    if linked_account and not _get_rest_id(chat_id):
        rest_id = linked_account.get("restaurant_id")
        if rest_id:
            _set_data(chat_id, restaurant_id=rest_id)

    rest_id = _get_rest_id(chat_id)
    if not rest_id:
        tl = text.strip().lower()

        if username:
            db_check = load_database()
            now_str = datetime.datetime.utcnow().isoformat()
            pending = next(
                (
                    pr
                    for pr in db_check.get("pending_registrations", [])
                    if pr.get("telegram_username", "").lower() == username.lower().lstrip("@")
                    and pr.get("expires_at", "") > now_str
                ),
                None,
            )
            if pending:
                rest_data = pending.get("restaurant_data", {})
                new_rest = rest_data.get("new_rest", {})
                region = rest_data.get("region", "")
                new_rest["telegram_chat_id"] = chat_id
                new_rest["telegram_username"] = username.lstrip("@")
                email = pending.get("email", "")
                db2 = load_database()
                db2.setdefault("restaurants", []).append(new_rest)
                if region and region not in db2.get("regions", {}):
                    db2.setdefault("regions", {})[region] = {
                        "type": "General Area",
                        "foot_traffic_baseline": 500,
                        "weekend_multiplier": 1.1,
                        "holiday_multiplier": 1.0,
                        "rain_impact": -0.2,
                    }
                db2["pending_registrations"] = [
                    p for p in db2.get("pending_registrations", []) if p.get("email") != email
                ]
                save_database(db2)
                try:
                    
                    auth.create_account(email, new_rest["id"], chat_id, username.lstrip("@"))
                    _set_data(chat_id, restaurant_id=new_rest["id"])
                    await _send(
                        client,
                        chat_id,
                        f"ÃƒÂ¢Ã…â€œÃ¢â‚¬Â¦ *Welcome to WasteWise AI!*\n\n"
                        f"Your account for *{new_rest .get ('name','your restaurant')}* is now active.\n"
                        "Your web dashboard will update automatically.\n\n"
                        "Just talk to me naturally ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Â tell me your sales, ask for a forecast, or add your menu.",
                    )
                    return
                except Exception as e:
                    await _send(client, chat_id, f"ÃƒÂ¢Ã…Â¡Ã‚Â ÃƒÂ¯Ã‚Â¸Ã‚Â Could not complete registration: {e }")
                    return

        if any(w in tl for w in ("login", "log in", "sign in")):
            await _start_bot_login(client, chat_id)
            return

        if username:
            pending = auth.get_demo_pending_link(username)
            if pending:
                auth.link_demo_telegram(username, chat_id)
                auth.complete_demo_pending_link(username)
                otp = auth.create_demo_otp(chat_id)
                await _send(
                    client,
                    chat_id,
                    f"ÃƒÂ°Ã…Â¸Ã…Â½Ã‚Â® *WasteWise Demo*\n\n"
                    f"Your Telegram @{username} is now connected.\n\n"
                    f"*Your one-time code:*\n`{otp}`\n\n"
                    f"ÃƒÂ¢Ã‚Â¬Ã¢â‚¬Â¦ÃƒÂ¯Ã‚Â¸Ã‚Â Go back to the website and enter this code.\n_(Valid for 2 minutes)_",
                )
                return

            auth.get_demo_chat_id_by_username(username)
            # If they are already linked, do nothing here. Let them fall through
            # to natural language processing so they can use the bot.

        await _send(
            client,
            chat_id,
            "ÃƒÂ°Ã…Â¸Ã¢â‚¬ËœÃ¢â‚¬Â¹ Welcome to *WasteWise AI*!\n\n"
            "I help indian restaurants reduce food waste through AI forecasting.\n\n"
            "ÃƒÂ¢Ã¢â€šÂ¬Ã‚Â¢ Type *register* to set up your restaurant\n"
            "ÃƒÂ¢Ã¢â€šÂ¬Ã‚Â¢ Type *login* to sign in with your email",
        )
        return

    db = load_database()
    rest = _get_restaurant(db, rest_id)
    if not rest:
        await _send(client, chat_id, "Please type *login* to reconnect.")
        return

    tl = text.strip().lower()

    # Fast typo-tolerant greeting check ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Â no AI, no classifier, <1ms
    if _is_greeting(tl):
        now = datetime.datetime.now().strftime("%d %b %Y, %I:%M %p")
        await _send(
            client,
            chat_id,
            f"ÃƒÂ°Ã…Â¸Ã¢â‚¬ËœÃ¢â‚¬Â¹ Hey! WasteWise AI here, managing *{rest['name']}*.\n\n"
            "Just talk to me naturally! For example:\n\n"
            "ÃƒÂ¢Ã¢â€šÂ¬Ã‚Â¢ *Forecast* ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Â _'What should I prepare today?'_\n"
            "ÃƒÂ¢Ã¢â€šÂ¬Ã‚Â¢ *Sales* ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Â _'Sold 95 Pyaaz Kachori today'_\n"
            "ÃƒÂ¢Ã¢â€šÂ¬Ã‚Â¢ *Events* ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Â _'Tomorrow wedding with 300 guests'_\n"
            "ÃƒÂ¢Ã¢â€šÂ¬Ã‚Â¢ *Menu* ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Â _'Add Milo Ais to menu'_ or _'Show my menu'_\n"
            "ÃƒÂ¢Ã¢â€šÂ¬Ã‚Â¢ *Ingredients* ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Â _'Set ingredients for Pyaaz Kachori: 200g rice, 50ml coconut milk'_\n"
            "ÃƒÂ¢Ã¢â€šÂ¬Ã‚Â¢ *Photo* ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Â Send a photo of your receipt or whiteboard\n"
            "ÃƒÂ¢Ã¢â€šÂ¬Ã‚Â¢ *File* ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Â Send a CSV or Excel file\n"
            "ÃƒÂ¢Ã¢â€šÂ¬Ã‚Â¢ *Sessions* ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Â _'Who is logged in?'_ or `security`\n"
            "ÃƒÂ¢Ã¢â€šÂ¬Ã‚Â¢ *Orders* ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Â _'done 5'_ or _'miss 3'_ to confirm/miss orders\n"
            "ÃƒÂ¢Ã¢â€šÂ¬Ã‚Â¢ *Chain* ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Â _'create chain'_ or _'show my chains'_\n\n"
            f"ÃƒÂ°Ã…Â¸Ã¢â‚¬Å“Ã¢â‚¬Â¦ {now}",
        )
        return




    # -----------------------------------------------------------------------
    # Smart intent classification ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Â local model first (~5ms), AI fallback
    # only when confidence is low. No hardcoded keywords, no long API waits.
    # -----------------------------------------------------------------------
    import asyncio

    local_intent, confidence = await asyncio.to_thread(_classify_intent_local, text)
    print(f"[LocalCLF] '{text[:40]}' ÃƒÂ¢Ã¢â‚¬Â Ã¢â‚¬â„¢ {local_intent} ({confidence:.2f})")

    # High-confidence local intents we can handle instantly without AI
    if confidence >= 0.60 and local_intent == "menu_show":
        menu = rest.get("menu", [])
        if not menu:
            await _send(client, chat_id, "ÃƒÂ°Ã…Â¸Ã¢â‚¬Å“Ã¢â‚¬Â¹ Your menu is empty. Send me something like _'Add Samosa Chaat to menu'_ to get started.")
        else:
            lines_m = [f"ÃƒÂ°Ã…Â¸Ã¢â‚¬Å“Ã¢â‚¬Â¹ *Menu ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Â {rest['name']}*\n"]
            for m in menu:
                lines_m.append(
                    f"ÃƒÂ¢Ã¢â€šÂ¬Ã‚Â¢ {m['item']} ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Â ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¹{m.get('profit_margin_rm', 0):.0f} margin"
                    f" | {m.get('base_daily_demand', 0)} portions/day"
                )
            await _send(client, chat_id, "\n".join(lines_m))
        return

    if confidence >= 0.60 and local_intent == "forecast":
        from services.cache import get_forecast_cache
        today_str_fp = get_today(rest).isoformat()
        cached_fc = get_forecast_cache(rest_id, today_str_fp)
        if cached_fc:
            await _send(client, chat_id, cached_fc)
        else:
            await _send(client, chat_id, "ÃƒÂ°Ã…Â¸Ã¢â‚¬ÂÃ¢â‚¬Å¾ Generating your forecastÃƒÂ¢Ã¢â€šÂ¬Ã‚Â¦ please wait a moment.")
            forecast_fp = await asyncio.to_thread(_do_generate_forecast, rest_id)
            await _send(client, chat_id, forecast_fp)
        return

    if confidence >= 0.60 and local_intent == "greeting":
        now = datetime.datetime.now().strftime("%d %b %Y, %I:%M %p")
        await _send(
            client,
            chat_id,
            f"ÃƒÂ°Ã…Â¸Ã¢â‚¬ËœÃ¢â‚¬Â¹ Hey! WasteWise AI here, managing *{rest['name']}*.\n\n"
            "Just talk to me naturally! For example:\n\n"
            "ÃƒÂ¢Ã¢â€šÂ¬Ã‚Â¢ *Forecast* ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Â _'What should I prepare today?'_\n"
            "ÃƒÂ¢Ã¢â€šÂ¬Ã‚Â¢ *Sales* ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Â _'Sold 95 Pyaaz Kachori today'_\n"
            "ÃƒÂ¢Ã¢â€šÂ¬Ã‚Â¢ *Events* ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Â _'Tomorrow wedding with 300 guests'_\n"
            "ÃƒÂ¢Ã¢â€šÂ¬Ã‚Â¢ *Menu* ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Â _'Add item to menu'_ or _'Show my menu'_\n"
            f"ÃƒÂ°Ã…Â¸Ã¢â‚¬Å“Ã¢â‚¬Â¦ {now}",
        )
        return

    if confidence >= 0.60 and local_intent == "inventory":
        from services.inventory import compute_remaining_inventory
        remaining = compute_remaining_inventory(rest)
        lines = [f"ÃƒÂ°Ã…Â¸Ã¢â‚¬Å“Ã‚Â¦ *Remaining Inventory ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Â {rest['name']}*\n"]
        total_remaining = 0
        discount_pct = rest.get("discount_pct", 30)
        for item in remaining:
            if item["remaining"] > 0:
                disc = round(item['profit_margin_rm'] * (1 - discount_pct / 100), 2)
                lines.append(f"ÃƒÂ¢Ã¢â€šÂ¬Ã‚Â¢ {item['item']}: *{item['remaining']}* portions (ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¹{disc:.0f} at {discount_pct}% off)")
                total_remaining += item["remaining"]
        if total_remaining == 0:
            lines.append("ÃƒÂ¢Ã…â€œÃ¢â‚¬Â¦ All sold out ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Â great day!")
        else:
            lines.append(f"\nTotal: *{total_remaining}* portions at {discount_pct}% discount")
        await _send(client, chat_id, "\n".join(lines))
        return

    if confidence >= 0.60 and local_intent == "orders":
        today_str2 = get_today(rest).isoformat()
        orders2 = [o for o in rest.get("marketplace_orders", []) if o.get("date") == today_str2]
        if not orders2:
            await _send(client, chat_id, f"ÃƒÂ°Ã…Â¸Ã¢â‚¬Å“Ã¢â‚¬Â¹ No marketplace orders for today yet.")
        else:
            revenue2 = sum(o.get("total_rm", 0) for o in orders2 if o.get("status") != "cancelled")
            lines2 = [f"ÃƒÂ°Ã…Â¸Ã¢â‚¬ÂºÃ‚ÂÃƒÂ¯Ã‚Â¸Ã‚Â *Today's Orders ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Â {rest['name']}*\n"]
            for o in orders2[-10:]:
                status_emoji = {"pending": "ÃƒÂ¢Ã‚ÂÃ‚Â³", "completed": "ÃƒÂ¢Ã…â€œÃ¢â‚¬Â¦", "cancelled": "ÃƒÂ¢Ã‚ÂÃ…â€™"}.get(o.get("status", ""), "ÃƒÂ¢Ã‚ÂÃ¢â‚¬Å“")
                items_str = ", ".join(f"{oi.get('qty', 1)}x{oi.get('item', '')}" for oi in o.get("items", []))
                lines2.append(f"{status_emoji} {o.get('customer_name', 'Guest')} ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Â {items_str} (ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¹{o.get('total_rm', 0):.0f})")
            lines2.append(f"\nÃƒÂ°Ã…Â¸Ã¢â‚¬â„¢Ã‚Â° Revenue: *ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¹{revenue2:.0f}* | Your share: *ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¹{revenue2*0.90:.0f}*")
            await _send(client, chat_id, "\n".join(lines2))
        return

    if confidence >= 0.60 and local_intent == "profit":
        from services.inventory import get_today_profit_summary
        s = get_today_profit_summary(rest)
        lines3 = [
            f"ÃƒÂ°Ã…Â¸Ã¢â‚¬â„¢Ã‚Â° *Today's Sales ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Â {rest['name']}*\n",
            f"ÃƒÂ°Ã…Â¸Ã¢â‚¬Å“Ã…Â  Regular sales: ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¹{s['regular_sales_rm']:.0f}",
            f"ÃƒÂ°Ã…Â¸Ã¢â‚¬ÂºÃ‚ÂÃƒÂ¯Ã‚Â¸Ã‚Â Marketplace: ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¹{s['marketplace_revenue_rm']:.0f}",
            f"ÃƒÂ°Ã…Â¸Ã‚ÂÃ‚Â¦ Your earnings: *ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¹{s['shopkeeper_earnings_rm']:.0f}*",
            f"ÃƒÂ°Ã…Â¸Ã¢â‚¬Å“Ã‚Â± Platform fee: ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¹{s['platform_fee_rm']:.0f}",
            f"ÃƒÂ°Ã…Â¸Ã¢â‚¬Å“Ã‚Â¦ Orders: {s['total_orders']}",
        ]
        await _send(client, chat_id, "\n".join(lines3))
        return

    if confidence >= 0.60 and local_intent == "causal_analysis":
        await _send(client, chat_id, "ÃƒÂ°Ã…Â¸Ã¢â‚¬ÂÃ¢â‚¬Å¾ Running causal AI analysis on your sales dataÃƒÂ¢Ã¢â€šÂ¬Ã‚Â¦")
        if "today" in text.lower():
            target_date = get_today(rest).isoformat()
        else:
            target_date = get_yesterday(rest).isoformat()
        try:
            from services.causal_ai import format_causal_report_telegram
            report = await asyncio.to_thread(format_causal_report_telegram, rest, target_date)
            await _send(client, chat_id, report)
        except Exception as e:
            await _send(client, chat_id, f"ÃƒÂ¢Ã‚ÂÃ…â€™ Causal analysis error: {e}")
        return

    if confidence >= 0.60 and local_intent == "menu_engineering":
        if not rest.get("menu"):
            await _send(client, chat_id, "ÃƒÂ¢Ã‚ÂÃ…â€™ Add menu items first before running menu analysis.")
            return
        await _send(client, chat_id, "ÃƒÂ°Ã…Â¸Ã¢â‚¬ÂÃ¢â‚¬Å¾ Generating menu engineering matrixÃƒÂ¢Ã¢â€šÂ¬Ã‚Â¦")
        try:
            from services.menu_engineering import classify_menu_items, generate_menu_recommendations
            classification = await asyncio.to_thread(classify_menu_items, rest)
            recommendations = await asyncio.to_thread(generate_menu_recommendations, rest)
            emoji_map = {"star": "ÃƒÂ¢Ã‚Â­Ã‚Â", "ploughhorse": "ÃƒÂ°Ã…Â¸Ã‚ÂÃ‚Â´", "puzzle": "ÃƒÂ¢Ã‚ÂÃ¢â‚¬Å“", "dog": "ÃƒÂ°Ã…Â¸Ã‚ÂÃ‚Â¶"}
            lines = [f"ÃƒÂ°Ã…Â¸Ã‚Â§Ã‚Â  *Menu Engineering ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Â {rest['name']}*\n"]
            for item, cat in classification.items():
                lines.append(f"{emoji_map.get(cat, '')} {item} ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Â _{cat.capitalize()}_")
            lines.append(f"\nÃƒÂ°Ã…Â¸Ã¢â‚¬â„¢Ã‚Â¡ *AI Recommendation:*\n{recommendations}")
            await _send(client, chat_id, "\n".join(lines))
        except Exception as e:
            await _send(client, chat_id, f"ÃƒÂ¢Ã‚ÂÃ…â€™ Menu engineering error: {e}")
        return

    if confidence >= 0.60 and local_intent == "security":
        await _handle_security_menu(client, chat_id)
        return

    if confidence >= 0.60 and local_intent == "help":
        await _send(
            client,
            chat_id,
            "ÃƒÂ¢Ã¢â‚¬Å¾Ã‚Â¹ÃƒÂ¯Ã‚Â¸Ã‚Â *WasteWise Commands*\n\n"
            "ÃƒÂ¢Ã¢â€šÂ¬Ã‚Â¢ Show menu: _'what's on my menu'_\n"
            "ÃƒÂ¢Ã¢â€šÂ¬Ã‚Â¢ Add item: _'add samosa to menu'_\n"
            "ÃƒÂ¢Ã¢â€šÂ¬Ã‚Â¢ Forecast: _'what should I prepare'_\n"
            "ÃƒÂ¢Ã¢â€šÂ¬Ã‚Â¢ Sales: _'sold 50 samosas'_\n"
            "ÃƒÂ¢Ã¢â€šÂ¬Ã‚Â¢ Event: _'tomorrow is a wedding'_\n"
            "ÃƒÂ¢Ã¢â€šÂ¬Ã‚Â¢ Analysis: _'why did my sales drop'_\n"
            "ÃƒÂ¢Ã¢â€šÂ¬Ã‚Â¢ Performance: _'which items perform best'_\n"
            "ÃƒÂ¢Ã¢â€šÂ¬Ã‚Â¢ Inventory: _'how much stock is left'_\n"
            "ÃƒÂ¢Ã¢â€šÂ¬Ã‚Â¢ Profit: _'how much did I earn today'_\n"
            "ÃƒÂ¢Ã¢â€šÂ¬Ã‚Â¢ Orders: _'show my orders'_\n"
            "ÃƒÂ¢Ã¢â€šÂ¬Ã‚Â¢ Security: _'who is logged in'_"
        )
        return

    # For everything else, use AI detect_intent (handles nuanced/multi-intent)
    # But skip _typing if local model already gave us a confident answer we escalate
    await _typing(client, chat_id)
    intent_list = await asyncio.to_thread(detect_intent, text, rest)
    if not isinstance(intent_list, list):
        intent_list = [intent_list]

    side_replies = []
    remaining_intents = []
    for _idata in intent_list:
        _intent = _idata.get("intent", "general")

        if _intent == "order_accept":
            _oid = (_idata.get("order_id") or "").lower().strip()
            _matched = None
            _db_oc = load_database()
            _rest_oc = _get_restaurant(_db_oc, rest_id)
            if _rest_oc:
                for _o2 in _rest_oc.get("marketplace_orders", []):
                    _oid_db = _o2.get("order_id", "").lower()
                    if _oid_db == _oid or _oid_db.endswith(_oid) or _oid in _oid_db:
                        if _o2.get("status") == "pending":
                            _o2["status"] = "accepted"
                            _o2["updated_at"] = datetime.datetime.now().isoformat()
                            _o2["updated_by"] = "nlp"
                            _matched = _o2
                        break
                if _matched:
                    
                    side_replies.append(f"\u2705 Order `{_matched['order_id']}` accepted. Awaiting pickup.")
                else:
                    side_replies.append(f"\u26a0\ufe0f Order `{_oid or '?'}` not found or not pending.")

        elif _intent == "order_reject":
            _oid = (_idata.get("order_id") or "").lower().strip()
            _matched = None
            _db_oc = load_database()
            _rest_oc = _get_restaurant(_db_oc, rest_id)
            if _rest_oc:
                for _o2 in _rest_oc.get("marketplace_orders", []):
                    _oid_db = _o2.get("order_id", "").lower()
                    if _oid_db == _oid or _oid_db.endswith(_oid) or _oid in _oid_db:
                        if _o2.get("status") == "pending":
                            _o2["status"] = "cancelled"
                            _o2["cancel_reason"] = "Shopkeeper rejected"
                            _o2["updated_at"] = datetime.datetime.now().isoformat()
                            _o2["updated_by"] = "nlp"
                            _matched = _o2
                        break
                if _matched:
                    
                    side_replies.append(f"\u274c Order `{_matched['order_id']}` rejected.")
                else:
                    side_replies.append(f"\u26a0\ufe0f Order `{_oid or '?'}` not found or not pending.")

        elif _intent == "order_confirm":
            _oid = (_idata.get("order_id") or "").lower().strip()
            _matched = None
            _db_oc = load_database()
            _rest_oc = _get_restaurant(_db_oc, rest_id)
            if _rest_oc:
                for _o2 in _rest_oc.get("marketplace_orders", []):
                    _oid_db = _o2.get("order_id", "").lower()
                    if _oid_db == _oid or _oid_db.endswith(_oid) or _oid in _oid_db:
                        if _o2.get("status") in ("pending", "accepted"):
                            _o2["status"] = "completed"
                            _o2["confirmed_at"] = datetime.datetime.now().isoformat()
                            _o2["confirmed_by"] = "nlp"
                            _matched = _o2
                        break
                if _matched:
                    
                    side_replies.append(
                        f"\u2705 Order `{_matched['order_id']}` from "
                        f"*{_matched.get('customer_name','Customer')}* marked *collected*. Revenue recorded!"
                    )
                else:
                    side_replies.append(f"\u26a0\ufe0f Order `{_oid or '?'}` not found or cannot be collected.")

        elif _intent == "order_miss":
            _oid = (_idata.get("order_id") or "").lower().strip()
            _matched = None
            _db_m = load_database()
            _rest_m = _get_restaurant(_db_m, rest_id)
            if _rest_m:
                for _o in _rest_m.get("marketplace_orders", []):
                    _oid_db = _o.get("order_id", "").lower()
                    if _oid_db == _oid or _oid_db.endswith(_oid) or _oid in _oid_db:
                        if _o.get("status") in ("pending", "accepted"):
                            _o["status"] = "cancelled"
                            _o["cancel_reason"] = "Customer didn't pick up"
                            _o["confirmed_at"] = datetime.datetime.now().isoformat()
                            _o["confirmed_by"] = "nlp"
                            _matched = _o
                        break
            if _matched:
                
                side_replies.append(
                    f"\u274c Order `{_matched['order_id']}` from "
                    f"*{_matched.get('customer_name','Customer')}* marked *not picked up*. "
                    f"Inventory released."
                )
            else:
                side_replies.append(f"\u26a0\ufe0f Order `{_oid or '?'}` not found or cannot be missed.")

        elif _intent == "update_discount":
            _item_name = _idata.get("item")
            _disc_pct = _idata.get("discount_pct")
            if _disc_pct is not None:
                _disc_pct = max(0, min(70, int(_disc_pct)))
                _db_d = load_database()
                _rest_d = _get_restaurant(_db_d, rest_id)
                if _rest_d:
                    if _item_name:

                        _listings = _rest_d.setdefault("marketplace_listings", {})
                        _cfg = _listings.get(_item_name, {})
                        _cfg["discount_pct"] = _disc_pct
                        _listings[_item_name] = _cfg
                        side_replies.append(
                            f"\ud83c\udff7\ufe0f *{_item_name }* discount set to *{_disc_pct }% off*."
                        )
                    else:

                        _rest_d["discount_pct"] = _disc_pct
                        side_replies.append(
                            f"\ud83c\udff7\ufe0f Global closing discount set to *{_disc_pct }% off* for all items."
                        )
                    save_database(_db_d)
            else:
                side_replies.append("\u26a0\ufe0f Could not read discount percentage.")

        elif _intent == "fetch_sales":
            _item_raw = _idata.get("item")
            _target_date_raw = _idata.get("target_date")
            if _target_date_raw == "yesterday":
                _target_date = get_yesterday(rest).isoformat()
            elif _target_date_raw and _target_date_raw != "today":
                _target_date = _target_date_raw
            else:
                _target_date = get_today(rest).isoformat()
            _db_fs = load_database()
            _rest_fs = _get_restaurant(_db_fs, rest_id)
            if not _rest_fs:
                side_replies.append("Restaurant not found.")
                continue
                
            _menu_map = {m["item"].lower(): m for m in _rest_fs.get("menu", [])}

            # Count sources separately so the user can see the full picture
            _record_sales = {}
            for _rec in _rest_fs.get("daily_records", []):
                if _rec.get("date") == _target_date:
                    for k, v in (_rec.get("actual_sales") or {}).items():
                        _record_sales[k.lower()] = _record_sales.get(k.lower(), 0) + int(v)
                    break

            _market_sales = {}
            for o in _rest_fs.get("marketplace_orders", []):
                if o.get("date") == _target_date and o.get("status") == "completed":
                    for oi in o.get("items", []):
                        _k = oi.get("item", "").lower()
                        _market_sales[_k] = _market_sales.get(_k, 0) + int(oi.get("qty", 0))

            # Merge for display
            _all_keys = set(_record_sales.keys()) | set(_market_sales.keys())
            _target_sales_lower = {}
            for _k in _all_keys:
                _target_sales_lower[_k] = _record_sales.get(_k, 0) + _market_sales.get(_k, 0)

            _target_sales = _target_sales_lower  # for the full sales printout block below

            # Format the date label with exact date for clarity
            _today_iso = get_today(rest).isoformat()
            if _target_date == _today_iso:
                _date_label = f"today ({_target_date})"
            else:
                _date_label = f"on {_target_date}"

            if _item_raw:
                _canonical_item = _fuzzy_match_menu(_item_raw, _menu_map)

                if _canonical_item and _canonical_item.lower() in _target_sales_lower:
                    _v = _target_sales_lower[_canonical_item.lower()]
                    _rec_v = _record_sales.get(_canonical_item.lower(), 0)
                    _mkt_v = _market_sales.get(_canonical_item.lower(), 0)
                    side_replies.append(
                        f"ÃƒÂ°Ã…Â¸Ã¢â‚¬Å“Ã…Â  You sold *{_v}* portions of *{_canonical_item}* {_date_label}."
                    )
                else:
                    _display_name = _canonical_item or _item_raw
                    # Help user understand which dates have seeded data
                    _available_dates = sorted({r.get("date", "") for r in _rest_fs.get("daily_records", []) if r.get("actual_sales", {}).get(_display_name, 0) > 0 or any(v > 0 for v in (r.get("actual_sales") or {}).values())})
                    _tip = f"\n\n_Tip: Historical data exists for {len(_available_dates)} past days. Ask: 'sales on {_available_dates[-1]}' to see older records._" if _available_dates else ""
                    side_replies.append(f"ÃƒÂ°Ã…Â¸Ã¢â‚¬Å“Ã…Â  No sales recorded for '*{_display_name}*' {_date_label}.{_tip}")
            else:
                if _target_sales:
                    _sales_lines = [f"ÃƒÂ°Ã…Â¸Ã¢â‚¬Å“Ã…Â  *Sales {_date_label}:*\n"]
                    for k, v in sorted(_target_sales.items(), key=lambda x: -x[1]):
                        _rec_v = _record_sales.get(k, 0)
                        _mkt_v = _market_sales.get(k, 0)
                        if _rec_v and _mkt_v:
                            _sales_lines.append(f"ÃƒÂ¢Ã¢â€šÂ¬Ã‚Â¢ {k.title()}: *{v}*")
                        else:
                            _sales_lines.append(f"ÃƒÂ¢Ã¢â€šÂ¬Ã‚Â¢ {k.title()}: *{v}*")
                    side_replies.append("\n".join(_sales_lines))
                else:
                    # Find nearest date with data
                    _all_dated = sorted({r.get("date", "") for r in _rest_fs.get("daily_records", []) if r.get("actual_sales")}, reverse=True)
                    _tip = f"\n\n_Tip: Try asking 'sales on {_all_dated[0]}' for the most recent historical data._" if _all_dated else ""
                    side_replies.append(f"ÃƒÂ°Ã…Â¸Ã¢â‚¬Å“Ã…Â  No sales recorded for {_date_label} yet.{_tip}")

        else:
            remaining_intents.append(_idata)

    if side_replies:
        await _send(client, chat_id, "\n".join(side_replies))

    if not remaining_intents:
        return

    intent_data = remaining_intents[0]
    intent = intent_data.get("intent", "general")

    if intent == "greeting":
        now = datetime.datetime.now().strftime("%d %b %Y, %I:%M %p")
        await _send(client, chat_id, f"ÃƒÂ°Ã…Â¸Ã¢â‚¬ËœÃ¢â‚¬Â¹ WasteWise AI for *{rest ['name']}*. ÃƒÂ°Ã…Â¸Ã¢â‚¬Å“Ã¢â‚¬Â¦ {now }")

    elif tl in ("inventory", "/inventory", "stok", "remaining", "baki", "sisa"):
        from services.inventory import compute_remaining_inventory

        remaining = compute_remaining_inventory(rest)
        lines = [f"ÃƒÂ°Ã…Â¸Ã¢â‚¬Å“Ã‚Â¦ *Remaining Inventory ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Â {rest ['name']}*\n"]
        total_remaining = 0
        discount_pct = rest.get("discount_pct", 30)
        for item in remaining:
            if item["remaining"] > 0:
                disc = round(item['profit_margin_rm'] * (1 - discount_pct / 100), 2)
                lines.append(
                    f"ÃƒÂ¢Ã¢â€šÂ¬Ã‚Â¢ {item['item']}: *{item['remaining']}* portions (ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¹{disc:.2f} at {discount_pct}% off)"
                )
                total_remaining += item["remaining"]
        if total_remaining == 0:
            lines.append("ÃƒÂ¢Ã…â€œÃ¢â‚¬Â¦ All sold out ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Â great day!")
        else:
            lines.append(
                f"\nTotal: *{total_remaining }* portions available at {discount_pct }% discount"
            )
        await _send(client, chat_id, "\n".join(lines))

    elif tl in ("orders", "/orders", "pesanan"):
        today_str = get_today(rest).isoformat()
        orders = [o for o in rest.get("marketplace_orders", []) if o.get("date") == today_str]
        if not orders:
            await _send(
                client,
                chat_id,
                f"ÃƒÂ°Ã…Â¸Ã¢â‚¬Å“Ã¢â‚¬Â¹ No marketplace orders for today yet.\n\nShare your store link to get orders!",
            )
        else:
            revenue = sum(o["total_rm"] for o in orders if o.get("status") != "cancelled")
            lines = [f"ÃƒÂ°Ã…Â¸Ã¢â‚¬ÂºÃ‚ÂÃƒÂ¯Ã‚Â¸Ã‚Â *Today's Orders ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Â {rest ['name']}*\n"]
            for o in orders[-10:]:
                status_emoji = {"pending": "ÃƒÂ¢Ã‚ÂÃ‚Â³", "completed": "ÃƒÂ¢Ã…â€œÃ¢â‚¬Â¦", "cancelled": "ÃƒÂ¢Ã‚ÂÃ…â€™"}.get(
                    o["status"], "ÃƒÂ¢Ã‚ÂÃ¢â‚¬Å“"
                )
                items_str = ", ".join(f"{oi['qty']}x{oi['item']}" for oi in o.get("items", []))
                lines.append(
                    f"{status_emoji} {o['customer_name']} ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Â {items_str} (ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¹{o['total_rm']:.2f})"
                )
            lines.append(f"\nÃƒÂ°Ã…Â¸Ã¢â‚¬â„¢Ã‚Â° Total revenue: *ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¹{revenue:.2f}*")
            lines.append(f"ÃƒÂ°Ã…Â¸Ã‚ÂÃ‚Â¦ Your share (90%): *ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¹{revenue*0.90:.2f}*")
            await _send(client, chat_id, "\n".join(lines))

    elif tl in ("sales", "/sales", "jualan", "pendapatan", "profit"):
        from services.inventory import get_today_profit_summary

        today_summary = get_today_profit_summary(rest)
        lines = [
            f"\U0001f4b0 *Today's Sales \u2014 {rest['name']}*\n",
            f"\U0001f4ca Regular sales: \u20b9{today_summary['regular_sales_rm']:.2f}",
            f"\U0001f6d2 Marketplace orders: \u20b9{today_summary['marketplace_revenue_rm']:.2f}",
            f"\U0001f3e6 Your earnings: *\u20b9{today_summary['shopkeeper_earnings_rm']:.2f}*",
            f"\U0001f4f1 Platform fee: \u20b9{today_summary['platform_fee_rm']:.2f}",
            f"\U0001f4e6 Orders today: {today_summary['total_orders']}",
        ]
        await _send(client, chat_id, "\n".join(lines))

    elif intent == "forecast":
        forecast = await asyncio.to_thread(_do_generate_forecast, rest_id)
        await _send(client, chat_id, forecast)

    elif intent == "menu_show":
        if not rest.get("menu"):
            await _send(
                client,
                chat_id,
                "No menu items yet!\n\n"
                "Tell me what you sell: _'I sell Pyaaz Kachori, Masala Chai, and Kuih'_\n"
                "Or use *set ingredients for [item]: [ingredients]* to define ingredient ratios.",
            )
            return
        lines = [f"ÃƒÂ°Ã…Â¸Ã¢â‚¬Å“Ã¢â‚¬Â¹ *{rest ['name']} ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Â Menu*\n"]
        has_bom = rest.get("bom", {})
        for m in rest["menu"]:
            bom_note = " ÃƒÂ¢Ã…â€œÃ¢â‚¬Â¦" if m["item"] in has_bom else " _(no ingredients set)_"
            lines.append(f"ÃƒÂ¢Ã¢â€šÂ¬Ã‚Â¢ {m ['item']} ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Â RM {m ['profit_margin_rm']:.2f} margin{bom_note }")
        if not has_bom:
            lines.append(
                "\n_Tip: Set ingredient ratios for accurate shopping lists._\n"
                "_Example: 'Set ingredients for Pyaaz Kachori: 200g rice, 50ml coconut milk'_"
            )
        await _send(client, chat_id, "\n".join(lines))

    elif intent == "login":
        await _send(client, chat_id, "Select your restaurant:", reply_markup=_rest_keyboard())
        _set_state(chat_id, "choosing_restaurant")

    if tl in ("help", "/help"):
        await _send(
            client,
            chat_id,
            "ÃƒÂ°Ã…Â¸Ã…â€™Ã‚Â¿ *WasteWise AI ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Â Help*\n\n"
            "Just talk normally in English or Malay!\n\n"
            "ÃƒÂ°Ã…Â¸Ã¢â‚¬Å“Ã…Â  *Forecast* ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Â _'What to cook today?'_, _'How many Dal Baati Churma?'_\n"
            "ÃƒÂ°Ã…Â¸Ã¢â‚¬Å“Ã¢â‚¬Â¹ *Menu* ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Â _'Show menu'_, _'Add Milo Ais'_, _'Remove Ice Cream'_\n"
            "ÃƒÂ°Ã…Â¸Ã¢â‚¬Å“Ã‹â€  *Sales* ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Â _'Sold 95 Pyaaz Kachori today'_\n"
            "ÃƒÂ°Ã…Â¸Ã…Â½Ã¢â‚¬Â° *Events* ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Â _'Wedding tomorrow 300 guests'_\n"
            "ÃƒÂ°Ã…Â¸Ã‚Â¥Ã‹Å“ *Ingredients* ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Â _'Set ingredients for Dal Baati Churma: 120g flour, 20g ghee'_\n"
            "ÃƒÂ°Ã…Â¸Ã¢â‚¬Å“Ã‚Â¦ *Inventory* ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Â `inventory` or `stok`\n"
            "ÃƒÂ°Ã…Â¸Ã¢â‚¬ÂºÃ‚ÂÃƒÂ¯Ã‚Â¸Ã‚Â *Orders* ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Â `orders` ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Â today's orders\n"
            "ÃƒÂ¢Ã…â€œÃ¢â‚¬Â¦ *Confirm order* ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Â `done 5` or `collected 5` (today's order number)\n"
            "ÃƒÂ¢Ã‚ÂÃ…â€™ *Miss order* ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Â `miss 5` or `missed 5`\n"
            "ÃƒÂ°Ã…Â¸Ã¢â‚¬â„¢Ã‚Â° *Profit* ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Â `sales` or `profit`\n\n"
            "ÃƒÂ°Ã…Â¸Ã¢â‚¬ËœÃ‚Â¥ *Sessions / Devices*\n"
            "  ÃƒÂ¢Ã¢â€šÂ¬Ã‚Â¢ _'Who is logged in?'_ or `security`\n"
            "  ÃƒÂ¢Ã¢â€šÂ¬Ã‚Â¢ _'Show all devices'_ or `sessions`\n"
            "  ÃƒÂ¢Ã¢â€šÂ¬Ã‚Â¢ `/remove_xxxxxxxx` ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Â remove a linked device\n"
            "  ÃƒÂ¢Ã¢â€šÂ¬Ã‚Â¢ `/make_primary @username` ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Â transfer primary to another Telegram\n\n"
            "ÃƒÂ°Ã…Â¸Ã¢â‚¬ÂÃ¢â‚¬â€ *Chain Management*\n"
            "  ÃƒÂ¢Ã¢â€šÂ¬Ã‚Â¢ `create chain My Group` ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Â create a restaurant chain\n"
            "  ÃƒÂ¢Ã¢â€šÂ¬Ã‚Â¢ `my chains` ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Â list your chains\n"
            "  ÃƒÂ¢Ã¢â€šÂ¬Ã‚Â¢ `add to chain chain_xxxx` ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Â link this restaurant to a chain\n\n"
            "ÃƒÂ°Ã…Â¸Ã¢â‚¬â€Ã¢â‚¬ËœÃƒÂ¯Ã‚Â¸Ã‚Â *Delete Account*\n"
            "  ÃƒÂ¢Ã¢â€šÂ¬Ã‚Â¢ `/delete_account` ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Â starts the deletion flow\n\n"
            "ÃƒÂ°Ã…Â¸Ã¢â‚¬ÂÃ¢â‚¬Ëœ *login* ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Â Switch restaurant\n"
            "ÃƒÂ°Ã…Â¸Ã¢â‚¬Å“Ã‚Â *register* ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Â Add a new restaurant",
        )
        return

    elif intent == "event":
        description = intent_data.get("description") or "Special event"
        headcount = max(1, min(100_000, intent_data.get("headcount") or 50))
        days = max(1, min(30, intent_data.get("days") or 1))
        summary = intent_data.get("summary", description)
        register_owner_event(rest_id, description, headcount, days)
        await _typing(client, chat_id)
        import asyncio
        forecast = await asyncio.to_thread(_do_generate_forecast, rest_id)
        await _send(client, chat_id, f"ÃƒÂ¢Ã…â€œÃ¢â‚¬Â¦ Event registered: {summary }\n\n{forecast }")

    elif intent == "sales":
        result = await asyncio.to_thread(process_ai_data_ingestion, rest_id, text, "none")
        await _send(client, chat_id, f"ÃƒÂ°Ã…Â¸Ã¢â‚¬Å“Ã…Â  {result}")
        # Immediately follow up with updated forecast ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Â no artificial sleep
        await _typing(client, chat_id)
        forecast = await asyncio.to_thread(_do_generate_forecast, rest_id)
        await _send(client, chat_id, f"ÃƒÂ°Ã…Â¸Ã¢â‚¬ÂÃ¢â‚¬Å¾ *Updated forecast:*\n\n{forecast}")


    elif intent == "menu_add":
        items_before = {m["item"] for m in rest.get("menu", [])}
        result = await asyncio.to_thread(process_ai_data_ingestion, rest_id, text, "append")
        from services.nlp import load_db_for
        db2 = load_db_for(rest_id)
        rest2 = _get_restaurant(db2, rest_id)
        items_after = {m["item"] for m in (rest2.get("menu", []) if rest2 else [])}
        newly_added = list(items_after - items_before)
        _set_data(chat_id, last_action="menu_add", last_result=result, last_added_items=newly_added)
        await _send(client, chat_id, f"ÃƒÂ°Ã…Â¸Ã¢â‚¬Å“Ã¢â‚¬Â¹ {result}")
        if newly_added:
            await _send(client, chat_id, f"\u2705 Added: *{', '.join(newly_added)}*")
            db3 = load_db_for(rest_id)
            rest3 = _get_restaurant(db3, rest_id)
            if rest3 and newly_added[0] not in rest3.get("bom", {}):
                await _ask_bom_interactive(client, chat_id, newly_added[0])
                return
        # Forecast in background ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Â don't block; send immediately when done
        await _typing(client, chat_id)
        forecast = await asyncio.to_thread(_do_generate_forecast, rest_id)
        await _send(client, chat_id, forecast)


    elif intent == "menu_remove":
        import asyncio
        result = await asyncio.to_thread(process_ai_data_ingestion, rest_id, text, "remove")
        await _send(client, chat_id, f"ÃƒÂ°Ã…Â¸Ã¢â‚¬Å“Ã¢â‚¬Â¹ {result }")

    elif intent == "causal_analysis":
        await _typing(client, chat_id)
        
        _target_date_raw = intent_data.get("target_date")
        if _target_date_raw == "yesterday":
            _target_date = get_yesterday(rest).isoformat()
        elif _target_date_raw == "today":
            _target_date = get_today(rest).isoformat()
        elif _target_date_raw:
            _target_date = _target_date_raw
        else:
            _target_date = get_yesterday(rest).isoformat()

        _item_raw = intent_data.get("item")
        _canonical_item = None
        if _item_raw:
            _menu_map = {m["item"].lower(): m for m in rest.get("menu", [])}
            _canonical_item = _fuzzy_match_menu(_item_raw, _menu_map)

        try:
            from services.causal_ai import format_causal_report_telegram

            import asyncio
            report = await asyncio.to_thread(format_causal_report_telegram, rest, _target_date, _canonical_item)
            await _send(client, chat_id, report)
        except Exception as e:
            await _send(client, chat_id, f"ÃƒÂ¢Ã‚ÂÃ…â€™ Causal analysis error: {e }")

    elif intent == "menu_engineering":
        await _typing(client, chat_id)
        if not rest.get("menu"):
            await _send(client, chat_id, "ÃƒÂ¢Ã‚ÂÃ…â€™ Add menu items first before I can analyse your menu.")
        else:
            try:
                from services.menu_engineering import (
                    classify_menu_items,
                    generate_menu_recommendations,
                    format_engineering_report,
                )

                _item_raw = intent_data.get("item")
                _canonical_item = None
                if _item_raw:
                    _menu_map = {m["item"].lower(): m for m in rest.get("menu", [])}
                    _canonical_item = _fuzzy_match_menu(_item_raw, _menu_map)

                import asyncio
                report = await asyncio.to_thread(format_engineering_report, rest, _canonical_item)
                await _send(client, chat_id, report)
            except Exception as e:
                await _send(client, chat_id, f"ÃƒÂ¢Ã‚ÂÃ…â€™ Menu analysis error: {e }")

    elif intent == "cv_inventory":
        _set_state(chat_id, "awaiting_cv_inventory_photo")
        await _send(
            client,
            chat_id,
            "ÃƒÂ°Ã…Â¸Ã¢â‚¬Å“Ã‚Â¸ *Inventory Scan*\n\n"
            "Send me a photo of your ingredient shelf or storage area.\n"
            "I'll use computer vision to detect what you have and how much.\n\n"
            "_Make sure the photo is clear and well-lit for best results._",
        )

    else:

        if len(remaining_intents) > 1:
            await _send(
                client,
                chat_id,
                f"\u2139\ufe0f _{len (remaining_intents )-1 } more action(s) from your message were processed above._",
            )
        now_str = datetime.datetime.now().strftime("%d %b %Y, %I:%M %p")
        data_ctx = _get_data(chat_id)
        last_action = data_ctx.get("last_action", "")
        last_result = data_ctx.get("last_result", "")
        last_added = data_ctx.get("last_added_items", [])
        context_note = ""
        if last_action and last_result:
            context_note = f"Last action: {last_action }. Result: {last_result [:200 ]}\n"
        if last_added:
            context_note += f"Recently added to menu: {last_added }\n"
        prompt = (
            f"You are WasteWise AI for {rest ['name']} ({rest .get ('region','India')}).\n"
            f"Time: {now_str }\n"
            f"Menu ({len (rest .get ('menu',[]))} items): {[m ['item']for m in rest .get ('menu',[])]}\n"
            f"Recent owner notes: {[m ['message']for m in rest .get ('recent_feedback_memory',[])[-3 :]]}\n"
            f"{context_note }\n"
            f"Owner said: \"{text }\"\n\n"
            "Answer the specific question directly and concisely (under 80 words). "
            "If they ask what was just added, use the 'Recently added' context above ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Â give the specific item name. "
            "If they ask about a specific item, answer about that item only. "
            "Do not dump a full list when a specific answer is expected."
        )
        reply = await asyncio.to_thread(call_ai, prompt, False)
        if reply:
            await _send(client, chat_id, reply)
        elif any(w in tl for w in ["time", "masa", "pukul", "jam"]):
            now = datetime.datetime.now().strftime("%d %b %Y, %I:%M %p")
            await _send(client, chat_id, f"ÃƒÂ°Ã…Â¸Ã¢â‚¬Â¢Ã‚Â *{now }*")
        else:
            await _send(
                client, chat_id, "Got it! Ask me about your forecast, sales, menu, or events."
            )


async def _start_bot_login(client: httpx.AsyncClient, chat_id: int) -> None:
    """Start the email-based login flow from Telegram."""
    _set_state(chat_id, "bot_login_email")
    await _send(
        client, chat_id, "ÃƒÂ°Ã…Â¸Ã¢â‚¬ÂÃ¢â‚¬Ëœ *Login to WasteWise AI*\n\n" "Enter your registered email address:"
    )


async def _handle_bot_login_email(client: httpx.AsyncClient, chat_id: int, email: str) -> None:
    """Send OTP to primary Telegram UUID for login."""
    account = auth.get_account_by_email(email)
    if not account:
        await _send(
            client,
            chat_id,
            "ÃƒÂ¢Ã‚ÂÃ…â€™ No account found for that email.\n\n"
            "Type *register* to create a new account, or try a different email.",
        )
        _set_state(chat_id, None)
        return

    primary = next(
        (s for s in account.get("sessions", []) if s.get("is_primary") and s.get("chat_id")), None
    )
    if not primary:
        await _send(client, chat_id, "ÃƒÂ¢Ã…Â¡Ã‚Â ÃƒÂ¯Ã‚Â¸Ã‚Â Could not find your primary Telegram account to send OTP.")
        _set_state(chat_id, None)
        return

    is_same_as_primary = primary["chat_id"] == chat_id
    if is_same_as_primary:
        otp = auth.create_otp(email, "bot_login")
        await _send(
            client, chat_id, f"ÃƒÂ°Ã…Â¸Ã¢â‚¬ÂÃ‚Â Your OTP: *{otp }*\n" f"Enter it here {_otp_minutes_note ()}:"
        )
        _set_data(chat_id, login_email=email)
        _set_state(chat_id, "bot_login_otp")
    else:
        # Use the persistent client ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Â avoid creating/leaking a throwaway AsyncClient
        _tmp_client = _get_tg_client()
        _chat_resp = await _api(_tmp_client, "getChat", chat_id=chat_id)
        username = _chat_resp.get("result", {}).get("username", f"id:{chat_id }")

        approval_id = auth.create_approval_request(primary["chat_id"], chat_id, f"@{username }")

        kb = _inline_keyboard(
            [
                [
                    {"text": "ÃƒÂ¢Ã…â€œÃ¢â‚¬Â¦ Approve", "callback_data": f"approve:{approval_id }"},
                    {"text": "ÃƒÂ¢Ã‚ÂÃ…â€™ Deny", "callback_data": f"deny:{approval_id }"},
                ]
            ]
        )
        await _send(
            client,
            primary["chat_id"],
            f"ÃƒÂ°Ã…Â¸Ã¢â‚¬ÂÃ¢â‚¬Â *New login request*\n\n"
            f"@{username } wants to link to your WasteWise account ({account ['email']}).\n\n"
            "Do you approve this?",
            reply_markup=kb,
        )
        await _send(
            client,
            chat_id,
            "ÃƒÂ¢Ã…â€œÃ¢â‚¬Â¦ Approval request sent to your primary Telegram account.\n"
            "Once approved, you'll be logged in automatically.",
        )
        _set_data(chat_id, login_email=email, pending_approval_id=approval_id)
        _set_state(chat_id, "bot_awaiting_approval")


async def _handle_security_menu(client: httpx.AsyncClient, chat_id: int) -> None:
    """
    Show account security with inline tap-buttons per session.
    Primary sees: Remove button per non-primary session, Make Primary button per non-primary Telegram session.
    Non-primary sees: read-only list + message to contact primary.
    """
    account = auth.get_account_by_telegram(chat_id)
    if not account:
        await _send(client, chat_id, "Please login first. Type `login`.")
        return

    sessions = auth.get_sessions_for_account(account["email"])
    is_primary_caller = any(s.get("is_primary") and s.get("chat_id") == chat_id for s in sessions)

    lines = [f"ÃƒÂ°Ã…Â¸Ã¢â‚¬ÂÃ‚Â *Security ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Â {account ['email']}*\n"]
    lines.append(f"Total sessions: *{len (sessions )}*\n")

    await _send(client, chat_id, "\n".join(lines))

    for s in sessions:
        stype = s.get("type", "web")
        sid = s.get("session_id", "")[:8]
        is_p = s.get("is_primary", False)
        uname = s.get("telegram_username", "")
        label = s.get("label", "")
        exp = s.get("expires_at", "")

        if stype == "telegram":
            icon = "ÃƒÂ¢Ã‚Â­Ã‚Â" if is_p else "ÃƒÂ°Ã…Â¸Ã¢â‚¬Å“Ã‚Â±"
            name = f"@{uname }" if uname else f"Telegram"
        else:
            icon = "ÃƒÂ°Ã…Â¸Ã…â€™Ã‚Â"
            name = label or "Web browser"

        if is_p:
            exp_str = "never expires"
        elif exp:
            exp_str = f"exp. {exp [:10 ]}"
        else:
            exp_str = "no expiry set"

        session_text = f"{icon } *{name }*\n" f"Type: {stype } Ãƒâ€šÃ‚Â· {exp_str }\n" f"ID: `{sid }`"

        buttons = []
        if not is_p:
            buttons.append({"text": "ÃƒÂ°Ã…Â¸Ã¢â‚¬â€Ã¢â‚¬Ëœ Remove this session", "callback_data": f"sec:remove:{sid }"})
        if stype == "telegram" and not is_p and is_primary_caller and uname:
            buttons.append({"text": "ÃƒÂ¢Ã‚Â­Ã‚Â Make Primary", "callback_data": f"sec:mkprimary:{uname }"})

        kb = _inline_keyboard([buttons]) if buttons else None
        await _send(client, chat_id, session_text, reply_markup=kb)

    if is_primary_caller:
        await _send(
            client,
            chat_id,
            "ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬\n"
            "ÃƒÂ¢Ã‚Â­Ã‚Â *You are the primary account.*\n"
            "ÃƒÂ¢Ã¢â€šÂ¬Ã‚Â¢ Tap ÃƒÂ¢Ã‚Â­Ã‚Â Make Primary on a Telegram session to transfer\n"
            "ÃƒÂ¢Ã¢â€šÂ¬Ã‚Â¢ Tap ÃƒÂ°Ã…Â¸Ã¢â‚¬â€Ã¢â‚¬Ëœ Remove on any session to revoke access\n"
            "ÃƒÂ¢Ã¢â€šÂ¬Ã‚Â¢ `/delete_account` ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Â delete this account",
        )
    else:
        await _send(
            client,
            chat_id,
            "ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬\n"
            "_You are a secondary device._\n"
            "Contact the primary Telegram account to remove sessions or transfer primary status.",
        )


async def process_update(client: httpx.AsyncClient, update: dict) -> None:
    try:
        if "callback_query" in update:
            await handle_callback(client, update["callback_query"])
            return
        msg = update.get("message") or update.get("edited_message")
        if not msg:
            return
        chat_id = msg["chat"]["id"]
        username = msg.get("from", {}).get("username", "") or ""
        if "photo" in msg:
            await handle_photo(client, chat_id, msg["photo"])
        elif "document" in msg:
            await handle_document(client, chat_id, msg["document"])
        elif "text" in msg:

            text = msg["text"]
            if text.startswith("/"):
                text = text[1:].split("@")[0]
            tl = text.strip().lower()
            if tl in ("register", "start register"):
                await start_registration(client, chat_id)
            elif any(
                phrase in tl
                for phrase in (
                    "who is logged in",
                    "who's logged in",
                    "show devices",
                    "show all devices",
                    "security",
                    "sessions",
                    "my sessions",
                    "linked accounts",
                    "who logged in",
                    "list devices",
                    "list sessions",
                    "show sessions",
                    "who can access",
                    "active sessions",
                )
            ):
                await _handle_security_menu(client, chat_id)
            elif tl.startswith("make_primary ") or tl.startswith("/make_primary "):

                account = auth.get_account_by_telegram(chat_id)
                primary_s = next(
                    (
                        s
                        for s in (account or {}).get("sessions", [])
                        if s.get("is_primary") and s.get("chat_id") == chat_id
                    ),
                    None,
                )
                if not primary_s:
                    await _send(
                        client,
                        chat_id,
                        "ÃƒÂ¢Ã‚ÂÃ…â€™ Only the current primary account can transfer primary status.",
                    )
                else:
                    target_uname = tl.split(" ", 1)[1].lstrip("@").strip()
                    sessions = auth.get_sessions_for_account(account["email"])
                    target_s = next(
                        (
                            s
                            for s in sessions
                            if s.get("telegram_username", "").lower() == target_uname.lower()
                            and s.get("type") == "telegram"
                        ),
                        None,
                    )
                    if not target_s:
                        await _send(
                            client,
                            chat_id,
                            f"ÃƒÂ¢Ã‚ÂÃ…â€™ No Telegram session found for @{target_uname }.\n\nType `security` to see all linked sessions.",
                        )
                    elif target_s["session_id"] == primary_s["session_id"]:
                        await _send(client, chat_id, "ÃƒÂ¢Ã‚Â­Ã‚Â You are already the primary.")
                    else:

                        db_p = load_database()
                        acc_p = next(
                            (
                                a
                                for a in db_p.get("accounts", [])
                                if a["email"].lower() == account["email"].lower()
                            ),
                            None,
                        )
                        if acc_p:
                            for s in acc_p.get("sessions", []):
                                s["is_primary"] = s["session_id"] == target_s["session_id"]
                            
                            await _send(
                                client,
                                chat_id,
                                f"ÃƒÂ¢Ã…â€œÃ¢â‚¬Â¦ *Primary transferred to @{target_uname }.*\n\n"
                                f"They now hold primary status and can approve logins, transfers, and deletions.",
                            )

                            new_chat_id = target_s.get("chat_id")
                            if new_chat_id:
                                await _send(
                                    client,
                                    new_chat_id,
                                    f"ÃƒÂ¢Ã‚Â­Ã‚Â *You are now the PRIMARY account for {account ['email']}!*\n\n"
                                    "You can now approve new device logins and manage account settings.",
                                )
            elif tl == "delete_account" or tl == "/delete_account":
                account = auth.get_account_by_telegram(chat_id)
                primary = next(
                    (
                        s
                        for s in (account or {}).get("sessions", [])
                        if s.get("is_primary") and s.get("chat_id") == chat_id
                    ),
                    None,
                )
                if not primary:
                    await _send(
                        client,
                        chat_id,
                        "ÃƒÂ¢Ã‚ÂÃ…â€™ Only the primary Telegram account can delete the account.",
                    )
                else:
                    _set_state(chat_id, "confirm_delete_choice")
                    kb = _inline_keyboard(
                        [
                            [
                                {
                                    "text": "ÃƒÂ°Ã…Â¸Ã…â€™Ã‚Â¿ Anonymise & keep AI data",
                                    "callback_data": "delete:keep",
                                }
                            ],
                            [
                                {
                                    "text": "ÃƒÂ°Ã…Â¸Ã¢â‚¬â„¢Ã‚Â£ Delete everything permanently",
                                    "callback_data": "delete:hard",
                                }
                            ],
                            [{"text": "ÃƒÂ¢Ã‚ÂÃ…â€™ Cancel", "callback_data": "delete:cancel"}],
                        ]
                    )
                    await _send(
                        client,
                        chat_id,
                        "ÃƒÂ¢Ã…Â¡Ã‚Â ÃƒÂ¯Ã‚Â¸Ã‚Â *Delete Account ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Â Choose how:*\n\n"
                        "ÃƒÂ°Ã…Â¸Ã…â€™Ã‚Â¿ *Anonymise & keep data* ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Â Your restaurant info and PII are removed, "
                        "but anonymised sales history is kept to help improve AI for other hawkers.\n\n"
                        "ÃƒÂ°Ã…Â¸Ã¢â‚¬â„¢Ã‚Â£ *Delete everything* ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Â All data permanently erased. Cannot be undone.\n\n"
                        "Which would you prefer?",
                        reply_markup=kb,
                    )
            elif tl == "yes delete my account":
                if _get_state(chat_id) in ("confirm_delete", "confirm_delete_hard"):
                    keep_data = _get_state(chat_id) == "confirm_delete"
                    account = auth.get_account_by_telegram(chat_id)
                    if account:
                        email = account["email"]
                        rest_id = account.get("restaurant_id")
                        auth.delete_account(email)
                        if rest_id:
                            db3 = load_database()
                            if keep_data:
                                rest3 = next(
                                    (r for r in db3.get("restaurants", []) if r["id"] == rest_id),
                                    None,
                                )
                                if rest3:
                                    rest3["name"] = f"[Anonymised Stall {rest_id [-4 :]}]"
                                    rest3["owner_name"] = "[Anonymised]"
                                    rest3["telegram_chat_id"] = None
                                    rest3["telegram_username"] = None
                                    rest3["_anonymised"] = True
                            else:
                                db3["restaurants"] = [
                                    r for r in db3.get("restaurants", []) if r["id"] != rest_id
                                ]
                        
                        _set_state(chat_id, None)
                        _clear_data(chat_id, "restaurant_id")
                        msg_del = (
                            "ÃƒÂ¢Ã…â€œÃ¢â‚¬Â¦ Your account has been anonymised. Sales data is kept to help other hawkers. Thank you! ÃƒÂ°Ã…Â¸Ã…â€™Ã‚Â¿"
                            if keep_data
                            else "ÃƒÂ¢Ã…â€œÃ¢â‚¬Â¦ Your account and all data have been permanently deleted. Thank you for using WasteWise AI."
                        )
                        await _send(client, chat_id, msg_del)
                    else:
                        _set_state(chat_id, None)
                        await _send(client, chat_id, "No account found to delete.")
                else:
                    _set_state(chat_id, None)
                    await _send(client, chat_id, "Delete cancelled.")

            elif tl.startswith("create chain") or tl.startswith("/create_chain"):
                chain_name = tl.replace("create chain", "").replace("/create_chain", "").strip()
                if not chain_name:
                    await _send(
                        client,
                        chat_id,
                        "Please specify a chain name.\n_Example: `create chain My Hawker Group`_",
                    )
                else:
                    account = auth.get_account_by_telegram(chat_id)
                    if not account:
                        await _send(client, chat_id, "ÃƒÂ¢Ã‚ÂÃ…â€™ Please login first.")
                    else:
                        kb = _inline_keyboard(
                            [
                                [
                                    {
                                        "text": "ÃƒÂ¢Ã…â€œÃ¢â‚¬Â¦ Confirm Create Chain",
                                        "callback_data": f"chain:create:{chain_name [:20 ]}",
                                    }
                                ],
                                [{"text": "ÃƒÂ¢Ã‚ÂÃ…â€™ Cancel", "callback_data": "chain:cancel"}],
                            ]
                        )
                        await _send(
                            client,
                            chat_id,
                            f"ÃƒÂ°Ã…Â¸Ã¢â‚¬ÂÃ¢â‚¬â€ *Create chain: \"{chain_name }\"?*\n\n"
                            "This will create a new restaurant chain. "
                            "You can then add your restaurants as branches.\n\n"
                            "_(No Telegram approval needed \u2014 you are already on Telegram!)_",
                            reply_markup=kb,
                        )

            elif any(
                phrase in tl for phrase in ("my chains", "show chains", "list chains", "my chain")
            ):
                account = auth.get_account_by_telegram(chat_id)
                if not account:
                    await _send(client, chat_id, "ÃƒÂ¢Ã‚ÂÃ…â€™ Please login first.")
                else:
                    db_c = load_database()
                    email_c = account["email"].lower()
                    my_chains = [
                        c
                        for c in db_c.get("chains", [])
                        if c.get("owner_email", "").lower() == email_c
                    ]
                    if not my_chains:
                        await _send(
                            client,
                            chat_id,
                            "ÃƒÂ°Ã…Â¸Ã¢â‚¬ÂÃ¢â‚¬â€ *No chains yet.*\n\n"
                            "Type `create chain My Group Name` to create one.",
                        )
                    else:
                        lines = ["ÃƒÂ°Ã…Â¸Ã¢â‚¬ÂÃ¢â‚¬â€ *Your Restaurant Chains*\n"]
                        for ch in my_chains:
                            cid = ch["chain_id"]
                            branches = [
                                r["name"]
                                for r in db_c.get("restaurants", [])
                                if r.get("chain_id") == cid
                            ]
                            lines.append(
                                f"ÃƒÂ¢Ã¢â€šÂ¬Ã‚Â¢ *{ch .get ('name',cid )}* ({ch .get ('chain_type','franchise')})"
                            )
                            lines.append(f"  ID: `{cid }`")
                            lines.append(
                                f"  Branches: {', '.join (branches )if branches else 'none'}"
                            )
                        await _send(client, chat_id, "\n".join(lines))

            elif tl.startswith("add to chain") or tl.startswith("/add_to_chain"):
                rest_id_c = _get_rest_id(chat_id)
                chain_ref = tl.replace("add to chain", "").replace("/add_to_chain", "").strip()
                if not rest_id_c or not chain_ref:
                    await _send(
                        client,
                        chat_id,
                        "Usage: `add to chain chain_xxxxxxxx`\n"
                        "Get your chain ID with `my chains`.",
                    )
                else:
                    db_c2 = load_database()
                    chain_c2 = next(
                        (c for c in db_c2.get("chains", []) if c["chain_id"] == chain_ref), None
                    )
                    if not chain_c2:
                        await _send(
                            client,
                            chat_id,
                            f"ÃƒÂ¢Ã‚ÂÃ…â€™ Chain `{chain_ref }` not found. Check with `my chains`.",
                        )
                    else:
                        rest_obj = next(
                            (r for r in db_c2.get("restaurants", []) if r["id"] == rest_id_c), None
                        )
                        if rest_obj:
                            rest_obj["chain_id"] = chain_ref
                            
                            await _send(
                                client,
                                chat_id,
                                f"ÃƒÂ¢Ã…â€œÃ¢â‚¬Â¦ *{rest_obj ['name']}* added to chain *{chain_c2 .get ('name',chain_ref )}*!",
                            )
                        else:
                            await _send(client, chat_id, "ÃƒÂ¢Ã‚ÂÃ…â€™ Could not find your restaurant.")

            elif tl.startswith("approve ") or tl.startswith("deny "):
                parts = tl.split(" ", 1)
                decision = parts[0] == "approve"
                prefix = parts[1].strip() if len(parts) > 1 else ""
                if prefix:

                    try:
                        from main import _pending_dashboard_approvals

                        match = next(
                            (
                                (tok, e)
                                for tok, e in _pending_dashboard_approvals.items()
                                if tok.startswith(prefix) and e["primary_chat_id"] == chat_id
                            ),
                            None,
                        )
                        if not match:
                            await _send(
                                client, chat_id, "ÃƒÂ¢Ã…Â¡Ã‚Â ÃƒÂ¯Ã‚Â¸Ã‚  Approval request not found or already handled."
                            )
                        else:
                            token, entry = match
                            entry["status"] = "approved" if decision else "denied"
                            emoji = "ÃƒÂ¢Ã…â€œÃ¢â‚¬Â¦" if decision else "ÃƒÂ¢Ã‚ Ã…â€™"
                            await _send(
                                client,
                                chat_id,
                                f"{emoji } Dashboard action *{entry .get ('action','')}* "
                                f"{'approved'if decision else 'denied'}.",
                            )
                    except Exception as _e_ap:
                        await _send(client, chat_id, f"ÃƒÂ¢Ã…Â¡Ã‚Â ÃƒÂ¯Ã‚Â¸Ã‚  Could not process approval: {_e_ap }")

            elif tl.startswith("remove_") and len(tl) > 7:
                session_prefix = tl[7:]
                account = auth.get_account_by_telegram(chat_id)
                if account:
                    sessions = auth.get_sessions_for_account(account["email"])
                    target = next(
                        (s for s in sessions if s["session_id"].startswith(session_prefix)), None
                    )
                    if target and not target.get("is_primary"):
                        removed = auth.remove_session(account["email"], target["session_id"])
                        label = target.get("telegram_username") or target.get("label", "session")
                        await _send(
                            client,
                            chat_id,
                            (
                                f"ÃƒÂ¢Ã…â€œÃ¢â‚¬Â¦ Removed session: @{label }"
                                if removed
                                else "ÃƒÂ¢Ã…Â¡Ã‚Â ÃƒÂ¯Ã‚Â¸Ã‚Â Could not remove that session."
                            ),
                        )
                    else:
                        await _send(
                            client,
                            chat_id,
                            "ÃƒÂ¢Ã‚ÂÃ…â€™ Cannot remove that session (not found or is primary).",
                        )


            elif tl in ("scan_inventory", "scan", "cv_scan", "photo_scan"):
                rest_id = _get_rest_id(chat_id)
                if not rest_id:
                    await _send(client, chat_id, "ÃƒÂ¢Ã‚ÂÃ…â€™ Please login first.")
                else:
                    _set_state(chat_id, "awaiting_cv_inventory_photo")
                    await _send(
                        client,
                        chat_id,
                        "ÃƒÂ°Ã…Â¸Ã¢â‚¬Å“Ã‚Â¸ *Inventory Scan*\n\n"
                        "Send me a photo of your ingredient shelf or storage area.\n"
                        "I'll detect ingredients and quantities using computer vision.\n\n"
                        "_Make sure the photo is clear and well-lit._",
                    )
            else:
                await handle_text(client, chat_id, text, username=username)
    except Exception as e:
        print(f"[Bot] Update error: {e }")
        import traceback

        traceback.print_exc()


async def handle_update(update: dict, token: str) -> None:
    """Entry point for webhook updates from main.py"""
    global TELEGRAM_TOKEN, TG_API
    if token and not TELEGRAM_TOKEN:
        TELEGRAM_TOKEN = token
        TG_API = f"https://api.telegram.org/bot{token}"

    if token:
        TG_API = f"https://api.telegram.org/bot{token}"

    # Configure the send_queue ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Â uses BRIDGE_URL internally, api_base is a marker only
    _sq = get_send_queue()
    bridge_url = os.environ.get("BRIDGE_URL", "").rstrip("/")
    _sq.configure(f"bridge:{bridge_url}" if bridge_url else TG_API)

    # Processing it twice could send duplicate replies, double-credit sales, etc.
    update_id = update.get("update_id")
    if update_id is not None and get_update_dedup().is_duplicate(update_id):
        print(f"[Bot] Duplicate update_id {update_id} ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Â skipping.")
        return

    # Use the persistent shared client ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Â avoids TLS handshake on every webhook
    client = _get_tg_client()
    await process_update(client, update)


