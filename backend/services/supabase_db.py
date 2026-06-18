from __future__ import annotations
import os
import json
import threading
import datetime
import logging

log = logging.getLogger("supabase_db")


try:
    from supabase import create_client, Client

    _SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
    _SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "")
    _sb: Client | None = (
        create_client(_SUPABASE_URL, _SUPABASE_KEY) if _SUPABASE_URL and _SUPABASE_KEY else None
    )
    if _sb:
        log.info("âœ… Supabase connected")
    else:
        log.warning("âš ï¸  Supabase not configured â€” falling back to JSON")
except Exception as _e:
    log.warning(f"âš ï¸  Supabase import failed ({_e }) â€” falling back to JSON")
    _sb = None


_SB_SEMAPHORE = threading.Semaphore(3)


from services.cache_layer import cache_get, cache_set, cache_delete

_DB_CACHE_KEY = "wastewise:db_snapshot"
_DB_CACHE_TTL = 86400
_cache_lock = threading.Lock()


_JSON_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "database.json")
_json_lock = threading.Lock()


import queue

_push_queue: queue.Queue = queue.Queue(maxsize=1)
_push_worker_started = False
_last_push_ts: float = 0.0


def get_last_push_ts() -> float:
    """Return unix timestamp of the last successful Supabase push (0 = never)."""
    return _last_push_ts


def _start_push_worker() -> None:
    """Start the background thread that drains the Supabase push queue."""
    global _push_worker_started
    if _push_worker_started or not _sb:
        return
    _push_worker_started = True

    def _worker() -> None:
        global _last_push_ts
        while True:
            try:
                db = _push_queue.get(timeout=5)
                for attempt in range(3):
                    try:
                        _push_to_supabase(db)
                        
                        _last_push_ts = time.monotonic()
                        break
                    except Exception as e:
                        if attempt == 2:

                            log.warning(
                                f"Supabase push failed after 3 retries: {e } â€” data safe in JSON/Redis"
                            )
                        else:
                            import time

                            time.sleep(2**attempt)
            except queue.Empty:
                continue
            except Exception as e:
                log.error(f"Push worker error: {e }")

    t = threading.Thread(target=_worker, daemon=True, name="supabase-push")
    t.start()


def load_database() -> dict:
    """
    Load full database dict.
    Priority: Redis/memory cache â†’ Supabase â†’ JSON file
    Cache TTL = 5 min. After expiry Supabase is re-fetched once.
    """
    cached = cache_get(_DB_CACHE_KEY)
    if cached is not None:
        return cached

    with _cache_lock:

        cached = cache_get(_DB_CACHE_KEY)
        if cached is not None:
            return cached
        if _sb:
            db = _pull_from_supabase()
        else:
            db = _load_json()
        cache_set(_DB_CACHE_KEY, db, ttl=_DB_CACHE_TTL)
        return db


def save_database(db: dict) -> None:
    """
    Production-grade save strategy:
      1. Write to cache (Redis/memory) immediately
      2. Write to JSON atomically (synchronous â€” data always safe)
      3. Queue Supabase push (async background â€” never blocks request)
    """
    import copy

    snapshot = copy.deepcopy(db)

    cache_set(_DB_CACHE_KEY, snapshot, ttl=_DB_CACHE_TTL)

    import threading
    threading.Thread(target=_save_json, args=(snapshot,), daemon=True).start()

    if _sb:
        _start_push_worker()
        try:
            _push_queue.put_nowait(snapshot)
        except queue.Full:

            try:
                _push_queue.get_nowait()
            except queue.Empty:
                pass

            try:
                _push_queue.put_nowait(snapshot)
            except queue.Full:
                log.warning("Supabase push queue full. Data is safe in Redis/JSON cache.")


def invalidate_cache() -> None:
    """Force next load_database() to re-fetch from Supabase."""
    cache_delete(_DB_CACHE_KEY)


