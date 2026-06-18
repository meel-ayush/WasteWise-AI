from services.nlp import get_today
import os
import sys
import re
import uuid
import datetime
import asyncio
import base64
import secrets

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import threading
from fastapi import FastAPI, HTTPException, File, UploadFile, Form, BackgroundTasks, Depends
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from typing import Optional, List
from dotenv import load_dotenv
import httpx

load_dotenv()

from services import auth
from services.bom_ai import ask_bom_conversational
from services.security import (
    require_auth,
    require_restaurant_access,
    AuthenticatedUser,
    SecurityHeadersMiddleware,
    validate_email,
    validate_item_name,
    sanitise_text,
    validate_otp_code,
    check_otp_rate_limit,
    record_failed_otp,
    clear_otp_attempts,
    get_client_ip,
)
from services.audit import AuditMiddleware

from services.nlp import (
    generate_morning_forecast,
    process_ai_data_ingestion,
    register_owner_event,
    get_accuracy_data,
    load_database,
    save_database,
    _get_restaurant,
    _do_generate_forecast,
    process_image_upload,
    get_shopping_list,
)
from services.cache import invalidate_forecast, invalidate_intelligence, invalidate_db
from services.inventory import (
    compute_remaining_inventory,
    compute_profit_split,
    get_today_profit_summary,
    get_weekly_profit_data,
    build_closing_time_telegram_message,
    get_all_marketplace_restaurants,
    get_dynamic_discount,
    get_marketplace_menu,
    get_marketplace_listings,
    ai_optimize_discounts,
)

from services.file_processor import process_upload, extract_image_mime


from services.causal_ai import analyse_underperformance, format_causal_report_telegram
from services.menu_engineering import (
    classify_menu_items,
    generate_menu_recommendations,
    get_weekly_menu_report_telegram,
)
from services.chain_management import (
    create_chain,
    add_branch_to_chain,
    get_chain_summary,
    push_menu_template_to_chain,
    format_chain_telegram_summary,
)
from services.federated_learning import run_federated_round
from services.computer_vision_inventory import scan_inventory_from_image

BRIDGE_URL = os.environ.get("BRIDGE_URL", "").rstrip("/")
INTERNAL_SECRET = os.environ.get("INTERNAL_SECRET", "")

_bridge_http: httpx.AsyncClient | None = None


def _get_bridge_http() -> httpx.AsyncClient:
    global _bridge_http
    if _bridge_http is None or _bridge_http.is_closed:
        _bridge_http = httpx.AsyncClient(
            timeout=httpx.Timeout(connect=8.0, read=20.0, write=10.0, pool=5.0),
            limits=httpx.Limits(max_connections=10, max_keepalive_connections=5),
        )
    return _bridge_http


async def _tg_send(chat_id, text: str, parse_mode: str = "Markdown") -> None:
    """Send a Telegram message. Always routes through the Render bridge."""
    if not chat_id or not text:
        return
    if not BRIDGE_URL:
        print(f"[TgSend] BRIDGE_URL not set ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Â cannot send to chat {chat_id}")
        return
    print(f"[TgSend] ÃƒÂ¢Ã¢â‚¬Â Ã¢â‚¬â„¢ Sending to chat {chat_id} via bridgeÃƒÂ¢Ã¢â€šÂ¬Ã‚Â¦")
    try:
        headers = {"X-Internal-Secret": INTERNAL_SECRET} if INTERNAL_SECRET else {}
        response = await _get_bridge_http().post(
            f"{BRIDGE_URL}/api/notify",
            json={"chat_id": int(chat_id), "text": text, "parse_mode": parse_mode},
            headers=headers,
            timeout=15.0,
        )
        response.raise_for_status()
        print(f"[TgSend] ÃƒÂ¢Ã…â€œÃ¢â‚¬Â¦ Delivered to chat {chat_id}")
    except Exception as _e:
        print(f"[TgSend] ÃƒÂ¢Ã‚ÂÃ…â€™ Bridge error for chat {chat_id}: {_e!r}")


app = FastAPI(
    title="WasteWise AI",
    version="9.0.0",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)

from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from fastapi import Request

limiter = Limiter(key_func=get_remote_address)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)


app.add_middleware(SecurityHeadersMiddleware)


app.add_middleware(AuditMiddleware)


_origins_raw = os.environ.get("ALLOWED_ORIGINS", "").strip()
if not _origins_raw:
    raise RuntimeError(
        "[STARTUP] ALLOWED_ORIGINS environment variable is not set. "
        "Set it to your frontend URL (e.g. https://yourapp.vercel.app or http://localhost:3000) before starting."
    )
ALLOWED_ORIGINS = [o.strip() for o in _origins_raw.split(",") if o.strip()]

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "DELETE", "PUT", "PATCH", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-Requested-With"],
)



@app.on_event("startup")
async def on_startup():
    _backfill_price_rm()
    _backfill_new_restaurant_fields()
    try:
        from services.storage_service import _ensure_buckets
        _ensure_buckets()
    except Exception as e:
        print(f"[Startup] Storage bucket setup skipped: {e}")
    token = os.environ.get("TELEGRAM_TOKEN", "")
    if token:
        from services.scheduler import start_scheduler
        start_scheduler(token)
        asyncio.create_task(_warmup_telegram(token))
    asyncio.create_task(_prewarm_ai_providers())


async def _prewarm_ai_providers() -> None:
    async def _init_gemini():
        try:
            try:
                from services.ai_provider import _get_gemini_client
                await asyncio.get_event_loop().run_in_executor(None, _get_gemini_client)
                print("[Startup] Gemini client pre-warmed (singleton).")
                return
            except ImportError:
                pass
            from services.ai_provider import call_ai, GEMINI_API_KEY
            if GEMINI_API_KEY:
                await asyncio.get_event_loop().run_in_executor(
                    None, call_ai, "ping", False
                )
                print("[Startup] Gemini client pre-warmed (probe call).")
            else:
                print("[Startup] No GEMINI_API_KEY - skipping Gemini pre-warm.")
        except Exception as e:
            print(f"[Startup] AI pre-warm failed (non-fatal): {e}")
    asyncio.create_task(_init_gemini())



async def _warmup_telegram(token: str) -> None:
    """Verify the bridge can reach Telegram at startup. Configures SendQueue.

    Routes through the Render bridge (BRIDGE_URL/api/forward) instead of
    hitting api.telegram.org directly, which is blocked by HF Spaces.
    Delay: 1s Ã¢â€ â€™ 2s Ã¢â€ â€™ 4s Ã¢â€ â€™ 8s Ã¢â€ â€™ 16s Ã¢â€ â€™ 30s (capped) between attempts.
    """
    import asyncio
    await asyncio.sleep(2)  # Let uvicorn finish binding

    if not BRIDGE_URL:
        print("[Startup] BRIDGE_URL not set Ã¢â‚¬â€ skipping Telegram warm-up. SendQueue inactive.")
        return

    headers = {"X-Internal-Secret": INTERNAL_SECRET} if INTERNAL_SECRET else {}
    attempt = 0
    while True:
        try:
            resp = await _get_bridge_http().post(
                f"{BRIDGE_URL}/api/forward",
                json={"method": "getMe", "params": {}},
                headers=headers,
                timeout=15.0,
            )
            if resp.status_code == 200 and resp.json().get("ok"):
                name = resp.json().get("result", {}).get("first_name", "Bot")
                print(f"[Startup] Telegram warm-up OK via bridge Ã¢â‚¬â€ connected as '{name}'.")
                try:
                    from services.send_queue import get_send_queue
                    # api_base is unused when BRIDGE_URL is set, but configure() must be called
                    get_send_queue().configure(f"bridge:{BRIDGE_URL}")
                    print("[Startup] SendQueue configured and ready.")
                except Exception as _sq_e:
                    print(f"[Startup] SendQueue configure warning (non-fatal): {_sq_e!r}")
                return
            print(f"[Startup] Telegram warm-up bridge HTTP {resp.status_code}, retryingÃ¢â‚¬Â¦")
        except Exception as e:
            wait = min(30, 2 ** min(attempt, 4))
            attempt += 1
            print(f"[Startup] Telegram warm-up attempt {attempt} failed ({type(e).__name__}), retry in {wait}sÃ¢â‚¬Â¦")
            await asyncio.sleep(wait)



