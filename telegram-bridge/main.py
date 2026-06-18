import os
import asyncio
import datetime
import json
import time
import httpx
from fastapi import FastAPI, Request, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from contextlib import asynccontextmanager
from dotenv import load_dotenv

load_dotenv()

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET", "")
HF_BACKEND_URL = os.environ.get("HF_BACKEND_URL", "").rstrip("/")
INTERNAL_SECRET = os.environ.get("INTERNAL_SECRET", "")
BOT_USERNAME = os.environ.get("BOT_USERNAME", "WasteWise_bot")
_RATE_WINDOW = 60
_RATE_LIMIT = 30

_tg_client: httpx.AsyncClient | None = None
_hf_client: httpx.AsyncClient | None = None
_rate_buckets: dict = {}


def _tg_api(path: str) -> str:
    return f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/{path}"


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _tg_client, _hf_client
    _tg_client = httpx.AsyncClient(
        timeout=httpx.Timeout(connect=10.0, read=25.0, write=10.0, pool=5.0),
        limits=httpx.Limits(max_connections=20, max_keepalive_connections=10, keepalive_expiry=60),
    )
    _hf_client = httpx.AsyncClient(
        timeout=httpx.Timeout(connect=10.0, read=55.0, write=10.0, pool=5.0),
        limits=httpx.Limits(max_connections=10, max_keepalive_connections=5, keepalive_expiry=30),
    )
    asyncio.create_task(_warmup())
    yield
    for c in (_tg_client, _hf_client):
        if c and not c.is_closed:
            await c.aclose()


app = FastAPI(title="WasteWise Telegram Bridge", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)

_allowed = [o for o in [HF_BACKEND_URL] if o]
if _allowed:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_allowed,
        allow_methods=["GET", "POST"],
        allow_headers=["X-Internal-Secret", "Content-Type"],
    )


def _check_rate(key: str) -> bool:
    now = time.time()
    bucket = _rate_buckets.get(key, [])
    bucket = [t for t in bucket if now - t < _RATE_WINDOW]
    if len(bucket) >= _RATE_LIMIT:
        return False
    bucket.append(now)
    _rate_buckets[key] = bucket
    return True


async def _warmup():
    await asyncio.sleep(3)
    if not TELEGRAM_TOKEN:
        print("[Bridge] WARNING: TELEGRAM_TOKEN not set")
        return
    try:
        r = await _tg_client.get(_tg_api("getMe"), timeout=12.0)
        d = r.json()
        if d.get("ok"):
            print(f"[Bridge] Connected as @{d['result'].get('username')}")
        else:
            print(f"[Bridge] Telegram warmup failed: {d}")
    except Exception as e:
        print(f"[Bridge] Warmup error: {e}")


async def _tg_send(chat_id: int, text: str, parse_mode: str = "Markdown", reply_markup=None):
    if not TELEGRAM_TOKEN or not chat_id or not text:
        return
    params: dict = {"chat_id": chat_id, "text": text, "parse_mode": parse_mode}
    if reply_markup is not None:
        params["reply_markup"] = json.dumps(reply_markup) if not isinstance(reply_markup, str) else reply_markup
    try:
        await _tg_client.post(_tg_api("sendMessage"), json=params)
    except Exception as e:
        print(f"[Bridge] Send error to {chat_id}: {e}")


async def _forward_to_hf(data: dict) -> dict | None:
    if not HF_BACKEND_URL:
        return None
    headers = {}
    if INTERNAL_SECRET:
        headers["X-Internal-Secret"] = INTERNAL_SECRET
    try:
        r = await _hf_client.post(
            f"{HF_BACKEND_URL}/internal/telegram",
            json=data,
            headers=headers,
            timeout=55.0,
        )
        if r.status_code == 200:
            return r.json()
        print(f"[Bridge] HF returned {r.status_code}")
    except Exception as e:
        print(f"[Bridge] HF forward error: {e}")
    return None