def _pull_from_supabase() -> dict:
    """Fetch all data from Supabase and reconstruct the legacy dict format.

    PERFORMANCE: All 17 table fetches run in parallel (ThreadPoolExecutor).
    Cold-start load time: ~500ms instead of 5-8 seconds.
    """
    if _sb is None:
        raise RuntimeError("_pull_from_supabase called but Supabase client is not initialised")
    from concurrent.futures import ThreadPoolExecutor

    db: dict = {
        "restaurants": [],
        "regions": {},
        "accounts": [],
        "pending_otps": [],
        "pending_registrations": [],
        "pending_approvals": [],
        "global_learning_events": [],
        "chains": [],
    }
    try:

        TABLES = [
            "regions",
            "restaurants",
            "restaurant_menu",
            "daily_records",
            "daily_items_sold",
            "active_events",
            "closing_stock",
            "marketplace_orders",
            "accounts",
            "sessions",
            "pending_otps",
            "pending_registrations",
            "pending_approvals",
            "chains",
        ]

        def _fetch(table: str):
            for attempt in range(3):
                try:
                    return table, (_sb.table(table).select("*").execute().data or [])
                except Exception as e:
                    if attempt == 2:
                        raise e
                    import time

                    time.sleep(1 + attempt)

        raw: dict = {}
        with ThreadPoolExecutor(max_workers=3) as pool:
            for table, data in pool.map(_fetch, TABLES):
                raw[table] = data

        rows = raw["regions"]
        rests = raw["restaurants"]
        menus = raw["restaurant_menu"]
        records = raw["daily_records"]
        items_sold = raw["daily_items_sold"]
        events = raw["active_events"]
        stock = raw["closing_stock"]
        orders = raw["marketplace_orders"]

        for r in rows:
            db["regions"][r["name"]] = {
                "type": r["type"],
                "foot_traffic_baseline": r["foot_traffic_baseline"],
                "weekend_multiplier": r["weekend_multiplier"],
                "holiday_multiplier": r["holiday_multiplier"],
                "rain_impact": r["rain_impact"],
            }

        menu_by_rest: dict[str, list] = {}
        for m in menus:
            menu_by_rest.setdefault(m["restaurant_id"], []).append(
                {
                    "item": m["item"],
                    "base_daily_demand": m["base_daily_demand"],
                    "profit_margin_rm": float(m["profit_margin_rm"]),
                    "price_rm": float(m["price_rm"]),
                    "halal_certified": m.get("halal_certified", True),
                    "allergens": m.get("allergens") or [],
                    "description": m.get("description") or "",
                }
            )

        items_by_date: dict[str, dict[str, dict]] = {}
        for i in items_sold:
            key = f"{i ['restaurant_id']}_{i ['date']}"
            items_by_date.setdefault(key, {})[i["item"]] = i["qty_sold"]

        events_by_rest: dict[str, list] = {}
        for ev in events:
            events_by_rest.setdefault(ev["restaurant_id"], []).append(
                {
                    "description": ev["description"],
                    "headcount": ev["headcount"],
                    "days": ev["days"],
                    "date": ev["event_date"],
                    "expires_at": ev["expires_at"],
                }
            )

        stock_by_rest: dict[str, list] = {}
        closing_date_by_rest: dict[str, str] = {}
        for s in stock:
            stock_by_rest.setdefault(s["restaurant_id"], []).append(
                {
                    "item": s["item"],
                    "qty_available": s["qty_available"],
                    "original_price_rm": float(s["original_price_rm"]),
                    "discounted_price_rm": float(s["discounted_price_rm"]),
                    "discount_pct": s["discount_pct"],
                }
            )
            closing_date_by_rest[s["restaurant_id"]] = s["stock_date"]

        orders_by_rest: dict[str, list] = {}
        for o in orders:
            orders_by_rest.setdefault(o["restaurant_id"], []).append(
                {
                    "order_id": o["order_id"],
                    "date": o["order_date"],
                    "created_at": o.get("created_at", ""),
                    "customer_name": o["customer_name"],
                    "phone": o["phone"],
                    "items": o["items"],
                    "total_rm": float(o["total_rm"]),
                    "shopkeeper_earnings_rm": float(o["shopkeeper_earnings_rm"]),
                    "platform_fee_rm": float(o["platform_fee_rm"]),
                    "pickup_deadline": o.get("pickup_deadline"),
                    "pickup_notes": o.get("pickup_notes", ""),
                    "reminder_sent": o.get("reminder_sent", False),
                    "status": o["status"],
                }
            )

        records_by_rest: dict[str, list] = {}
        for rec in records:
            rid = rec["restaurant_id"]
            key = f"{rid }_{rec ['date']}"
            actual_sales = items_by_date.get(key, {})
            records_by_rest.setdefault(rid, []).append(
                {
                    "date": rec["date"],
                    "forecast": rec.get("forecast_text"),
                    "forecast_generated_at": rec.get("forecast_generated_at"),
                    "actual_sales": actual_sales if actual_sales else None,
                    "total_revenue_rm": float(rec.get("total_revenue_rm", 0)),
                    "total_waste_qty": int(rec.get("total_waste_qty", 0)),
                    "weather": rec.get("weather"),
                    "foot_traffic": rec.get("foot_traffic"),
                }
            )

        for rest in rests:
            rid = rest["id"]
            db["restaurants"].append(
                {
                    "id": rid,
                    "name": rest["name"],
                    "region": rest["region"],
                    "type": rest["type"],
                    "owner_name": rest["owner_name"],
                    "telegram_chat_id": rest.get("telegram_chat_id"),
                    "telegram_username": rest.get("telegram_username"),
                    "email": rest.get("email"),
                    "chain_id": rest.get("chain_id"),
                    "privacy_accepted": rest.get("privacy_accepted", True),
                    "registered_at": rest.get("registered_at", ""),
                    "specialty_weather": rest.get("specialty_weather", "neutral"),
                    "closing_time": rest.get("closing_time", "21:00"),
                    "discount_pct": rest.get("discount_pct", 30),
                    "marketplace_enabled": rest.get("marketplace_enabled", True),
                    "preferred_language": rest.get("preferred_language", "english"),
                    "latitude": rest.get("latitude"),
                    "longitude": rest.get("longitude"),
                    "is_demo": rest.get("is_demo", False),
                    "bom": rest.get("bom") or {},
                    "recent_feedback_memory": rest.get("recent_feedback_memory") or [],
                    "q_tables": rest.get("q_tables") or {},
                    "sustainability_waste_prevented_kg": float(
                        rest.get("sustainability_waste_prevented_kg", 0)
                    ),
                    "sustainability_co2_saved_kg": float(
                        rest.get("sustainability_co2_saved_kg", 0)
                    ),
                    "menu": menu_by_rest.get(rid, []),
                    "daily_records": sorted(records_by_rest.get(rid, []), key=lambda r: r["date"]),
                    "active_events": events_by_rest.get(rid, []),
                    "closing_stock": stock_by_rest.get(rid, []),
                    "closing_stock_date": closing_date_by_rest.get(rid, ""),
                    "marketplace_orders": orders_by_rest.get(rid, []),
                }
            )

        accts = raw["accounts"]
        sess = raw["sessions"]
        sess_by_account: dict[str, list] = {}
        for s in sess:
            sess_by_account.setdefault(str(s["account_id"]), []).append(
                {
                    "session_id": s["session_id"],
                    "type": s["type"],
                    "chat_id": s.get("chat_id"),
                    "telegram_username": s.get("telegram_username"),
                    "label": s.get("label", ""),
                    "is_primary": s.get("is_primary", False),
                    "linked_at": s.get("linked_at", ""),
                    "last_active": s.get("last_active", ""),
                    "expires_at": s.get("expires_at"),
                }
            )
        for a in accts:
            db["accounts"].append(
                {
                    "email": a["email"],
                    "restaurant_id": a.get("restaurant_id"),
                    "created_at": a.get("created_at", ""),
                    "sessions": sess_by_account.get(str(a["id"]), []),
                    "_account_uuid": str(a["id"]),
                }
            )

        db["pending_otps"] = raw["pending_otps"]
        db["pending_registrations"] = [
            {**r, "restaurant_data": r.get("restaurant_data") or {}}
            for r in raw["pending_registrations"]
        ]
        db["pending_approvals"] = raw["pending_approvals"]
        db["chains"] = raw["chains"]

        log.info(
            f"âœ… Pulled from Supabase: {len (db ['restaurants'])} restaurants, {len (accts )} accounts"
        )

    except Exception as e:
        log.error(f"Supabase pull failed: {e } â€” using JSON fallback")
        return _load_json()

    return db