def _backfill_price_rm():
    """
    Phase 0 critical fix: add price_rm to any menu item that is missing it.
    Estimates from profit_margin_rm using formula: price = margin / 0.60.
    Uses a schema version flag to skip on subsequent startups (performance fix).
    """
    try:
        db = load_database()
        if db.get("_schema_version", 0) >= 1:
            # Already at current schema Ã¢â‚¬â€ no write needed.
            return
        changed = False
        for restaurant in db.get("restaurants", []):
            for item in restaurant.get("menu", []):
                if "price_rm" not in item:
                    pm = item.get("profit_margin_rm", 0)
                    item["price_rm"] = round(pm / 0.60, 2) if pm > 0 else 5.00
                    changed = True
                if "halal_certified" not in item:
                    item["halal_certified"] = True
                    changed = True
                if "allergens" not in item:
                    item["allergens"] = []
                    changed = True
                if "description" not in item:
                    item["description"] = ""
                    changed = True
        db["_schema_version"] = 1
        save_database(db)
        if changed:
            print("[Startup] Backfilled missing menu fields (price_rm, halal, allergens).")
        else:
            print("[Startup] Schema v1: all menu fields present, version flag written.")
    except Exception as e:
        print(f"[Startup] price_rm backfill failed: {e}")




def _backfill_new_restaurant_fields():
    """Add new restaurant-level fields introduced in v9.0 to existing restaurants."""
    try:
        db = load_database()
        changed = False
        for restaurant in db.get("restaurants", []):
            defaults = {
                "preferred_language": "english",
                "sustainability_totals": {"waste_prevented_kg": 0.0, "co2_saved_kg": 0.0},
                "ingredient_purchases": [],
                "gamification": {
                    "current_streak": 0,
                    "longest_streak": 0,
                    "last_log_date": None,
                    "total_logs": 0,
                    "accuracy_milestones": [],
                },
                "chain_id": None,
                "is_demo": False,
                "q_tables": {},
                "bayesian_beliefs": {},
            }
            for field, default in defaults.items():
                if field not in restaurant:
                    restaurant[field] = default
                    changed = True
        if changed:
            save_database(db)
            print("[Startup] Backfilled new restaurant fields (v9.0).")
    except Exception as e:
        print(f"[Startup] Restaurant field backfill failed: {e }")


class UploadPayload(BaseModel):
    restaurant_id: str
    action: str
    menu_mode: str = "none"


class EventPayload(BaseModel):
    description: str = Field(..., min_length=3, max_length=200)
    headcount: int = Field(..., ge=1, le=100_000)
    days: int = Field(1, ge=1, le=30)


class MenuItemPayload(BaseModel):
    item: str = Field(..., min_length=1, max_length=100)
    base_daily_demand: int = Field(50, ge=1, le=10_000)
    profit_margin_rm: float = Field(2.50, ge=0.10, le=500.0)
    price_rm: float = Field(5.00, ge=0.10, le=1000.0)
    halal_certified: bool = Field(True)
    allergens: List[str] = Field(default_factory=list)
    description: str = Field("", max_length=300)


class MarketplaceAuthPayload(BaseModel):
    email: str = Field(..., min_length=5, max_length=200)
    password: str = Field(..., min_length=8, max_length=200)
    name: str = Field("", max_length=100)
    phone: str = Field("", max_length=20)


class ClosingTimePayload(BaseModel):
    closing_time: str = Field(..., pattern=r'^\d{2}:\d{2}$')
    discount_pct: int = Field(30, ge=5, le=70)
    marketplace_enabled: bool = Field(True)


class CustomerOrderPayload(BaseModel):
    restaurant_id: str = Field(...)
    customer_name: str = Field(..., min_length=1, max_length=100)
    phone: str = Field(..., min_length=5, max_length=20)
    items: List[dict] = Field(...)
    pickup_notes: str = Field("", max_length=200)
    ref_token: Optional[str] = None


class MarketplaceItemUpdate(BaseModel):
    listed: Optional[bool] = None
    price_rm: Optional[float] = Field(None, ge=0.10, le=9999)
    discount_pct: Optional[int] = Field(None, ge=0, le=70)


class DemoVerifyOtpPayload(BaseModel):
    username: str = Field(..., min_length=1, max_length=50)
    otp: str = Field(..., min_length=6, max_length=6)


def _validate_rest_id(restaurant_id: str) -> None:
    """Validate restaurant_id format to prevent path traversal and injection."""
    if not restaurant_id or not isinstance(restaurant_id, str):
        raise HTTPException(status_code=400, detail="restaurant_id is required.")
    if len(restaurant_id) > 64:
        raise HTTPException(status_code=400, detail="Invalid restaurant_id.")
    if not re.match(r'^[a-zA-Z0-9_\-]+$', restaurant_id):
        raise HTTPException(status_code=400, detail="Invalid restaurant_id format.")


@app.get("/")
def root():
    bot_username = os.environ.get("BOT_USERNAME", "WasteWise_bot")
    return {
        "status": "ok",
        "service": "WasteWise AI",
        "version": "9.0.0",
        "bot_username": bot_username,
    }


@app.get("/favicon.ico", include_in_schema=False)
def favicon():
    from fastapi.responses import Response

    return Response(status_code=204)


@app.get("/robots.txt", include_in_schema=False)
def robots_txt():
    """Prevent search crawler 404 noise in production logs."""
    from fastapi.responses import PlainTextResponse

    return PlainTextResponse("User-agent: *\nDisallow: /api/\n")


@app.get("/api/health")
def health_check():
    """
    Production health endpoint.
    Returns status of all subsystems: DB, cache, task queue, Supabase, push worker.
    Supabase connectivity is cached for 60 seconds to avoid burning a connection slot
    on every cron ping (health is called twice daily by the keep-alive cron job).
    """
    from services.cache_layer import cache_health, cache_get, cache_set
    from services.task_queue import queue_health
    from services.supabase_db import get_last_push_ts
    import time

    supabase_ok = cache_get("health:supabase")
    if supabase_ok is None:
        try:
            from services.supabase_db import _sb

            if _sb:
                _sb.table("restaurants").select("id").limit(1).execute()
                supabase_ok = True
            else:
                supabase_ok = False
        except Exception:
            supabase_ok = False
        cache_set("health:supabase", supabase_ok, ttl=60)

    db_ok = False
    try:
        db = load_database()
        db_ok = isinstance(db, dict)
    except Exception:
        pass

    last_push = get_last_push_ts()
    push_age = round(time.monotonic() - last_push) if last_push else None

    return {
        "status": "ok" if db_ok else "degraded",
        "version": "9.0.0",
        "supabase": "ok" if supabase_ok else "unavailable (JSON fallback active)",
        "cache": cache_health(),
        "task_queue": queue_health(),
        "database": "ok" if db_ok else "error",
        "push_worker": {
            "last_push_seconds_ago": push_age,
            "status": "ok" if push_age is not None else "no_push_yet",
        },
    }


@app.get("/api/bot_info")
async def get_bot_info():
    """
    Return the Telegram bot username.
    Priority: 1) BOT_USERNAME env var  2) live call via bridge  3) safe default
    """
    env_username = os.environ.get("BOT_USERNAME", "").strip()
    if env_username:
        return {"bot_username": env_username, "source": "env"}

    if BRIDGE_URL:
        try:
            headers = {"X-Internal-Secret": INTERNAL_SECRET} if INTERNAL_SECRET else {}
            r = await _get_bridge_http().post(
                f"{BRIDGE_URL}/api/forward",
                json={"method": "getMe", "params": {}},
                headers=headers,
                timeout=10.0,
            )
            if r.status_code == 200 and r.json().get("ok"):
                username = r.json().get("result", {}).get("username", "")
                if username:
                    return {"bot_username": username, "source": "bridge"}
        except Exception:
            pass

    return {"bot_username": "WasteWise_bot", "source": "default"}


@app.get("/api/restaurants")
def get_restaurants():
    db = load_database()
    return [
        {"id": r["id"], "name": r["name"], "region": r["region"]} for r in db.get("restaurants", [])
    ]



@app.get("/api/dashboard/{restaurant_id}/stream")
async def stream_dashboard(
    restaurant_id: str, 
    user: AuthenticatedUser = Depends(require_auth)
):
    _validate_rest_id(restaurant_id)
    if user.restaurant_id != restaurant_id and user.email != "admin":
        raise HTTPException(status_code=403, detail="Not authorized for this restaurant")

    from fastapi.responses import StreamingResponse
    from services.sse_broadcaster import sse_generator
    return StreamingResponse(sse_generator(restaurant_id), media_type="text/event-stream")

def notify_dashboard_update(restaurant_id: str):
    try:
        from services.sse_broadcaster import publish_refresh
        publish_refresh(restaurant_id)
    except Exception:
        pass