async def _process_update(update: dict):
    msg = update.get("message") or update.get("edited_message")
    cb = update.get("callback_query")

    if cb:
        await _forward_to_hf(update)
        return

    if not msg:
        return

    chat_id: int = msg["chat"]["id"]
    text: str = msg.get("text", "").strip()

    if not text:
        await _forward_to_hf(update)
        return

    result = await _forward_to_hf(update)

    if result and result.get("reply"):
        await _tg_send(chat_id, result["reply"])
    elif result and result.get("replies"):
        for r in result["replies"]:
            await _tg_send(chat_id, r.get("text", ""), reply_markup=r.get("reply_markup"))


@app.get("/")
@app.get("/health")
async def health():
    return {
        "status": "ok",
        "service": "wastewise-telegram-bridge",
        "time": datetime.datetime.utcnow().isoformat(),
    }


@app.post("/webhook")
async def telegram_webhook(request: Request):
    if WEBHOOK_SECRET:
        incoming = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
        if incoming != WEBHOOK_SECRET:
            return JSONResponse({"ok": True})
    try:
        data = await request.json()
    except Exception:
        return JSONResponse({"ok": True})
    asyncio.create_task(_process_update(data))
    return JSONResponse({"ok": True})


@app.post("/api/notify")
async def notify(request: Request):
    if INTERNAL_SECRET:
        incoming = request.headers.get("X-Internal-Secret", "")
        if incoming != INTERNAL_SECRET:
            raise HTTPException(403, "Forbidden")
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(400, "Invalid JSON")

    raw_chat_id = body.get("chat_id")
    text = body.get("text", "")
    parse_mode = body.get("parse_mode", "Markdown")

    if raw_chat_id is None or not text:
        raise HTTPException(400, "chat_id and text required")
    try:
        chat_id = int(raw_chat_id)
    except (ValueError, TypeError):
        raise HTTPException(400, "chat_id must be an integer")

    if not _check_rate(f"notify:{chat_id}"):
        raise HTTPException(429, "Too many requests")

    await _tg_send(chat_id, text, parse_mode)
    return {"status": "sent"}


@app.post("/api/forward")
async def forward_to_telegram(request: Request):
    if INTERNAL_SECRET:
        incoming = request.headers.get("X-Internal-Secret", "")
        if incoming != INTERNAL_SECRET:
            raise HTTPException(403, "Forbidden")
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(400, "Invalid JSON")

    method = body.get("method")
    if not method:
        raise HTTPException(400, "method required")

    params = body.get("params", {})
    try:
        r = await _tg_client.post(_tg_api(method), json=params)
        return JSONResponse(status_code=r.status_code, content=r.json())
    except Exception as e:
        print(f"[Bridge] Forward error for {method}: {e}")
        raise HTTPException(500, str(e))


@app.post("/api/download")
async def download_telegram_file(request: Request):
    if INTERNAL_SECRET:
        incoming = request.headers.get("X-Internal-Secret", "")
        if incoming != INTERNAL_SECRET:
            raise HTTPException(403, "Forbidden")
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(400, "Invalid JSON")

    file_path = body.get("file_path")
    file_id = body.get("file_id")

    if not file_path and not file_id:
        raise HTTPException(400, "file_path or file_id required")

    if not file_path and file_id:
        r_get = await _tg_client.get(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getFile?file_id={file_id}",
            timeout=10.0,
        )
        d_get = r_get.json()
        if not d_get.get("ok"):
            raise HTTPException(400, "Failed to get file_path from Telegram")
        file_path = d_get["result"]["file_path"]

    url = f"https://api.telegram.org/file/bot{TELEGRAM_TOKEN}/{file_path}"
    try:
        r = await _tg_client.get(url, timeout=60)
        return Response(content=r.content, status_code=r.status_code)
    except Exception as e:
        print(f"[Bridge] Download error for {file_path}: {e}")
        raise HTTPException(500, str(e))