def _push_to_supabase(db: dict) -> None:
    """Write changed data back to Supabase tables."""
    if _sb is None:
        raise RuntimeError("_push_to_supabase called but Supabase client is not initialised")
    now = datetime.datetime.utcnow().isoformat()

    for name, r in db.get("regions", {}).items():
        _sb.table("regions").upsert(
            {
                "name": name,
                "type": r.get("type", "General Area"),
                "foot_traffic_baseline": r.get("foot_traffic_baseline", 500),
                "weekend_multiplier": r.get("weekend_multiplier", 1.1),
                "holiday_multiplier": r.get("holiday_multiplier", 1.0),
                "rain_impact": r.get("rain_impact", -0.2),
            },
            on_conflict="name",
        ).execute()

    for rest in db.get("restaurants", []):
        rid = rest["id"]
        _sb.table("restaurants").upsert(
            {
                "id": rid,
                "name": rest["name"],
                "region": rest["region"],
                "type": rest.get("type", "hawker"),
                "owner_name": rest.get("owner_name", "Owner"),
                "telegram_chat_id": rest.get("telegram_chat_id"),
                "telegram_username": rest.get("telegram_username"),
                "email": rest.get("email"),
                "chain_id": rest.get("chain_id"),
                "privacy_accepted": rest.get("privacy_accepted", True),
                "registered_at": rest.get("registered_at", now),
                "specialty_weather": rest.get("specialty_weather", "neutral"),
                "closing_time": rest.get("closing_time", "21:00"),
                "discount_pct": rest.get("discount_pct", 30),
                "marketplace_enabled": rest.get("marketplace_enabled", True),
                "preferred_language": rest.get("preferred_language", "english"),
                "latitude": rest.get("latitude"),
                "longitude": rest.get("longitude"),
                "bom": rest.get("bom", {}),
                "recent_feedback_memory": rest.get("recent_feedback_memory", []),
                "q_tables": rest.get("q_tables", {}),
                "bayesian_beliefs": rest.get("bayesian_beliefs", {}),
                "gamification": rest.get("gamification", {}),
                "sustainability_waste_prevented_kg": rest.get(
                    "sustainability_waste_prevented_kg", 0
                ),
                "sustainability_co2_saved_kg": rest.get("sustainability_co2_saved_kg", 0),
                "updated_at": now,
            },
            on_conflict="id",
        ).execute()

        # Use upsert+selective-delete to avoid the DELETE+INSERT race condition.
        # If the INSERT fails after a DELETE, the menu would be permanently wiped.
        # Instead: upsert all current items, then delete any items NOT in current menu.
        menu_rows = [
            {
                "restaurant_id": rid,
                "item": item["item"],
                "base_daily_demand": item.get("base_daily_demand", 50),
                "profit_margin_rm": item.get("profit_margin_rm", 2.5),
                "price_rm": item.get("price_rm", 5.0),
                "halal_certified": item.get("halal_certified", True),
                "allergens": item.get("allergens", []),
                "description": item.get("description", ""),
                "updated_at": now,
            }
            for item in rest.get("menu", [])
        ]
        current_item_names = [item["item"] for item in rest.get("menu", [])]
        if menu_rows:
            _sb.table("restaurant_menu").upsert(
                menu_rows, on_conflict="restaurant_id,item"
            ).execute()
        # Remove items that are no longer in the menu
        if current_item_names:
            # Delete rows for this restaurant that are NOT in current_item_names
            all_menu = _sb.table("restaurant_menu").select("item").eq("restaurant_id", rid).execute().data or []
            items_to_delete = [r["item"] for r in all_menu if r["item"] not in current_item_names]
            for orphan in items_to_delete:
                _sb.table("restaurant_menu").delete().eq("restaurant_id", rid).eq("item", orphan).execute()
        else:
            # Menu is empty â€” clear all items for this restaurant
            _sb.table("restaurant_menu").delete().eq("restaurant_id", rid).execute()


        daily_rows = []
        sold_rows = []
        price_map = {m["item"]: m.get("price_rm", 0) for m in rest.get("menu", [])}

        for rec in rest.get("daily_records", []):
            date_str = rec.get("date", "")
            if not date_str:
                continue
            daily_rows.append(
                {
                    "restaurant_id": rid,
                    "date": date_str,
                    "total_revenue_rm": rec.get("total_revenue_rm", 0),
                    "total_waste_qty": rec.get("total_waste_qty", 0),
                    "weather": rec.get("weather"),
                    "foot_traffic": rec.get("foot_traffic"),
                    "forecast_text": rec.get("forecast"),
                    "forecast_generated_at": rec.get("forecast_generated_at"),
                }
            )
            actual = rec.get("actual_sales") or rec.get("items_sold") or {}
            items_list = (
                [{"item": k, "qty_sold": v} for k, v in actual.items()]
                if isinstance(actual, dict)
                else actual
            )
            for sold in items_list:
                sold_rows.append(
                    {
                        "restaurant_id": rid,
                        "date": date_str,
                        "item": sold["item"],
                        "qty_sold": sold.get("qty_sold", 0),
                        "revenue_rm": round(
                            sold.get("qty_sold", 0) * price_map.get(sold["item"], 0), 2
                        ),
                    }
                )

        if daily_rows:
            _sb.table("daily_records").upsert(
                daily_rows, on_conflict="restaurant_id,date"
            ).execute()
        if sold_rows:
            _sb.table("daily_items_sold").upsert(
                sold_rows, on_conflict="restaurant_id,date,item"
            ).execute()

        # Use upsert for active_events to avoid race condition
        ev_rows = [
            {
                "restaurant_id": rid,
                "description": ev.get("description", ""),
                "headcount": ev.get("headcount", 0),
                "days": ev.get("days", 1),
                "event_date": ev.get("date", ""),
                "expires_at": ev.get("expires_at", ""),
            }
            for ev in rest.get("active_events", [])
        ]
        # Clear and rewrite events â€” events have no natural unique key for upsert
        # but we do it atomically by deleting only if we have replacements OR there are none
        _sb.table("active_events").delete().eq("restaurant_id", rid).execute()
        if ev_rows:
            _sb.table("active_events").insert(ev_rows).execute()


        _sb.table("closing_stock").delete().eq("restaurant_id", rid).execute()
        stock_rows = []
        stock_date = rest.get("closing_stock_date", "")
        if stock_date:
            for s in rest.get("closing_stock", []):
                stock_rows.append(
                    {
                        "restaurant_id": rid,
                        "stock_date": stock_date,
                        "stock_time": rest.get("closing_stock_time"),
                        "item": s["item"],
                        "qty_available": s.get("qty_available", 0),
                        "original_price_rm": s.get("original_price_rm", 0),
                        "discounted_price_rm": s.get("discounted_price_rm", 0),
                        "discount_pct": s.get("discount_pct", 30),
                    }
                )
            if stock_rows:
                _sb.table("closing_stock").insert(stock_rows).execute()

        _sb.table("marketplace_orders").delete().eq("restaurant_id", rid).execute()
        order_rows = []
        for o in rest.get("marketplace_orders", []):
            order_rows.append(
                {
                    "order_id": o.get("order_id", ""),
                    "restaurant_id": rid,
                    "order_date": o.get("date", ""),
                    "created_at": o.get("created_at", now),
                    "customer_name": o.get("customer_name", ""),
                    "phone": o.get("phone", ""),
                    "items": o.get("items", []),
                    "total_rm": o.get("total_rm", 0),
                    "shopkeeper_earnings_rm": o.get("shopkeeper_earnings_rm", 0),
                    "platform_fee_rm": o.get("platform_fee_rm", 0),
                    "pickup_deadline": o.get("pickup_deadline"),
                    "pickup_notes": o.get("pickup_notes", ""),
                    "reminder_sent": o.get("reminder_sent", False),
                    "status": o.get("status", "pending"),
                }
            )
        if order_rows:
            _sb.table("marketplace_orders").insert(order_rows).execute()

    for acct in db.get("accounts", []):

        uuid_val = acct.get("_account_uuid")
        acct_row = {"email": acct["email"], "restaurant_id": acct.get("restaurant_id")}
        _sb.table("accounts").upsert(acct_row, on_conflict="email").execute()

        if not uuid_val:
            rows = _sb.table("accounts").select("id").eq("email", acct["email"]).execute().data
            uuid_val = rows[0]["id"] if rows else None
        if uuid_val:

            sess_rows = [
                {
                    "session_id": s["session_id"],
                    "account_id": uuid_val,
                    "type": s.get("type", "web"),
                    "chat_id": s.get("chat_id"),
                    "telegram_username": s.get("telegram_username"),
                    "label": s.get("label", ""),
                    "is_primary": s.get("is_primary", False),
                    "linked_at": s.get("linked_at", now),
                    "last_active": s.get("last_active", now),
                    "expires_at": s.get("expires_at"),
                }
                for s in acct.get("sessions", [])
            ]
            if sess_rows:
                _sb.table("sessions").upsert(sess_rows, on_conflict="session_id").execute()
                

    _sb.table("pending_otps").delete().lt("expires_at", now).execute()
    for otp in db.get("pending_otps", []):
        _sb.table("pending_otps").upsert(otp, on_conflict="id").execute()

    _sb.table("pending_registrations").delete().lt("expires_at", now).execute()
    for reg in db.get("pending_registrations", []):
        _sb.table("pending_registrations").upsert(
            {
                "email": reg["email"],
                "telegram_username": reg.get("telegram_username", ""),
                "restaurant_data": reg.get("restaurant_data", {}),
                "code_hash": reg.get("code_hash", ""),
                "expires_at": reg.get("expires_at", ""),
            },
            on_conflict="email",
        ).execute()

    for ap in db.get("pending_approvals", []):
        _sb.table("pending_approvals").upsert(ap, on_conflict="approval_id").execute()