@app.get("/api/dashboard/{restaurant_id}")
async def get_dashboard(
    restaurant_id: str,
    user: AuthenticatedUser = Depends(require_auth),
):
    await asyncio.to_thread(require_restaurant_access, restaurant_id, user)
    db = await asyncio.to_thread(load_database)

    restaurant = _get_restaurant(db, restaurant_id)
    if not restaurant:
        raise HTTPException(status_code=404, detail="Restaurant not found.")
    forecast_text = await asyncio.to_thread(generate_morning_forecast, restaurant_id)
    return {
        "restaurant": {
            "id": restaurant["id"],
            "name": restaurant["name"],
            "region": restaurant["region"],
            "menu": restaurant.get("menu", []),
            "active_events": restaurant.get("active_events", []),
        },
        "region_info": db.get("regions", {}).get(restaurant["region"], {}),
        "ai_forecast_message": forecast_text,
        "accuracy_data": get_accuracy_data(restaurant_id),
    }


@app.post("/api/upload")
def upload_text(
    payload: UploadPayload,
    user: AuthenticatedUser = Depends(require_auth),
):
    require_restaurant_access(payload.restaurant_id, user)
    if not payload.action or not payload.action.strip():
        raise HTTPException(status_code=400, detail="No data provided.")
    if payload.menu_mode not in ("none", "append", "overwrite"):
        raise HTTPException(status_code=400, detail="menu_mode must be none/append/overwrite.")

    result = process_ai_data_ingestion(
        payload.restaurant_id, payload.action, payload.menu_mode
    )
    notify_dashboard_update(payload.restaurant_id)
    return {
        "status": "success",
        "message": result,
    }


@app.post("/api/upload_file")
async def upload_file(
    restaurant_id: str = Form(...),
    menu_mode: str = Form("none"),
    file: UploadFile = File(...),
    user: AuthenticatedUser = Depends(require_auth),
):
    require_restaurant_access(restaurant_id, user)
    if menu_mode not in ("none", "append", "overwrite"):
        raise HTTPException(status_code=400, detail="Invalid menu_mode.")

    content = await file.read()
    if len(content) > 5_000_000:
        raise HTTPException(status_code=400, detail="File too large. Max 5MB.")
    if not content:
        raise HTTPException(status_code=400, detail="Uploaded file is empty.")

    filename = file.filename or "upload"
    text, fmt = process_upload(filename, content)

    if fmt == "image":
        mime = extract_image_mime(filename)
        result = process_image_upload(restaurant_id, content, mime)
        notify_dashboard_update(restaurant_id)
        return {"status": "success", "message": f"Ã°Å¸â€œÂ· {result }"}

    if not text.strip():
        raise HTTPException(status_code=400, detail="Could not extract text from file.")

    result = process_ai_data_ingestion(restaurant_id, text, menu_mode)
    notify_dashboard_update(restaurant_id)
    return {
        "status": "success",
        "message": f"Ã°Å¸â€œâ€ž Processed '{filename }' ({fmt .upper ()})\n{result }",
    }


@app.delete("/api/menu/{restaurant_id}/{item_name}")
def delete_menu_item(
    restaurant_id: str,
    item_name: str,
    background_tasks: BackgroundTasks,
    user: AuthenticatedUser = Depends(require_auth),
):
    require_restaurant_access(restaurant_id, user)
    db = load_database()
    restaurant = _get_restaurant(db, restaurant_id)
    if not restaurant:
        raise HTTPException(status_code=404, detail="Restaurant not found.")
    original = len(restaurant.get("menu", []))
    restaurant["menu"] = [
        m for m in restaurant.get("menu", []) if m["item"].lower() != item_name.lower()
    ]
    if len(restaurant["menu"]) == original:
        raise HTTPException(status_code=404, detail=f"Item not found on menu.")
    invalidate_forecast(restaurant_id)
    invalidate_intelligence(restaurant_id)
    invalidate_db()
    save_database(db)
    background_tasks.add_task(_do_generate_forecast, restaurant_id)
    notify_dashboard_update(restaurant_id)
    return {"status": "success", "message": f"'{item_name }' deleted. Forecast regenerating."}


@app.post("/api/menu/{restaurant_id}")
def add_menu_item(
    restaurant_id: str,
    payload: MenuItemPayload,
    background_tasks: BackgroundTasks,
    user: AuthenticatedUser = Depends(require_auth),
):
    require_restaurant_access(restaurant_id, user)
    db = load_database()
    restaurant = _get_restaurant(db, restaurant_id)
    if not restaurant:
        raise HTTPException(status_code=404, detail="Restaurant not found.")
    existing = {m["item"].lower() for m in restaurant.get("menu", [])}
    if payload.item.lower() in existing:
        raise HTTPException(status_code=400, detail=f"'{payload .item }' already exists.")
    restaurant.setdefault("menu", []).append(
        {
            "item": validate_item_name(payload.item),
            "base_daily_demand": payload.base_daily_demand,
            "profit_margin_rm": payload.profit_margin_rm,
            "price_rm": payload.price_rm,
            "halal_certified": payload.halal_certified,
            "allergens": payload.allergens,
            "description": sanitise_text(payload.description, 300),
        }
    )
    invalidate_forecast(restaurant_id)
    invalidate_intelligence(restaurant_id)
    invalidate_db()
    save_database(db)
    background_tasks.add_task(_do_generate_forecast, restaurant_id)
    notify_dashboard_update(restaurant_id)
    return {"status": "success", "message": f"'{payload .item }' added. Forecast regenerating."}


@app.post("/api/event/{restaurant_id}")
def add_event(
    restaurant_id: str,
    payload: EventPayload,
    background_tasks: BackgroundTasks,
    user: AuthenticatedUser = Depends(require_auth),
):
    require_restaurant_access(restaurant_id, user)
    message = register_owner_event(
        restaurant_id, sanitise_text(payload.description, 200), payload.headcount, payload.days
    )
    invalidate_forecast(restaurant_id)
    background_tasks.add_task(_do_generate_forecast, restaurant_id)
    notify_dashboard_update(restaurant_id)
    return {"status": "success", "message": message}


@app.get("/api/accuracy/{restaurant_id}")
def get_accuracy(
    restaurant_id: str,
    user: AuthenticatedUser = Depends(require_auth),
):
    _validate_rest_id(restaurant_id)
    require_restaurant_access(restaurant_id, user)
    return {"accuracy_data": get_accuracy_data(restaurant_id)}


@app.get("/api/shopping_list/{restaurant_id}")
def get_shopping_list_endpoint(
    restaurant_id: str,
    user: AuthenticatedUser = Depends(require_auth),
):
    """Today's ingredient shopping list based on forecast quantities."""
    _validate_rest_id(restaurant_id)
    require_restaurant_access(restaurant_id, user)
    db = load_database()
    restaurant = _get_restaurant(db, restaurant_id)
    if not restaurant:
        raise HTTPException(status_code=404, detail="Restaurant not found.")
    items = get_shopping_list(restaurant_id)
    return {"shopping_list": items, "date": get_today(restaurant).isoformat()}


@app.post("/api/bom/{restaurant_id}/{item_name}")
def set_item_bom(
    restaurant_id: str,
    item_name: str,
    bom: dict,
    background_tasks: BackgroundTasks,
    user: AuthenticatedUser = Depends(require_auth),
):
    """Owner defines ingredient ratios for a menu item."""
    _validate_rest_id(restaurant_id)
    require_restaurant_access(restaurant_id, user)
    # Size validation: prevent DoS via oversized BOM payloads
    if len(bom) > 50:
        raise HTTPException(status_code=400, detail="BOM cannot have more than 50 ingredients.")
    for k, v in bom.items():
        if not isinstance(k, str) or len(k) > 100:
            raise HTTPException(status_code=400, detail="BOM ingredient name too long (max 100 chars).")
        if not isinstance(v, (int, float)) or v < 0:
            raise HTTPException(status_code=400, detail="BOM values must be non-negative numbers.")
    db = load_database()
    restaurant = _get_restaurant(db, restaurant_id)
    if not restaurant:
        raise HTTPException(status_code=404, detail="Restaurant not found.")
    restaurant.setdefault("bom", {})[item_name] = bom
    invalidate_forecast(restaurant_id)
    save_database(db)
    background_tasks.add_task(_do_generate_forecast, restaurant_id)
    notify_dashboard_update(restaurant_id)
    return {"status": "success", "item": item_name, "bom": bom}



@app.get("/api/bom/{restaurant_id}")
def get_bom(
    restaurant_id: str,
    user: AuthenticatedUser = Depends(require_auth),
):
    """Return all owner-defined BOM ratios for a restaurant."""
    _validate_rest_id(restaurant_id)
    require_restaurant_access(restaurant_id, user)
    db = load_database()
    restaurant = _get_restaurant(db, restaurant_id)
    if not restaurant:
        raise HTTPException(status_code=404, detail="Restaurant not found.")
    return {"bom": restaurant.get("bom", {})}


@app.get("/api/accuracy_notes/{restaurant_id}")
def get_accuracy_notes(
    restaurant_id: str,
    user: AuthenticatedUser = Depends(require_auth),
):
    """Return plain-English actionable notes for low-accuracy items."""
    _validate_rest_id(restaurant_id)
    require_restaurant_access(restaurant_id, user)
    from services.data_miner import compute_mape_per_item, actionable_accuracy_notes

    db = load_database()
    restaurant = _get_restaurant(db, restaurant_id)
    if not restaurant:
        raise HTTPException(status_code=404, detail="Restaurant not found.")
    mape_data = compute_mape_per_item(restaurant)
    notes = actionable_accuracy_notes(mape_data, restaurant)
    return {"notes": notes}


@app.post("/api/auth/register")
@limiter.limit("5/minute")
async def api_register(
    request: Request,
    email: Optional[str] = None,
    name: Optional[str] = None,
    owner_name: Optional[str] = None,
    region: Optional[str] = None,
    restaurant_type: Optional[str] = None,
    telegram_username: Optional[str] = None,
    closing_time: str = "21:00",
):
    if not email or "@" not in email:
        raise HTTPException(status_code=400, detail="Valid email required.")
    if not name or not region:
        raise HTTPException(status_code=400, detail="Restaurant name and region required.")
    if not telegram_username:
        raise HTTPException(status_code=400, detail="Telegram username required.")
    tg_username = telegram_username.lstrip("@").lower()

    is_email_registered = await asyncio.to_thread(auth.email_registered, email)
    if is_email_registered:
        raise HTTPException(
            status_code=409, detail="This email is already registered. Please sign in instead."
        )
    is_pending_email = await asyncio.to_thread(auth.pending_email_registered, email)
    if is_pending_email:
        raise HTTPException(
            status_code=409,
            detail="A registration for this email is already in progress. Check your Telegram to complete it.",
        )
    db = await asyncio.to_thread(load_database)
    chat_id = None
    for r in db.get("restaurants", []):
        if r.get("telegram_username", "").lower() == tg_username and r.get("telegram_chat_id"):
            chat_id = r["telegram_chat_id"]
            break
    if not chat_id:
        for acc in db.get("accounts", []):
            for s in acc.get("sessions", []):
                if s.get("telegram_username", "").lower() == tg_username and s.get("chat_id"):
                    chat_id = s["chat_id"]
                    break
    is_telegram_registered = False
    if chat_id:
        is_telegram_registered = await asyncio.to_thread(auth.telegram_registered, chat_id)
    if chat_id and is_telegram_registered:
        raise HTTPException(
            status_code=409, detail="This Telegram account is already linked to a restaurant."
        )
    ct = closing_time if re.match(r'^\d{2}:\d{2}$', closing_time or "") else "21:00"
    rest_id = "rest_" + str(uuid.uuid4())[:8]
    new_rest = {
        "id": rest_id,
        "name": name.strip(),
        "region": region.strip(),
        "type": (restaurant_type or "hawker").strip(),
        "owner_name": (owner_name or "Owner").strip(),
        "telegram_chat_id": chat_id,
        "telegram_username": tg_username,
        "privacy_accepted": True,
        "registered_at": datetime.datetime.now().isoformat(),
        "specialty_weather": "neutral",
        "bom": {},
        "menu": [],
        "recent_feedback_memory": [],
        "active_events": [],
        "daily_records": [],
        "closing_time": ct,
        "discount_pct": 30,
        "marketplace_enabled": True,
    }
    if chat_id:
        db.setdefault("restaurants", []).append(new_rest)
        if region not in db.get("regions", {}):
            db.setdefault("regions", {})[region] = {
                "type": "General Area",
                "foot_traffic_baseline": 500,
                "weekend_multiplier": 1.1,
                "holiday_multiplier": 1.0,
                "rain_impact": -0.2,
            }
        await asyncio.to_thread(save_database, db)
        await asyncio.to_thread(auth.create_account, email, rest_id, chat_id, tg_username)
        token = await asyncio.to_thread(auth.add_web_session, email, "Web dashboard")

        try:
            _lat = new_rest.get("latitude") or new_rest.get("lat")
            _lon = new_rest.get("longitude") or new_rest.get("lon")
            if _lat and _lon:
                from services.india_context import trigger_intelligence_gathering

                trigger_intelligence_gathering(rest_id, float(_lat), float(_lon))
        except Exception:
            pass
        return {"status": "registered", "restaurant_id": rest_id, "email": email, "token": token}
    else:
        await asyncio.to_thread(
            auth.create_pending_registration,
            email, tg_username, {"rest_id": rest_id, "new_rest": new_rest, "region": region}
        )
        return {
            "status": "pending_telegram",
            "message": "Please open Telegram and send any message to the WasteWise AI bot to complete registration.",
            "email": email,
        }


@app.post("/api/auth/request_otp")
@limiter.limit("5/minute")
async def api_request_otp(request: Request, email: Optional[str] = None):
    client_ip = get_client_ip(request)
    check_otp_rate_limit(client_ip)

    email = validate_email(email or "")
    import asyncio
    account = await asyncio.to_thread(auth.get_account_by_email, email)
    if not account:
        raise HTTPException(
            status_code=404, detail="Email not registered. Please create an account."
        )
    primary = next(
        (s for s in account.get("sessions", []) if s.get("is_primary") and s.get("chat_id")), None
    )
    if not primary:
        raise HTTPException(
            status_code=400,
            detail="No Telegram account linked. Please verify your account on Telegram.",
        )
    otp = await asyncio.to_thread(auth.create_otp, email, "web_login")
    if True:
        msg = (
            "Ã°Å¸â€Â Your WasteWise AI login code: *" + otp + "*\n\n"
            "Valid for " + str(auth.OTP_TTL_SECONDS) + " seconds. Do not share this."
        )
        await _tg_send(primary["chat_id"], msg)

    return {"status": "otp_sent", "expires_in": auth.OTP_TTL_SECONDS}


@app.post("/api/auth/verify_otp")
@limiter.limit("10/minute")
async def api_verify_otp(request: Request, email: Optional[str] = None, otp: Optional[str] = None):
    client_ip = get_client_ip(request)
    check_otp_rate_limit(client_ip)

    if not email or not otp:
        raise HTTPException(status_code=400, detail="email and otp required.")
    email = validate_email(email)
    otp = validate_otp_code(otp)

    import asyncio
    is_valid = await asyncio.to_thread(auth.verify_otp, email, otp, "web_login")
    if not is_valid:
        record_failed_otp(client_ip)
        raise HTTPException(status_code=401, detail="Invalid or expired OTP.")

    clear_otp_attempts(client_ip)
    account = await asyncio.to_thread(auth.get_account_by_email, email)
    if not account:
        raise HTTPException(status_code=404, detail="Account not found.")
    token = await asyncio.to_thread(auth.add_web_session, email, "Web dashboard")
    return {
        "status": "success",
        "token": token,
        "restaurant_id": account["restaurant_id"],
        "email": email,
    }


@app.post("/api/auth/verify_telegram_otp")
async def api_verify_telegram_otp(
    request: Request,
    chat_id: Optional[int] = None,
    otp: Optional[str] = None,
):
    """Verify OTP sent to Telegram (used during web registration to confirm Telegram username)."""
    if not chat_id or not otp:
        raise HTTPException(status_code=400, detail="chat_id and otp required.")
    
    import asyncio
    is_valid = await asyncio.to_thread(auth.verify_telegram_otp, chat_id, otp, "web_register_verify")
    if not is_valid:
        raise HTTPException(status_code=401, detail="Invalid or expired OTP.")
    return {"status": "verified", "chat_id": chat_id}