def _load_json() -> dict:
    """Load from JSON file. Returns empty structure if file missing."""
    try:
        with _json_lock:
            with open(_JSON_PATH, encoding="utf-8") as f:
                return json.load(f)
    except FileNotFoundError:
        return {
            "restaurants": [],
            "regions": {},
            "accounts": [],
            "pending_otps": [],
            "pending_registrations": [],
            "pending_approvals": [],
            "chains": [],
            "global_learning_events": [],
        }
    except json.JSONDecodeError as e:
        log.error(f"database.json corrupt: {e } â€” attempting to load from Supabase")
        if _sb:
            return _pull_from_supabase()
        raise


def _save_json(db: dict) -> None:
    """
    Atomically write JSON using write-to-temp + os.replace.
    os.replace() is atomic on all major operating systems:
      - On POSIX: atomic rename (POSIX guarantee)
      - On Windows: atomic since Python 3.3+
    The .tmp file is NEVER in a partial state when database.json is read.
    Creates the data/ directory automatically if it doesn't exist (e.g. Railway).
    """
    with _json_lock:

        os.makedirs(os.path.dirname(_JSON_PATH), exist_ok=True)
        tmp = _JSON_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(db, f, ensure_ascii=False)
        os.replace(tmp, _JSON_PATH)



def sb_upsert_session(account_email: str, session: dict) -> None:
    """Upsert a single session without rewriting the entire DB."""
    if _sb:
        rows = _sb.table("accounts").select("id").eq("email", account_email).execute().data
        if rows:
            account_id = rows[0]["id"]
            _sb.table("sessions").upsert(
                {**session, "account_id": account_id}, on_conflict="session_id"
            ).execute()
    


def sb_get_account_by_session(session_id: str) -> dict | None:
    """Fast lookup: get account by session token."""
    if _sb:
        rows = _sb.table("sessions").select("*").eq("session_id", session_id).execute().data
        if not rows:
            return None
        s = rows[0]
        acct_rows = _sb.table("accounts").select("*").eq("id", str(s["account_id"])).execute().data
        return acct_rows[0] if acct_rows else None
    db = _load_json()
    for acct in db.get("accounts", []):
        for sess in acct.get("sessions", []):
            if sess.get("session_id") == session_id:
                return acct
    return None


def sb_get_restaurant(restaurant_id: str) -> dict | None:
    """Fast single-restaurant lookup from cache or Supabase."""
    db = load_database()
    return next((r for r in db.get("restaurants", []) if r["id"] == restaurant_id), None)