@app.get("/api/auth/me")
def api_me(request: Request):
    auth_header = request.headers.get("Authorization", "")
    token = auth_header[7:].strip() if auth_header.startswith("Bearer ") else ""
    if not token:
        raise HTTPException(status_code=401, detail="Authorization header required.")
    info = auth.validate_web_token(token)
    if not info:
        raise HTTPException(status_code=401, detail="Session expired or invalid.")
    auth.get_account_by_email(info["email"])
    sessions = auth.get_sessions_for_account(info["email"])
    
    return {
        "email": info["email"],
        "restaurant_id": info["restaurant_id"],
        "sessions": [
            {
                "session_id": s["session_id"][:8],
                "type": s.get("type"),
                "label": s.get("telegram_username") or s.get("label", ""),
                "is_primary": s.get("is_primary"),
                "expires_at": s.get("expires_at"),
                "last_active": s.get("last_active"),
                "chat_id": s.get("chat_id"),
                "telegram_username": s.get("telegram_username"),
            }
            for s in sessions
        ],
    }


@app.delete("/api/auth/session/{session_prefix}")
def api_remove_session(session_prefix: str, request: Request):
    auth_header = request.headers.get("Authorization", "")
    token = auth_header[7:].strip() if auth_header.startswith("Bearer ") else ""
    if not token:
        raise HTTPException(status_code=401, detail="Authorization header required.")
    info = auth.validate_web_token(token)
    if not info:
        raise HTTPException(status_code=401, detail="Session expired.")
    sessions = auth.get_sessions_for_account(info["email"])
    target = next((s for s in sessions if s["session_id"].startswith(session_prefix)), None)
    if not target:
        raise HTTPException(status_code=404, detail="Session not found.")
    if target.get("is_primary"):
        raise HTTPException(status_code=400, detail="Cannot remove the primary session.")
    removed = auth.remove_session(info["email"], target["session_id"])
    if removed:
        from services.sse_broadcaster import publish_refresh
        publish_refresh(info["restaurant_id"], f"logout_session:{session_prefix}")
    return {"status": "removed" if removed else "not_found"}


@app.post("/api/auth/unlink_telegram")
async def api_unlink_telegram(request: Request):
    auth_header = request.headers.get("Authorization", "")
    token = auth_header[7:].strip() if auth_header.startswith("Bearer ") else ""
    if not token:
        raise HTTPException(status_code=401, detail="Authorization header required.")
    info = auth.validate_web_token(token)
    if not info:
        raise HTTPException(status_code=401, detail="Session expired.")
        
    sessions = auth.get_sessions_for_account(info["email"])
    caller = next((s for s in sessions if s.get("session_id") == token), None)
    if not caller or not caller.get("chat_id"):
        raise HTTPException(status_code=400, detail="No Telegram account linked to this session.")
        
    chat_id = caller.get("chat_id")
    username = caller.get("telegram_username")
    
    # Remove link from all sessions sharing this chat_id
    db = auth._load()
    account = next((a for a in db.get("accounts", []) if a["email"].lower() == info["email"].lower()), None)
    if account:
        for s in account.get("sessions", []):
            if s.get("chat_id") == chat_id:
                s.pop("chat_id", None)
                s.pop("telegram_username", None)
                s["is_primary"] = False
        
        auth._save(db)
        
    # Also wipe it from the volatile memory of the Telegram Bridge
    if username and BRIDGE_URL:
        import httpx
        try:
            bridge_headers = {"X-Internal-Secret": INTERNAL_SECRET} if INTERNAL_SECRET else {}
            async with httpx.AsyncClient() as client:
                await client.post(
                    f"{BRIDGE_URL}/api/demo/link",
                    json={"action": "unlink", "username": username},
                    headers=bridge_headers,
                    timeout=2.0
                )
        except Exception as e:
            print(f"[Unlink] Failed to clear bridge memory: {e}")
    
    return {"message": "Telegram unlinked successfully"}



@app.patch("/api/auth/session/{session_prefix}/make_primary")
def api_make_session_primary(session_prefix: str, request: Request):
    """
    Transfer primary status to a different already-linked Telegram session.
    """
    auth_header = request.headers.get("Authorization", "")
    token = auth_header[7:].strip() if auth_header.startswith("Bearer ") else ""
    if not token:
        raise HTTPException(status_code=401, detail="Authorization header required.")
    info = auth.validate_web_token(token)
    if not info:
        raise HTTPException(status_code=401, detail="Session expired.")

    db = load_database()
    account = next(
        (a for a in db.get("accounts", []) if a["email"].lower() == info["email"].lower()), None
    )
    if not account:
        raise HTTPException(status_code=404, detail="Account not found.")

    sessions = account.get("sessions", [])

    caller_session = next((s for s in sessions if s["session_id"] == token), None)
    if not caller_session or not caller_session.get("is_primary"):
        raise HTTPException(
            status_code=403, detail="Only the primary Telegram account can transfer primary status."
        )

    target = next((s for s in sessions if s["session_id"].startswith(session_prefix)), None)
    if not target:
        raise HTTPException(status_code=404, detail="Session not found.")
    if target.get("session_id") == caller_session.get("session_id"):
        raise HTTPException(status_code=400, detail="This session is already primary.")
    if target.get("type") != "telegram":
        raise HTTPException(status_code=400, detail="Only Telegram sessions can be made primary.")

    for s in sessions:
        s["is_primary"] = s["session_id"] == target["session_id"]

    save_database(db)
    return {
        "status": "primary_transferred",
        "new_primary_session": target["session_id"][:8],
        "new_primary_telegram": target.get("telegram_username", ""),
    }


@app.delete("/api/auth/account")
def api_delete_account(request: Request, user: AuthenticatedUser = Depends(require_auth)):
    if not restaurant:
        raise HTTPException(status_code=404, detail="Restaurant not found.")
    if not restaurant.get("marketplace_enabled", True):
        raise HTTPException(status_code=404, detail="Marketplace not enabled for this store.")

    closing_time = restaurant.get("closing_time", "")
    dyn = get_dynamic_discount(closing_time, restaurant.get("discount_pct", 30))
    menu = get_marketplace_menu(restaurant)

    today_str = get_today(restaurant).isoformat()
    today_orders = [
        o
        for o in restaurant.get("marketplace_orders", [])
        if o.get("date") == today_str and o.get("status") != "cancelled"
    ]

    if restaurant.get("closing_stock_date") == today_str:
        ordered_qtys: dict = {}
        for order in today_orders:
            for oi in order.get("items", []):
                key = oi.get("item", "").lower()
                ordered_qtys[key] = ordered_qtys.get(key, 0) + oi.get("qty", 0)
        menu = [
            {
                **m,
                "qty_available": max(
                    0, (m["qty_available"] or 0) - ordered_qtys.get(m["item"].lower(), 0)
                ),
            }
            for m in menu
            if (
                m["qty_available"] is None
                or m["qty_available"] - ordered_qtys.get(m["item"].lower(), 0) > 0
            )
        ]

    return {
        "id": restaurant_id,
        "name": restaurant["name"],
        "region": restaurant.get("region", "India"),
        "type": restaurant.get("type", "hawker"),
        "closing_time": closing_time,
        "discount_pct": dyn["discount_pct"],
        "discount_label": dyn["label"],
        "urgency": dyn["urgency"],
        "minutes_to_close": dyn["minutes_to_close"],
        "menu": menu,
        "is_closing_stock": restaurant.get("closing_stock_date") == today_str,
        "orders_today": len(today_orders),
    }


@app.post("/api/marketplace/auth/register")
@limiter.limit("10/minute")
async def marketplace_register(request: Request, payload: MarketplaceAuthPayload):
    from services.marketplace_auth import register_customer
    from services.email_service import send_welcome_email

    if not payload.name.strip():
        raise HTTPException(400, "Name is required.")
    try:
        user = register_customer(
            email=payload.email.strip().lower(),
            password=payload.password,
            name=payload.name.strip(),
            phone=payload.phone.strip(),
        )
        send_welcome_email(user["email"], user["name"])
        return {"success": True, "message": "Account created! You can now log in."}
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.post("/api/marketplace/auth/login")
@limiter.limit("20/minute")
async def marketplace_login(request: Request, payload: MarketplaceAuthPayload):
    from services.marketplace_auth import login_customer

    try:
        return login_customer(email=payload.email.strip().lower(), password=payload.password)
    except ValueError as e:
        raise HTTPException(401, str(e))


@app.delete("/api/marketplace/auth/delete_account")
async def marketplace_delete_account(request: Request):
    from services.marketplace_auth import validate_customer_token, delete_customer_account
    from services.email_service import send_account_deletion_confirmation

    token = request.headers.get("Authorization", "").replace("Bearer ", "").strip()
    customer = validate_customer_token(token)
    if not customer:
        raise HTTPException(401, "Not authenticated.")
    try:
        delete_customer_account(customer["user_id"], token)
        send_account_deletion_confirmation(customer["email"], customer.get("name", ""))
        return {"success": True, "message": "Account permanently deleted."}
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.get("/api/restaurant/{restaurant_id}/sustainability")
def get_sustainability(
    restaurant_id: str,
    user: AuthenticatedUser = Depends(require_auth),
):
    _validate_rest_id(restaurant_id)
    require_restaurant_access(restaurant_id, user)
    from services.sustainability import get_lifetime_sustainability_totals

    db = load_database()
    restaurant = _get_restaurant(db, restaurant_id)
    if not restaurant:
        raise HTTPException(404, "Restaurant not found.")
    return {"restaurant_id": restaurant_id, **get_lifetime_sustainability_totals(restaurant)}


@app.get("/api/restaurant/{restaurant_id}/gamification")
def get_gamification(
    restaurant_id: str,
    user: AuthenticatedUser = Depends(require_auth),
):
    _validate_rest_id(restaurant_id)
    require_restaurant_access(restaurant_id, user)
    db = load_database()
    restaurant = _get_restaurant(db, restaurant_id)
    if not restaurant:
        raise HTTPException(404, "Restaurant not found.")
    gam = restaurant.get("gamification", {})
    return {
        "current_streak": gam.get("current_streak", 0),
        "longest_streak": gam.get("longest_streak", 0),
        "total_logs": gam.get("total_logs", 0),
        "accuracy_milestones": gam.get("accuracy_milestones", []),
        "last_log_date": gam.get("last_log_date"),
    }


@app.get("/api/location/autocomplete")
def location_autocomplete(q: str, lat: float = 3.1390, lon: float = 101.6869):
    if len(q) < 3:
        return {"results": []}
    from services.location_intel import autocomplete_address

    return {"results": autocomplete_address(q, lat, lon)}


@app.get("/api/location/weather")
def get_weather_endpoint(lat: float, lon: float):
    from services.location_intel import get_weather_forecast

    weather = get_weather_forecast(lat, lon)
    if not weather:
        raise HTTPException(503, "Weather service unavailable.")
    return weather


@app.get("/api/location/prayer_times")
def get_prayer_times_endpoint(lat: float, lon: float):
    from services.location_intel import get_prayer_times

    times = get_prayer_times(lat, lon)
    if not times:
        raise HTTPException(503, "Prayer times service unavailable.")
    return times


@app.patch("/api/restaurant/{restaurant_id}/language")
def set_language_preference(
    restaurant_id: str,
    language: str,
    user: AuthenticatedUser = Depends(require_auth),
):
    _validate_rest_id(restaurant_id)
    require_restaurant_access(restaurant_id, user)
    valid = {"english", "malay", "mandarin", "tamil"}
    if language not in valid:
        raise HTTPException(400, f"Language must be one of: {', '.join (valid )}")
    db = load_database()
    restaurant = _get_restaurant(db, restaurant_id)
    if not restaurant:
        raise HTTPException(404, "Restaurant not found.")
    restaurant["preferred_language"] = language
    save_database(db)
    return {"success": True, "preferred_language": language}


@app.get("/api/restaurant/{restaurant_id}/causal_analysis")
def causal_analysis(
    restaurant_id: str,
    target_date: Optional[str] = None,
    current_user: AuthenticatedUser = Depends(require_auth),
):
    """Explain WHY a specific date underperformed. Returns causal breakdown."""
    _validate_rest_id(restaurant_id)
    require_restaurant_access(restaurant_id, current_user)
    db = load_database()
    restaurant = _get_restaurant(db, restaurant_id)
    if not restaurant:
        raise HTTPException(404, "Restaurant not found.")
    if not target_date:
        if restaurant.get("daily_records"):
            target_date = sorted(restaurant["daily_records"], key=lambda x: x.get("date", ""))[-1].get("date")
        else:
            target_date = (get_today(restaurant) - datetime.timedelta(days=1)).isoformat()
    try:
        datetime.date.fromisoformat(target_date)
    except ValueError:
        raise HTTPException(400, "Invalid date format. Use YYYY-MM-DD.")
    return analyse_underperformance(restaurant, target_date)


@app.get("/api/restaurant/{restaurant_id}/causal_analysis/telegram")
def causal_analysis_telegram(
    restaurant_id: str,
    target_date: Optional[str] = None,
    current_user: AuthenticatedUser = Depends(require_auth),
):
    """Returns causal analysis formatted as a Telegram message."""
    _validate_rest_id(restaurant_id)
    require_restaurant_access(restaurant_id, current_user)
    db = load_database()
    restaurant = _get_restaurant(db, restaurant_id)
    if not restaurant:
        raise HTTPException(404, "Restaurant not found.")
    if not target_date:
        if restaurant.get("daily_records"):
            target_date = sorted(restaurant["daily_records"], key=lambda x: x.get("date", ""))[-1].get("date")
        else:
            target_date = (get_today(restaurant) - datetime.timedelta(days=1)).isoformat()
    report = format_causal_report_telegram(restaurant, target_date)
    return {"report": report, "target_date": target_date}


@app.get("/api/restaurant/{restaurant_id}/menu_engineering")
def menu_engineering(
    restaurant_id: str,
    current_user: AuthenticatedUser = Depends(require_auth),
):
    """BCG/Menu Engineering matrix: Stars, Ploughhorses, Puzzles, Dogs + AI recommendations."""
    _validate_rest_id(restaurant_id)
    require_restaurant_access(restaurant_id, current_user)
    db = load_database()
    restaurant = _get_restaurant(db, restaurant_id)
    if not restaurant:
        raise HTTPException(404, "Restaurant not found.")
    if len(restaurant.get("menu", [])) == 0:
        raise HTTPException(400, "No menu items. Add your menu first.")
    classification = classify_menu_items(restaurant)
    recommendations = generate_menu_recommendations(restaurant)
    return {
        "classification": classification,
        "recommendations": recommendations,
        "data_days": len(restaurant.get("daily_records", [])),
        "note": (
            "Recommendations improve with more daily sales data."
            if len(restaurant.get("daily_records", [])) < 14
            else None
        ),
    }


@app.get("/api/restaurant/{restaurant_id}/menu_engineering/weekly_report")
def menu_engineering_weekly_report(
    restaurant_id: str,
    current_user: AuthenticatedUser = Depends(require_auth),
):
    """Weekly menu engineering report formatted for Telegram."""
    _validate_rest_id(restaurant_id)
    require_restaurant_access(restaurant_id, current_user)
    db = load_database()
    restaurant = _get_restaurant(db, restaurant_id)
    if not restaurant:
        raise HTTPException(404, "Restaurant not found.")
    language = restaurant.get("preferred_language", "english")
    report = get_weekly_menu_report_telegram(restaurant, language)
    if not report:
        return {
            "report": None,
            "reason": "Insufficient data ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Â need at least 7 days of sales records.",
        }
    return {"report": report}


class CreateChainPayload(BaseModel):
    chain_name: str = Field(..., min_length=2, max_length=80)
    chain_type: str = Field(default="franchise")


class AddBranchPayload(BaseModel):
    restaurant_id: str


class PushMenuPayload(BaseModel):
    menu: List[dict]


@app.get("/api/chains")
def list_my_chains(current_user: AuthenticatedUser = Depends(require_auth)):
    """List all chains owned by the current user, with branch summaries."""
    db = load_database()
    my_chains = [
        c
        for c in db.get("chains", [])
        if c.get("owner_email", "").lower() == current_user.email.lower()
    ]
    result = []
    for chain in my_chains:
        cid = chain["chain_id"]
        branches = [
            {"id": r["id"], "name": r["name"], "region": r.get("region", "")}
            for r in db.get("restaurants", [])
            if r.get("chain_id") == cid
        ]
        result.append(
            {
                "chain_id": cid,
                "chain_name": chain.get("name", ""),
                "chain_type": chain.get("chain_type", "franchise"),
                "branch_count": len(branches),
                "branches": branches,
                "created_at": chain.get("created_at", ""),
            }
        )
    return {"chains": result, "total": len(result)}



_pending_dashboard_approvals: dict = {}
_approvals_lock = threading.Lock()


@app.post("/api/auth/dashboard_action/request")
async def request_dashboard_action_approval(
    action: str,
    restaurant_id: Optional[str] = None,
    chain_id: Optional[str] = None,
    user: AuthenticatedUser = Depends(require_auth),
):
    """
    Request primary Telegram approval for a destructive dashboard action.
    Actions: 'delete_restaurant', 'delete_chain', 'create_chain', 'add_branch', 'remove_branch'
    Returns an approval_token that the dashboard polls until approved/denied.
    """
    valid_actions = {
        "delete_restaurant",
        "delete_chain",
        "create_chain",
        "add_branch",
        "remove_branch",
    }
    if action not in valid_actions:
        raise HTTPException(400, f"action must be one of: {', '.join (valid_actions )}")

    account = auth.get_account_by_email(user.email)
    if not account:
        raise HTTPException(404, "Account not found.")
    primary = next(
        (s for s in account.get("sessions", []) if s.get("is_primary") and s.get("chat_id")), None
    )
    if not primary:
        raise HTTPException(400, "No primary Telegram linked. Connect Telegram first.")

    approval_token = secrets.token_urlsafe(16)
    expires_at = (datetime.datetime.utcnow() + datetime.timedelta(minutes=10)).isoformat()

    with _approvals_lock:
        _pending_dashboard_approvals[approval_token] = {
            "action": action,
            "restaurant_id": restaurant_id,
            "chain_id": chain_id,
            "email": user.email,
            "status": "pending",
            "expires_at": expires_at,
            "primary_chat_id": primary["chat_id"],
        }

    action_labels = {
        "delete_restaurant": "ÃƒÂ°Ã…Â¸Ã¢â‚¬â€Ã¢â‚¬ËœÃƒÂ¯Ã‚Â¸Ã‚Â Delete restaurant",
        "delete_chain": "ÃƒÂ°Ã…Â¸Ã¢â‚¬â€Ã¢â‚¬ËœÃƒÂ¯Ã‚Â¸Ã‚Â Delete entire chain",
        "create_chain": "ÃƒÂ°Ã…Â¸Ã¢â‚¬ÂÃ¢â‚¬â€ Create new chain",
        "add_branch": "ÃƒÂ¢Ã…Â¾Ã¢â‚¬Â¢ Add restaurant to chain",
        "remove_branch": "ÃƒÂ¢Ã…Â¾Ã¢â‚¬â€œ Remove branch from chain",
    }
    if True:
        pass

        msg = (
            f"ÃƒÂ°Ã…Â¸Ã¢â‚¬ÂÃ‚Â *Dashboard Action Requires Your Approval*\n\n"
            f"Action: *{action_labels .get (action ,action )}*\n"
            f"Restaurant: `{restaurant_id or chain_id or 'N/A'}`\n\n"
            f"Reply `approve {approval_token [:8 ]}` to allow\n"
            f"Reply `deny {approval_token [:8 ]}` to reject\n\n"
            f"_Expires in 10 minutes_"
        )
        try:
            await _tg_send(primary["chat_id"], msg)
        except Exception:
            pass

    return {"approval_token": approval_token, "expires_in_seconds": 600}


@app.get("/api/auth/dashboard_action/status/{approval_token}")
def check_dashboard_action_status(
    approval_token: str, user: AuthenticatedUser = Depends(require_auth)
):
    """Poll to check if the primary Telegram has approved/denied the action."""
    with _approvals_lock:
        entry = _pending_dashboard_approvals.get(approval_token)
    if not entry:
        raise HTTPException(404, "Approval not found or expired.")
    if entry["email"].lower() != user.email.lower():
        raise HTTPException(403, "Not your approval request.")
    now = datetime.datetime.utcnow().isoformat()
    if entry["expires_at"] < now:
        with _approvals_lock:
            _pending_dashboard_approvals.pop(approval_token, None)
        raise HTTPException(410, "Approval expired.")
    return {"status": entry["status"], "action": entry["action"]}


@app.post("/api/auth/dashboard_action/approve")
def approve_dashboard_action_from_bot(approval_prefix: str, approved: bool, chat_id: int):
    """
    Called internally by the Telegram bot when the primary account types 'approve xxx' or 'deny xxx'.
    Verifies the chat_id is the primary for the account, then marks approval.
    """
    with _approvals_lock:
        match = next(
            (
                (tok, e)
                for tok, e in _pending_dashboard_approvals.items()
                if tok.startswith(approval_prefix) and e["primary_chat_id"] == chat_id
            ),
            None,
        )
    if not match:
        return {"status": "not_found"}
    token, entry = match
    entry["status"] = "approved" if approved else "denied"
    return {"status": entry["status"], "action": entry["action"]}


@app.post("/api/chains")
def create_new_chain(
    payload: CreateChainPayload,
    current_user: AuthenticatedUser = Depends(require_auth),
):
    """Create a new restaurant chain owned by the current user."""
    valid_types = {"franchise", "multi_brand", "food_court"}
    if payload.chain_type not in valid_types:
        raise HTTPException(400, f"chain_type must be one of: {', '.join (valid_types )}")
    chain = create_chain(current_user.email, payload.chain_name.strip(), payload.chain_type)
    db = load_database()
    db.setdefault("chains", []).append(chain)
    save_database(db)
    return {"success": True, "chain": chain}


@app.post("/api/chains/{chain_id}/branches")
def add_branch(
    chain_id: str,
    payload: AddBranchPayload,
    current_user: AuthenticatedUser = Depends(require_auth),
):
    """Add an existing restaurant as a branch of a chain."""
    if not _re.match(r'^chain_[a-f0-9]{8}$', chain_id):
        raise HTTPException(400, "Invalid chain_id format.")
    _validate_rest_id(payload.restaurant_id)
    db = load_database()
    chains = db.get("chains", [])
    chain = next((c for c in chains if c["chain_id"] == chain_id), None)
    if not chain:
        raise HTTPException(404, "Chain not found.")
    if chain.get("owner_email", "").lower() != current_user.email.lower():
        raise HTTPException(403, "You do not own this chain.")
    restaurant = _get_restaurant(db, payload.restaurant_id)
    if not restaurant:
        raise HTTPException(404, "Restaurant not found.")
    if restaurant.get("owner_email", "").lower() != current_user.email.lower():
        raise HTTPException(403, "You do not own this restaurant.")
    ok = add_branch_to_chain(chain_id, payload.restaurant_id, db)
    if not ok:
        raise HTTPException(400, "Could not add branch.")
    save_database(db)
    return {"success": True, "chain_id": chain_id, "restaurant_id": payload.restaurant_id}


@app.get("/api/chains/{chain_id}/summary")
def chain_summary(
    chain_id: str,
    current_user: AuthenticatedUser = Depends(require_auth),
):
    """Get consolidated revenue, waste, and branch stats for a chain."""
    if not _re.match(r'^chain_[a-f0-9]{8}$', chain_id):
        raise HTTPException(400, "Invalid chain_id format.")
    db = load_database()
    chains = db.get("chains", [])
    chain = next((c for c in chains if c["chain_id"] == chain_id), None)
    if not chain:
        raise HTTPException(404, "Chain not found.")
    if chain.get("owner_email", "").lower() != current_user.email.lower():
        raise HTTPException(403, "You do not own this chain.")
    return get_chain_summary(chain_id, db)


@app.post("/api/chains/{chain_id}/push_menu")
def push_menu_to_chain(
    chain_id: str,
    payload: PushMenuPayload,
    current_user: AuthenticatedUser = Depends(require_auth),
):
    """Push a menu template to all branches in the chain."""
    if not _re.match(r'^chain_[a-f0-9]{8}$', chain_id):
        raise HTTPException(400, "Invalid chain_id format.")
    if not payload.menu or len(payload.menu) > 100:
        raise HTTPException(400, "Menu must have 1-100 items.")
    db = load_database()
    chains = db.get("chains", [])
    chain = next((c for c in chains if c["chain_id"] == chain_id), None)
    if not chain:
        raise HTTPException(404, "Chain not found.")
    if chain.get("owner_email", "").lower() != current_user.email.lower():
        raise HTTPException(403, "You do not own this chain.")
    updated = push_menu_template_to_chain(chain_id, payload.menu, db)
    save_database(db)
    return {"success": True, "branches_updated": updated}


@app.get("/api/chains/{chain_id}/telegram_summary")
def chain_telegram_summary(
    chain_id: str,
    current_user: AuthenticatedUser = Depends(require_auth),
):
    """Returns chain daily summary formatted for Telegram."""
    if not _re.match(r'^chain_[a-f0-9]{8}$', chain_id):
        raise HTTPException(400, "Invalid chain_id format.")
    db = load_database()
    chains = db.get("chains", [])
    chain = next((c for c in chains if c["chain_id"] == chain_id), None)
    if not chain:
        raise HTTPException(404, "Chain not found.")
    if chain.get("owner_email", "").lower() != current_user.email.lower():
        raise HTTPException(403, "You do not own this chain.")
    return {"report": format_chain_telegram_summary(chain_id, db)}


@app.delete("/api/chains/{chain_id}")
def delete_chain(
    chain_id: str,
    delete_branches: bool = False,
    current_user: AuthenticatedUser = Depends(require_auth),
):
    """Delete a chain. delete_branches=True also deletes all branch records."""
    if not _re.match(r'^chain_[a-f0-9]{8}$', chain_id):
        raise HTTPException(400, "Invalid chain_id format.")
    db = load_database()
    chains = db.get("chains", [])
    chain = next((c for c in chains if c["chain_id"] == chain_id), None)
    if not chain:
        raise HTTPException(404, "Chain not found.")
    if chain.get("owner_email", "").lower() != current_user.email.lower():
        raise HTTPException(403, "You do not own this chain.")
    branch_ids = chain.get("branch_ids", [])
    if delete_branches:
        db["restaurants"] = [r for r in db.get("restaurants", []) if r["id"] not in branch_ids]
        try:
            from services.supabase_db import _sb

            if _sb:
                for rid in branch_ids:
                    _sb.table("restaurants").delete().eq("id", rid).execute()
        except Exception:
            pass
    else:
        for r in db.get("restaurants", []):
            if r.get("chain_id") == chain_id:
                r["chain_id"] = None
    db["chains"] = [c for c in chains if c["chain_id"] != chain_id]
    try:
        from services.supabase_db import _sb

        if _sb:
            _sb.table("chains").delete().eq("chain_id", chain_id).execute()
    except Exception:
        pass
    save_database(db)
    return {
        "success": True,
        "chain_id": chain_id,
        "branches_affected": len(branch_ids),
        "action": "branches_deleted" if delete_branches else "branches_unlinked",
    }


@app.post("/api/admin/federated_round")
def trigger_federated_round(
    request: Request,
    current_user: AuthenticatedUser = Depends(require_auth),
):
    """Admin-only: trigger one round of federated averaging across all restaurants."""
    admin_email = os.environ.get("ADMIN_EMAIL", "").lower()
    if not admin_email or current_user.email.lower() != admin_email:
        raise HTTPException(403, "Admin access required.")
    db = load_database()
    result = run_federated_round(db)
    if result.get("participants", 0) > 0:
        save_database(db)
    return {
        "success": True,
        "participants": result["participants"],
        "skipped": result["skipped"],
        "model_version": db.get("federated_model", {}).get("version", 0),
        "updated_at": db.get("federated_model", {}).get("updated_at"),
    }


@app.get("/api/federated/model_info")
def federated_model_info():
    """Public: returns current federated model metadata (not weights)."""
    db = load_database()
    model = db.get("federated_model", {})
    return {
        "version": model.get("version", 0),
        "updated_at": model.get("updated_at"),
        "participants": "Privacy-preserving ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Â participant count not disclosed.",
        "description": "Shared demand prediction model trained via federated averaging. No restaurant data was shared.",
    }


@app.post("/api/restaurant/{restaurant_id}/cv_inventory")
async def cv_inventory_scan(
    restaurant_id: str,
    file: UploadFile = File(...),
    current_user: AuthenticatedUser = Depends(require_auth),
):
    """Upload an inventory photo ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Â AI detects ingredient quantities automatically."""
    _validate_rest_id(restaurant_id)
    require_restaurant_access(restaurant_id, current_user)
    if file.content_type not in ("image/jpeg", "image/png", "image/webp", "image/jpg"):
        raise HTTPException(400, "Upload a JPEG, PNG, or WebP image.")
    image_bytes = await file.read()
    if len(image_bytes) > 10 * 1024 * 1024:
        raise HTTPException(413, "Image too large. Max 10MB.")
    if len(image_bytes) < 1000:
        raise HTTPException(400, "Image file appears to be empty or corrupt.")
    db = load_database()
    restaurant = _get_restaurant(db, restaurant_id)
    if not restaurant:
        raise HTTPException(404, "Restaurant not found.")
    return scan_inventory_from_image(image_bytes, restaurant)





class DemoLinkPayload(BaseModel):
    username: str = Field(..., min_length=3, max_length=50)


@app.post("/api/auth/demo_link_telegram")
@limiter.limit("10/minute")
async def api_demo_link_telegram(request: Request, payload: DemoLinkPayload):
    """
    Step 1 of demo Telegram linking.
    Architecture: OTPs are generated here in the HF persistent DB (auth.py)
    and delivered via bridge /api/notify. Bridge is NOT used for OTP storage ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Â
    that was the root cause of 401 errors (bridge memory lost = OTP lost).
    """
    username = payload.username.lstrip("@").lower().strip()
    if not username:
        raise HTTPException(400, "Invalid username.")

    bridge_headers = {"X-Internal-Secret": INTERNAL_SECRET} if INTERNAL_SECRET else {}

    # --- Resolve chat_id: local auth first, bridge as fallback ---
    import asyncio
    chat_id = await asyncio.to_thread(auth.get_demo_chat_id_by_username, username)

    if not chat_id and BRIDGE_URL:
        try:
            r = await _get_bridge_http().post(
                f"{BRIDGE_URL}/api/demo/link",
                json={"action": "get_linked_chat", "username": username},
                headers=bridge_headers,
                timeout=1.5,
            )
            if r.status_code == 200:
                chat_id = r.json().get("chat_id")
        except Exception as _e:
            print(f"[DemoLink] Bridge get_linked_chat error: {_e}")

    if chat_id:
        chat_id = int(chat_id)
        # Sync to local auth so verify_otp fallback always works
        await asyncio.to_thread(auth.set_demo_chat_id, username, chat_id)

        # Generate OTP in HF persistent DB ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Â NOT in bridge memory
        otp_code = await asyncio.to_thread(auth.create_demo_otp, chat_id)
        otp_text = (
            f"\U0001f510 *WasteWise Demo Login*\n\n"
            f"Your one-time code: `{otp_code}`\n"
            f"_(Valid for 10 minutes)_\n\n"
            f"Enter this on the WasteWise website to log in."
        )

        async def _deliver_otp():
            sent = False
            if BRIDGE_URL:
                try:
                    nr = await _get_bridge_http().post(
                        f"{BRIDGE_URL}/api/notify",
                        json={"chat_id": chat_id, "text": otp_text, "parse_mode": "Markdown"},
                        headers=bridge_headers,
                        timeout=2.0,
                    )
                    sent = nr.status_code == 200
                except Exception as _e:
                    print(f"[DemoLink] Background bridge notify error: {_e}")

            if not sent:
                await _tg_send(chat_id, otp_text)
                
        import asyncio
        asyncio.create_task(_deliver_otp())

        return {
            "status": "otp_sent",
            "message": f"Code sent to your Telegram @{username}",
            "chat_id_hint": str(chat_id)[-4:],
        }

    # User not yet linked ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Â set pending and ask them to message the bot
    if BRIDGE_URL:
        try:
            await _get_bridge_http().post(
                f"{BRIDGE_URL}/api/demo/link",
                json={"action": "set_pending", "username": username},
                headers=bridge_headers,
                timeout=2.0,
            )
        except Exception as _e:
            print(f"[DemoLink] Bridge set_pending error: {_e}")
    else:
        auth.create_demo_pending_link(username)

    bot_username = os.environ.get("BOT_USERNAME", "WasteWise_bot")
    return {
        "status": "pending_bot",
        "bot_username": bot_username,
        "steps": [
            "1. Open Telegram on your phone",
            f"2. Search for @{bot_username}",
            "3. Send ANY message to the bot",
            "4. We'll send you the code automatically",
        ],
        "expires_in_seconds": 600,
    }




@app.post("/internal/telegram")
async def internal_telegram(request: Request, background_tasks: BackgroundTasks):
    incoming_secret = request.headers.get("X-Internal-Secret", "")
    if INTERNAL_SECRET and incoming_secret != INTERNAL_SECRET:
        return {"ok": True}
    try:
        data = await request.json()
    except Exception:
        return {"ok": True}

    async def _process(update: dict, token: str) -> None:
        try:
            from services.telegram_bot import handle_update
            await handle_update(update, token)
        except Exception as e:
            print(f"[InternalTG] Error: {e}")

    background_tasks.add_task(_process, data, os.environ.get("TELEGRAM_TOKEN", ""))
    return {"ok": True}




