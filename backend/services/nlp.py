import json
import datetime
import threading
import requests
from typing import Optional
from dotenv import load_dotenv

load_dotenv()

MAX_MEMORY_ENTRIES = 20
MAX_GLOBAL_EVENTS = 100
MAX_DAILY_RECORDS = 60


from services.supabase_db import load_database, save_database


def load_db_for(restaurant_id: str) -> dict:
    return load_database()


def save_db_for(restaurant_id: str, db: dict) -> None:
    try:
        from services.sse_broadcaster import publish_refresh
        publish_refresh(restaurant_id)
    except Exception:
        pass
    save_database(db)


def _get_restaurant(db: dict, restaurant_id: str) -> Optional[dict]:
    return next((r for r in db.get("restaurants", []) if r["id"] == restaurant_id), None)


def get_today(restaurant: dict) -> datetime.date:
    return datetime.date.today()


def get_yesterday(restaurant: dict) -> datetime.date:
    return datetime.date.today() - datetime.timedelta(days=1)


def _append_memory(restaurant: dict, message: str) -> None:
    mem = restaurant.setdefault("recent_feedback_memory", [])
    mem.append({"timestamp": datetime.datetime.now().isoformat(), "message": message})
    restaurant["recent_feedback_memory"] = mem[-MAX_MEMORY_ENTRIES:]


def get_weather(region: str) -> str:
    from services.cache import get_weather_cache, set_weather_cache

    cached = get_weather_cache(region)
    if cached:
        return cached
    city_map = {
        "Sikar INTI": "Sikar,India",
        "Connaught Place": "New+Delhi,India",
        "Chandni Chowk": "Penang,India",
        "Lajpat Nagar": "Subang+Jaya,India",
        "Karol Bagh": "New+Delhi,India",
        "Sadar Bazaar": "New+Delhi,India",
        "Cyberabad": "Cyberabad,India",
    }
    city = city_map.get(region, "New+Delhi,India")
    try:
        resp = requests.get(
            f"https://wttr.in/{city }?format=j1",
            timeout=3,
            headers={"User-Agent": "WasteWiseAI/5.0"},
        )
        if resp.status_code == 200:
            cond = resp.json()["current_condition"][0]
            temp_c = int(cond.get("temp_C", 32))
            feels = int(cond.get("FeelsLikeC", temp_c))
            desc = cond.get("weatherDesc", [{}])[0].get("value", "").lower()
            humid = int(cond.get("humidity", 70))
            if any(w in desc for w in ("rain", "thunder", "drizzle", "shower")):
                result = f"rainy ({temp_c }Â°C, feels {feels }Â°C, {humid }% humidity)"
            elif temp_c >= 34:
                result = f"very hot ({temp_c }Â°C, feels {feels }Â°C, {humid }% humidity)"
            elif temp_c >= 30:
                result = f"hot ({temp_c }Â°C, feels {feels }Â°C, {humid }% humidity)"
            else:
                result = f"warm ({temp_c }Â°C, feels {feels }Â°C, {humid }% humidity)"
            set_weather_cache(region, result)
            return result
    except Exception:
        pass
    result = "warm (~32Â°C, typical indian day)"
    set_weather_cache(region, result)
    return result


def get_active_events(restaurant: dict) -> list:
    """
    Return events active today. Prunes expired events from restaurant dict.
    Supports both storage formats:
      - New canonical: {"date": ..., "expires_at": ..., "days": ...}
      - Legacy:        {"start_date": ..., "end_date": ...}
    """
    today = datetime.date.today()
    valid, active = [], []
    for ev in restaurant.get("active_events", []):
        try:

            end_str = ev.get("expires_at") or ev.get("end_date", "")
            start_str = ev.get("date") or ev.get("start_date", "")
            if not end_str or not start_str:
                continue
            end = datetime.date.fromisoformat(end_str[:10])
            start = datetime.date.fromisoformat(start_str[:10])
        except (KeyError, ValueError):
            continue
        if today <= end:
            valid.append(ev)
            if today >= start:
                active.append(ev)
    restaurant["active_events"] = valid
    return active


def register_owner_event(restaurant_id: str, description: str, headcount: int, days: int) -> str:
    if not description.strip():
        return "Event description cannot be empty."
    if not (1 <= days <= 30):
        return "Event duration must be 1 to 30 days."
    if not (1 <= headcount <= 100_000):
        return "Invalid headcount."
    db = load_db_for(restaurant_id)
    restaurant = _get_restaurant(db, restaurant_id)
    if not restaurant:
        return "Restaurant not found."
    today = datetime.date.today()
    end_date = today + datetime.timedelta(days=days - 1)

    restaurant.setdefault("active_events", []).append(
        {
            "description": description.strip(),
            "headcount": headcount,
            "days": days,
            "date": today.isoformat(),
            "expires_at": end_date.isoformat(),
        }
    )
    _append_memory(
        restaurant,
        f"Owner declared event: '{description }' for {headcount :,} people "
        f"({today .isoformat ()} to {end_date .isoformat ()}).",
    )
    save_db_for(restaurant_id, db)
    dur = (
        "today only" if days == 1 else f"for {days } days (until {end_date .strftime ('%d %b %Y')})"
    )
    return (
        f"Event registered!\n'{description }' â€” {headcount :,} people, {dur }.\n"
        f"AI will factor this into all forecasts automatically."
    )


def detect_today_context(region: str, region_type: str, today_str: str, day_of_week: str) -> dict:
    from services.cache import get_context_cache, set_context_cache
    from services.ai_provider import call_ai_json

    cached = get_context_cache(region, today_str)
    if cached:
        return cached

    prompt = f"""India food-demand context engine. Today: {today_str } ({day_of_week }). Region: {region } ({region_type }).

What is significant TODAY in India that affects food demand? Use your knowledge of 2026 indian:
- Public holidays (federal + state), school holidays (DLP), university exam/break periods
- Islamic calendar (Ramadan timing, Eid), Chinese/Hindu/Tamil festivals, SPM/PT3 exams

Return ONLY valid JSON:
{{"public_holiday":"name or null","is_public_holiday":false,"school_holiday":"name or null","is_school_holiday":false,"religious_observance":"name or null","university_period":"exam_season/semester_break/normal","demand_signal":"surge/slight_surge/normal/slight_drop/drop","demand_reasoning":"2 sentences for {region_type }","affected_categories":["list"],"confidence":"high/medium/low"}}"""

    result = call_ai_json(prompt)
    if result:
        result.setdefault("is_public_holiday", False)
        result.setdefault("is_school_holiday", False)
        result.setdefault("demand_signal", "normal")
        result.setdefault("demand_reasoning", "Normal trading day.")
        result.setdefault("affected_categories", [])
        result.setdefault("confidence", "medium")
        result.setdefault("university_period", "normal")
        set_context_cache(region, today_str, result)
        return result

    fallback = {
        "is_public_holiday": False,
        "is_school_holiday": False,
        "demand_signal": "normal",
        "demand_reasoning": "Normal trading day.",
        "affected_categories": [],
        "confidence": "low",
        "university_period": "normal",
    }
    set_context_cache(region, today_str, fallback)
    return fallback


def _store_forecast_db(db: dict, restaurant_id: str, today_str: str, forecast_text: str) -> None:
    for r in db.get("restaurants", []):
        if r["id"] != restaurant_id:
            continue
        records = r.setdefault("daily_records", [])
        for rec in records:
            if rec["date"] == today_str:
                rec["forecast"] = forecast_text
                rec["forecast_generated_at"] = datetime.datetime.now().isoformat()
                r["daily_records"] = sorted(records, key=lambda x: x["date"])[-MAX_DAILY_RECORDS:]
                return
        records.append(
            {
                "date": today_str,
                "forecast": forecast_text,
                "forecast_generated_at": datetime.datetime.now().isoformat(),
                "actual_sales": None,
            }
        )
        r["daily_records"] = sorted(records, key=lambda x: x["date"])[-MAX_DAILY_RECORDS:]
        return


def _build_forecast_from_db(restaurant: dict, today_str: str) -> Optional[str]:
    """Check DB-stored forecast as secondary cache (survives server restart)."""
        # STRICTLY show days that have actual items sold data
    valid_records = [r for r in restaurant.get("daily_records", []) if r.get("actual_sales")]
    valid_records.sort(key=lambda x: x.get("date", ""))
    for rec in valid_records[-7:]:
        if rec.get("date") == today_str and rec.get("forecast"):
            return rec["forecast"]
    return None


def _generate_forecast_async(restaurant_id: str) -> None:
    """Run in background thread â€” never blocks the HTTP response."""
    try:
        _do_generate_forecast(restaurant_id)
    except Exception as e:
        print(f"[Forecast] Background error for {restaurant_id }: {e }")


def _do_generate_forecast(restaurant_id: str) -> str:
    """The actual forecast generation logic (blocking, run in background)."""
    from services.ai_provider import call_ai, ai_available
    from services.data_miner import format_intelligence_report
    from services.cache import set_forecast_cache, set_intelligence_cache

    db = load_db_for(restaurant_id)
    restaurant = _get_restaurant(db, restaurant_id)
    if not restaurant:
        return "Restaurant not found."

    today_date = get_today(restaurant)
    today_str = today_date.isoformat()
    day_of_week = today_date.strftime("%A")
    # For demo environments, use the mocked 'today' date but keep real time
    now_time = datetime.datetime.now().time()
    now_str = datetime.datetime.combine(today_date, now_time).strftime("%d %b %Y, %I:%M %p")

    region = restaurant["region"]
    region_info = db.get("regions", {}).get(region, {})
    region_type = region_info.get("type", "General area")
    weather = get_weather(region)
    active_events = get_active_events(restaurant)

    local_ev_str = (
        " | ".join(
            f"'{ev ['description']}' ({ev .get ('headcount','?'):,} people, until {ev ['end_date']})"
            for ev in active_events
        )
        if active_events
        else "None"
    )

    today_ctx = detect_today_context(region, region_type, today_str, day_of_week)
    intel = format_intelligence_report(restaurant_id, db)

    from services.data_miner import (
        calculate_waste_metrics,
        compute_data_quality_score,
        compute_mape_per_item,
        compute_item_trends_with_tuning,
    )

    today_wd = get_today(restaurant).strftime("%A")
    item_trends_local = compute_item_trends_with_tuning(restaurant, today_wd)
    waste_metrics = calculate_waste_metrics(restaurant, item_trends_local)
    dq_score = compute_data_quality_score(restaurant)
    mape_data = compute_mape_per_item(restaurant)
    from services.cache import set_intelligence_cache

    set_intelligence_cache(restaurant_id, intel)

    recent_mem = restaurant.get("recent_feedback_memory", [])[-10:]

    ctx_parts = []

    waste_str = (
        f"\u20b9{waste_metrics['total_saved_rm']:.2f}/day | "
        f"\u20b9{waste_metrics['weekly_saved_rm']:.2f}/week | "
        f"{waste_metrics['total_saved_kg']:.1f}kg/day saved vs naive baseline"
    )
    quality_str = (
        f"Data quality: {dq_score ['grade']} ({dq_score ['score']}/100) â€” {dq_score ['label']}"
    )
    if mape_data:
        worst_items = sorted(mape_data.items(), key=lambda x: x[1]['mape'], reverse=True)[:3]
        accuracy_str = " | ".join(
            f"{item }: MAPE={data ['mape']:.0f}% bias={data ['bias']:+.0f}%"
            for item, data in worst_items
            if data['n'] >= 3
        )
    else:
        accuracy_str = "No accuracy data yet"

    if today_ctx.get("is_public_holiday") and today_ctx.get("public_holiday"):
        ctx_parts.append(f"PUBLIC HOLIDAY: {today_ctx ['public_holiday']}")
    if today_ctx.get("is_school_holiday") and today_ctx.get("school_holiday"):
        ctx_parts.append(f"SCHOOL HOLIDAY: {today_ctx ['school_holiday']}")
    if today_ctx.get("religious_observance"):
        ctx_parts.append(f"RELIGIOUS: {today_ctx ['religious_observance']}")
    if today_ctx.get("university_period") not in (None, "normal"):
        ctx_parts.append(
            f"UNIVERSITY: {today_ctx ['university_period'].replace ('_',' ').title ()}"
        )
    if not ctx_parts:
        ctx_parts.append("Regular trading day")

    prompt = f"""You are WasteWise AI â€” indian food waste reduction system with genuine ML intelligence.

You receive PRE-COMPUTED statistical results from Holt-Winters forecasting + cross-restaurant mining.
Your job: narrate these computed facts into precise preparation quantities.

â•â• RESTAURANT â•â•
Name: {restaurant ['name']} | Region: {region } ({region_type })
Today: {today_str } ({day_of_week }) | Weather: {weather }
Foot traffic baseline: {region_info .get ('foot_traffic_baseline',500 )}/day

â•â• CALENDAR CONTEXT (AI-detected) â•â•
{' | '.join (ctx_parts )}
Demand signal: {today_ctx .get ('demand_signal','normal').upper ()} ({today_ctx .get ('confidence','medium')} confidence)
Reasoning: {today_ctx .get ('demand_reasoning','Normal day.')}
Affected categories: {', '.join (today_ctx .get ('affected_categories',[]))or 'None'}

â•â• OWNER-DECLARED LOCAL EVENTS â•â•
{local_ev_str }

â•â• ML INTELLIGENCE (Ensemble: Holt-Winters + ARIMA + Seasonality + Cross-Restaurant Mining) â•â•
{intel }

â•â• OWNER MEMORY LOGS (highest priority) â•â•
{json .dumps (recent_mem ,indent =2 )if recent_mem else "No logs yet."}

â•â• FULL MENU â•â•
{json .dumps (restaurant .get ('menu',[]),indent =2 )}

â•â• FORECAST ACCURACY & WASTE CONTEXT â•â•\n{waste_str }\n{quality_str }\nAccuracy per item: {accuracy_str }\n\nâ•â• SYNTHESIS RULES â•â•
1. For items WITH historical data: use the recommended_qty from ML intelligence as your BASE number
2. Apply the demand_signal multiplier (surge=Ã—1.5, slight_surge=Ã—1.15, drop=Ã—0.6, slight_drop=Ã—0.88)
3. For SHIFT ALERT items (strongly_falling/rising): respect the alert â€” reduce or increase significantly
4. For items with anomaly warnings: use the EWMA not the anomalous last reading
5. Weather: rain = reduce footfall items; very hot = boost cold drinks/desserts/ice cream
6. Local event: scale ALL items by headcount/typical_daily_covers
7. For NEW items (no history): use base_daily_demand Ã— today's demand signal
8. ECOSYSTEM signals: apply category-wide trend to all items in that category

PRIVACY: Never name competitors. Treat cross-restaurant data as your own intuition.

â•â• OUTPUT (follow exactly) â•â•
â˜€ï¸ Good morning, {restaurant ['name']}!
ðŸ“… Forecast generated: {now_str }

â€¢ [Item Name]: [Number] [unit]
[every single menu item â€” none skipped]

Reason: [One precise sentence. Name the main driver: event+headcount, holiday, trending item, or weather. Max 25 words.]"""

    if not ai_available():
        from services.data_miner import compute_item_trends

        trends = compute_item_trends(restaurant)
        lines = [f"â˜€ï¸ Good morning, {restaurant ['name']}!", f"ðŸ“… Generated: {now_str }", ""]
        for m in restaurant.get("menu", []):
            t = trends.get(m["item"])
            q = int(t.recommended_qty) if t else m["base_daily_demand"]
            lines.append(f"â€¢ {m ['item']}: {q } portions")
        lines.append(
            f"\nReason: Statistical ML forecast (EWMA+Holt-Winters) â€” add AI API keys to .env for narration."
        )
        forecast = "\n".join(lines)
    else:
        forecast = call_ai(prompt, json_mode=False)
        if not forecast:
            from services.data_miner import compute_item_trends

            trends = compute_item_trends(restaurant)
            lines = [f"â˜€ï¸ Good morning, {restaurant ['name']}!", f"ðŸ“… Generated: {now_str }", ""]
            for m in restaurant.get("menu", []):
                t = trends.get(m["item"])
                q = int(t.recommended_qty) if t else m["base_daily_demand"]
                lines.append(f"â€¢ {m ['item']}: {q } portions")
            lines.append(
                "\nReason: Holt-Winters statistical forecast (AI narration temporarily unavailable)."
            )
            forecast = "\n".join(lines)

    set_forecast_cache(restaurant_id, today_str, forecast)
    _store_forecast_db(db, restaurant_id, today_str, forecast)
    save_db_for(restaurant_id, db)
    return forecast


def generate_morning_forecast(restaurant_id: str, force_refresh: bool = False) -> str:
    """
    FAST PATH: Returns cached forecast IMMEDIATELY.
    If no cache: returns statistical baseline instantly + spawns background AI generation.
    The frontend polls every 30s â€” it will get the full AI forecast on next poll.
    """
    from services.cache import get_forecast_cache, set_forecast_cache, invalidate_forecast
    from services.data_miner import compute_item_trends

    db = load_db_for(restaurant_id)
    restaurant = _get_restaurant(db, restaurant_id)
    if not restaurant:
        return "Restaurant not found."

    today_date = get_today(restaurant)
    today_str = today_date.isoformat()
    now_time = datetime.datetime.now().time()
    now_str = datetime.datetime.combine(today_date, now_time).strftime("%d %b %Y, %I:%M %p")

    if force_refresh:
        invalidate_forecast(restaurant_id)
    else:
        cached = get_forecast_cache(restaurant_id, today_str)
        if cached:
            return cached

    if not force_refresh:
        db_cached = _build_forecast_from_db(restaurant, today_str)
        if db_cached:
            set_forecast_cache(restaurant_id, today_str, db_cached)
            return db_cached

    trends = compute_item_trends(restaurant)
    lines = [
        f"â˜€ï¸ Good morning, {restaurant ['name']}!",
        f"ðŸ“… Generated: {now_str } (statistical baseline)",
        "",
    ]
    for m in restaurant.get("menu", []):
        t = trends.get(m["item"])
        q = int(t.recommended_qty) if t else m["base_daily_demand"]
        lines.append(f"â€¢ {m ['item']}: {q } portions")
    lines.append(
        "\nReason: Holt-Winters + seasonality forecast. Full AI analysis refreshing in background..."
    )
    instant_forecast = "\n".join(lines)

    t = threading.Thread(target=_generate_forecast_async, args=(restaurant_id,), daemon=True)
    t.start()

    return instant_forecast


def process_ai_data_ingestion(
    restaurant_id: str, raw_data_input: str, menu_mode: str = "none"
) -> str:
    from services.ai_provider import call_ai_json, ai_available
    from services.cache import invalidate_forecast, invalidate_intelligence, invalidate_db

    if not raw_data_input or not raw_data_input.strip():
        return "No data provided."
    if not ai_available():
        return "No AI provider configured. Add API keys to .env"

    db = load_db_for(restaurant_id)
    restaurant = _get_restaurant(db, restaurant_id)

    if not restaurant:
        return "Restaurant not found."

    today_str = get_today(restaurant).isoformat()

    if menu_mode in ("append", "overwrite"):
        prompt = f"""Parse menu items from the owner of {restaurant['name']} (Indian restaurant in {restaurant.get('region', 'India')}).
Input: '{raw_data_input}'
Intent: {'APPEND TO EXISTING MENU' if menu_mode == 'append' else 'COMPLETELY OVERWRITE MENU'}
Return ONLY valid JSON: {{"parsed_menu": [{{"item": "Proper Name", "base_daily_demand": 60, "profit_margin_inr": 35.00}}]}}
Use realistic Indian restaurant defaults. base_daily_demand: 20-500. profit_margin_inr: 8-200 (Indian Rupees profit per serving).
Item names must be properly capitalised with spaces. Do not concatenate words."""

        data = call_ai_json(prompt)
        if not data:
            return "Could not process menu data. Please try again."
        try:
            new_items = data.get("parsed_menu", data) if isinstance(data, dict) else data
            if not isinstance(new_items, list):
                new_items = [new_items]
            now_iso = datetime.datetime.utcnow().isoformat() + "Z"
            valid = [
                {
                    "item": str(i["item"]),
                    "base_daily_demand": max(1, int(i.get("base_daily_demand", 50))),
                    "profit_margin_rm": max(0.10, float(
                        i.get("profit_margin_inr", i.get("profit_margin_rm", 30.0))
                    )),
                    "added_at": now_iso,   # â† required for demo cleanup TTL
                }
                for i in new_items
                if isinstance(i, dict) and i.get("item")
            ]
            if not valid:
                return "Could not extract valid menu items."
            if menu_mode == "overwrite":
                restaurant["menu"] = valid
                added = len(valid)
            else:
                existing = {m["item"].lower() for m in restaurant.get("menu", [])}
                to_add = [i for i in valid if i["item"].lower() not in existing]
                if not to_add:
                    return "All items already exist. Use Replace Menu to overwrite."
                restaurant.setdefault("menu", []).extend(to_add)
                added = len(to_add)
            _append_memory(restaurant, f"Owner {menu_mode}d menu ({added} items).")
            invalidate_forecast(restaurant_id)
            invalidate_intelligence(restaurant_id)
            invalidate_db()
            save_db_for(restaurant_id, db)

            threading.Thread(
                target=_generate_forecast_async, args=(restaurant_id,), daemon=True
            ).start()
            return f"Menu updated! {added} item(s) added. Forecast regenerating in background."
        except Exception as e:
            print(f"[Ingestion] Menu: {e}")
            return "Menu update failed. Please try again."

    elif menu_mode == "remove":
        prompt = f"""Identify which menu item the owner wants to remove.
Owner message: '{raw_data_input}'
Current menu: {[m['item'] for m in restaurant.get('menu', [])]}
Return ONLY valid JSON: {{"items_to_remove": ["Item 1", "Item 2"]}}
Match the names exactly to the current menu where possible."""

        data = call_ai_json(prompt)
        if not data:
            return "Could not understand which items to remove."
        try:
            to_remove = data.get("items_to_remove", [])
            if not to_remove:
                return "No items identified for removal."
            
            existing = restaurant.get("menu", [])
            to_remove_lower = [i.lower() for i in to_remove]
            new_menu = [m for m in existing if m["item"].lower() not in to_remove_lower]
            
            removed_count = len(existing) - len(new_menu)
            if removed_count == 0:
                return "No matching items found on the menu to remove."
                
            restaurant["menu"] = new_menu
            _append_memory(restaurant, f"Owner removed {removed_count} item(s) from menu.")
            invalidate_forecast(restaurant_id)
            invalidate_intelligence(restaurant_id)
            invalidate_db()
            save_db_for(restaurant_id, db)
            
            threading.Thread(
                target=_generate_forecast_async, args=(restaurant_id,), daemon=True
            ).start()
            return f"Removed {removed_count} item(s) from the menu. Forecast regenerating."
        except Exception as e:
            print(f"[Ingestion] Menu remove: {e}")
            return "Failed to remove items."

    prompt = f"""Analyse owner message from {restaurant ['name']} ({restaurant ['region']}).
Date: {today_str }. Message: '{raw_data_input }'
Return ONLY valid JSON:
{{"sales_summary":"1-2 sentences about what happened","actual_sales_today":{{"Item Name":integer}},"global_market_shift":"Anonymised macro insight for region (no names, no revenue). null if nothing useful."}}
Only include actual_sales_today items where a specific quantity was clearly mentioned."""

    data = call_ai_json(prompt)
    if not data:
        _append_memory(restaurant, raw_data_input[:500])
        invalidate_forecast(restaurant_id)
        invalidate_intelligence(restaurant_id)
        invalidate_db()
        save_db_for(restaurant_id, db)
        threading.Thread(
            target=_generate_forecast_async, args=(restaurant_id,), daemon=True
        ).start()
        return "Data saved to AI memory. Forecast regenerating."

    summary = data.get("sales_summary", raw_data_input[:300])
    actual_sales = data.get("actual_sales_today") or {}
    macro = data.get("global_market_shift") or None

    _append_memory(restaurant, summary)

    if actual_sales and isinstance(actual_sales, dict):
        records = restaurant.setdefault("daily_records", [])
        updated = False
        for rec in records:
            if rec.get("date") == today_str:
                if "actual_sales" not in rec or not rec["actual_sales"]:
                    rec["actual_sales"] = {}
                for k, v in actual_sales.items():
                    if isinstance(v, (int, float)) and v > 0:
                        rec["actual_sales"][k] = rec["actual_sales"].get(k, 0) + int(v)
                updated = True
                break
        if not updated:
            records.append(
                {
                    "date": today_str,
                    "forecast": None,
                    "actual_sales": {
                        k: int(v)
                        for k, v in actual_sales.items()
                        if isinstance(v, (int, float)) and v > 0
                    },
                }
            )
        restaurant["daily_records"] = sorted(records, key=lambda x: x["date"])[-MAX_DAILY_RECORDS:]

    if macro and macro not in (None, "null", ""):
        db.setdefault("global_learning_events", []).append(
            {
                "timestamp": datetime.datetime.now().isoformat(),
                "pattern": str(macro),
            }
        )
        db["global_learning_events"] = db["global_learning_events"][-MAX_GLOBAL_EVENTS:]

    invalidate_forecast(restaurant_id)
    invalidate_intelligence(restaurant_id)
    invalidate_db()
    save_db_for(restaurant_id, db)

    threading.Thread(target=_generate_forecast_async, args=(restaurant_id,), daemon=True).start()

    msg = f"Data saved!\n{summary }"
    if actual_sales:
        msg += f"\nRecorded sales for {len (actual_sales )} item(s). ML models updated."
    if macro and macro not in (None, "null", ""):
        msg += "\nAnonymised insight shared to ecosystem."
    msg += "\nForecast regenerating in background â€” refresh in ~15s."
    return msg


def detect_intent(user_message: str, restaurant: dict) -> dict:
    """
    Classify owner message into an intent using AI (no hardcoded keywords).
    Intents: forecast, event, sales, menu_add, menu_show, menu_remove,
             login, help, greeting, general
    """
    from services.ai_provider import call_ai_json, ai_available

    if not ai_available():
        return {
            "intent": "general",
            "description": None,
            "headcount": None,
            "days": 1,
            "summary": user_message[:100],
        }

    menu_items = [m["item"] for m in restaurant.get("menu", [])]
    today_str = get_today(restaurant).isoformat()
    day_name = get_today(restaurant).strftime("%A")

    rest_name = restaurant.get("name", "this restaurant")
    prompt = (
        f"You are a multi-intent classifier for the owner of a indian food stall called {rest_name }.\n"
        f"Today: {today_str } ({day_name }). Menu: {json .dumps (menu_items )}\n"
        f"Owner message: \"{user_message }\"\n\n"
        "Detect ALL actions/intents in the message. Return a JSON array â€” one object per intent.\n"
        "Intents:\n"
        "1.  \"forecast\"        - wants forecast or how much to prepare today\n"
        "2.  \"event\"           - any gathering: birthday, wedding, party, kenduri, majlis\n"
        "3.  \"sales\"           - reporting quantities sold, leftovers, daily totals\n"
        "4.  \"menu_add\"        - wants to ADD new items to menu\n"
        "5.  \"menu_show\"       - wants to VIEW/LIST current menu items\n"
        "6.  \"menu_remove\"     - wants to REMOVE/DELETE a menu item\n"
        "7.  \"login\"           - wants to switch restaurant or change account\n"
        "8.  \"help\"            - wants to know how to use the app\n"
        "9.  \"greeting\"        - saying hi, hello, selamat pagi, etc.\n"
        "10. \"causal_analysis\" - asking WHY sales dropped, rose, or what caused a change\n"
        "    Extract: item (menu item name or null for global), target_date (YYYY-MM-DD or 'yesterday' or 'today' or null)\n"
        "11. \"menu_engineering\"- asking to analyse or rank menu items by performance\n"
        "    Extract: item (menu item name or null for global)\n"
        "12. \"cv_inventory\"    - asking to scan or photograph stock/ingredients\n"
        "13. \"order_accept\"    - shopkeeper confirms/accepts a pending order.\n"
        "    Extract: order_id (the order reference e.g. ord_abc123)\n"
        "14. \"order_reject\"    - shopkeeper rejects/declines a pending order.\n"
        "    Extract: order_id\n"
        "15. \"order_confirm\"   - confirming an accepted order was picked up / collected.\n"
        "    Extract: order_id\n"
        "16. \"order_miss\"      - reporting an accepted order was NOT picked up.\n"
        "    Extract: order_id\n"
        "17. \"update_discount\" - setting or changing discount for a specific item or globally.\n"
        "    Extract: item (menu item name or null for global), discount_pct (integer 0-70)\n"
        "18. \"fetch_sales\"     - asking for the sales count, performance, or history of a specific item or all items today.\n"
        "    Extract: item (menu item name or null for all), target_date (YYYY-MM-DD or 'yesterday' or 'today' or null)\n"
        "19. \"general\"         - everything else\n\n"
        "Return a JSON array. Each element:\n"
        "{\"intent\":\"...\",\"description\":null,\"headcount\":null,\"days\":1,\"summary\":\"...\","
        "\"order_id\":null,\"item\":null,\"discount_pct\":null,\"target_date\":null}\n"
        "Rules:\n"
        "- For event: fill description (event name), headcount (guess 50 if unknown), days (default 1), target_date.\n"
        "- For order_confirm/order_miss: fill order_id from the message.\n"
        "- For update_discount: fill item (null=global) and discount_pct.\n"
        "- For fetch_sales, causal_analysis, menu_engineering: fill item and target_date if present.\n"
        "- If message has ONE intent, still return a single-element array.\n"
    )
    raw = call_ai_json(prompt)
    valid_intents = {
        "forecast",
        "event",
        "sales",
        "menu_add",
        "menu_show",
        "menu_remove",
        "login",
        "help",
        "greeting",
        "causal_analysis",
        "menu_engineering",
        "cv_inventory",
        "order_confirm",
        "order_miss",
        "update_discount",
        "fetch_sales",
        "general",
    }

    if isinstance(raw, dict):
        raw = [raw]
    if isinstance(raw, list):
        results = []
        for it in raw:
            if not isinstance(it, dict):
                continue
            it.setdefault("intent", "general")
            it.setdefault("description", None)
            it.setdefault("headcount", None)
            it.setdefault("days", 1)
            it.setdefault("summary", user_message[:100])
            it.setdefault("order_id", None)
            it.setdefault("item", None)
            it.setdefault("discount_pct", None)
            it.setdefault("target_date", None)
            if not it.get("days"):
                it["days"] = 1
            if it["intent"] not in valid_intents:
                it["intent"] = "general"
            results.append(it)
        if results:
            return results

    return [
        {
            "intent": "general",
            "description": None,
            "headcount": None,
            "days": 1,
            "summary": user_message[:100],
            "order_id": None,
            "item": None,
            "discount_pct": None,
            "target_date": None,
        }
    ]


def get_accuracy_data(restaurant_id: str) -> list:
    db = load_db_for(restaurant_id)
    restaurant = _get_restaurant(db, restaurant_id)
    if not restaurant:
        return []
    result = []
        # STRICTLY show days that have actual items sold data
    valid_records = [r for r in restaurant.get("daily_records", []) if r.get("actual_sales")]
    valid_records.sort(key=lambda x: x.get("date", ""))
    for rec in valid_records[-7:]:
        try:
            d = datetime.date.fromisoformat(rec["date"])
        except ValueError:
            continue
        actual = (rec.get("actual_sales") or {}).copy()
        
        # Include real-time completed marketplace orders in the graph and breakdown
        for order in restaurant.get("marketplace_orders", []):
            if order.get("date") == rec["date"] and order.get("status") == "completed":
                for item in order.get("items", []):
                    qty = int(item.get("qty", 0))
                    name = item.get("item", "")
                    if name and qty > 0:
                        actual[name] = actual.get(name, 0) + qty
                        
        total = sum(v for v in actual.values() if isinstance(v, (int, float)))
        result.append(
            {
                "date": rec["date"],
                "day": d.strftime("%d %b"),
                "weekday": d.strftime("%a"),
                "has_forecast": bool(rec.get("forecast")),
                "has_actual": bool(actual),
                "total_actual": total,
                "actual_sales": actual,
            }
        )
    return result


def process_image_upload(
    restaurant_id: str, image_bytes: bytes, mime_type: str = "image/jpeg"
) -> str:
    """
    Two-step pipeline: classify image type first, then extract data.
    Rejects screenshots, web pages, and random photos before calling Vision.
    """
    from services.ai_provider import call_ai_with_image, ai_available
    from services.data_miner import check_image_quality
    
    if not ai_available():
        return "No AI provider configured."

    is_ok, reason = check_image_quality(image_bytes)
    if not is_ok:
        return "ðŸ“· Cannot process this photo:\n\n" + reason + "\n\nPlease retake and try again."

    db = load_db_for(restaurant_id)
    restaurant = _get_restaurant(db, restaurant_id)
    if not restaurant:
        return "Restaurant not found."

    rest_name = restaurant["name"]
    menu_items = [m["item"] for m in restaurant.get("menu", [])]
    today_str = get_today(restaurant).isoformat()

    classify_prompt = "\n".join(
        [
            f"You are reviewing an image submitted to a food waste reduction app for {rest_name }.",
            "",
            "Classify this image strictly:",
            "- 'receipt': a printed or handwritten sales receipt, till receipt, cash register printout",
            "- 'whiteboard': a whiteboard/chalkboard showing today's sales, stock counts, or quantities",
            "- 'note': handwritten paper note with food quantities or sales numbers",
            "- 'menu_board': a physical menu board, chalk board menu, or printed menu card",
            "- 'irrelevant': screenshot of a website, random photo, product image, digital screen content, anything NOT a physical food business document",
            "",
            'Return ONLY valid JSON:',
            '{"image_type": "receipt", "is_useful": true, "reason": "one sentence what you see"}',
            "",
            "Be very strict. If in doubt, classify as irrelevant.",
        ]
    )

    classify_raw = call_ai_with_image(classify_prompt, image_bytes, mime_type)
    if not classify_raw:
        return "Could not analyse the image. Please try again or upload a CSV/Excel file."

    def _parse(raw: str) -> dict:
        text = raw.strip()
        if text.startswith("```"):
            parts = text.split("```")
            text = parts[1] if len(parts) > 1 else text
            if text.lower().startswith("json"):
                text = text[4:]
        try:
            return json.loads(text.strip())
        except json.JSONDecodeError:
            return {}

    classify_data = _parse(classify_raw)
    image_type = classify_data.get("image_type", "irrelevant")
    is_useful = classify_data.get("is_useful", False)
    classify_note = classify_data.get("reason", "")

    if not is_useful or image_type == "irrelevant":
        return "\n".join(
            [
                "ðŸ“· This photo cannot be used.",
                "",
                "I detected: " + classify_note,
                "",
                "Please send:",
                "â€¢ ðŸ“„ A receipt or till printout showing what you sold",
                "â€¢ ðŸ–Šï¸ A whiteboard/note with today's quantities",
                "â€¢ ðŸ“‹ A photo of your menu board (to update menu)",
                "",
                "Or upload a CSV/Excel file instead.",
            ]
        )

    is_menu_image = image_type == "menu_board"
    task_line = (
        "Extract food/drink item names and their quantities sold or stock counts."
        if not is_menu_image
        else "Extract food/drink item names only. These are menu items, not sales."
    )

    extract_prompt = "\n".join(
        [
            f"You are extracting data from a {image_type } for {rest_name }.",
            f"Existing menu: {menu_items }",
            f"Today: {today_str }",
            "",
            task_line,
            "",
            "Rules:",
            "- ONLY extract items that are CLEARLY VISIBLE with legible text",
            "- For sales/receipts: only include items where you can see a specific number",
            "- Do NOT guess or invent quantities you cannot see",
            "- Do NOT assume demand of 1 if no number is visible â€” leave those out",
            "",
            "Return ONLY valid JSON:",
            '{"extracted": {"Item Name": quantity_integer_or_null}, "confidence": "high/medium/low", "notes": "brief description"}',
            "",
            "If you cannot read specific numbers, set that item value to null and exclude it.",
        ]
    )

    extract_raw = call_ai_with_image(extract_prompt, image_bytes, mime_type)
    if not extract_raw:
        return "Could not read the data from the image. Please try again or use a CSV file."

    extract_data = _parse(extract_raw)
    raw_extracted = extract_data.get("extracted", {})
    confidence = extract_data.get("confidence", "low")
    notes = extract_data.get("notes", "")

    extracted = {
        k: int(v)
        for k, v in raw_extracted.items()
        if v is not None and isinstance(v, (int, float)) and v > 0
    }

    if not extracted:
        return "\n".join(
            [
                "ðŸ“· Image recognised (" + image_type + ") but no readable numbers found.",
                "",
                notes,
                "",
                "The AI could not extract specific quantities.",
                "Please make sure numbers are clearly visible, or type the data manually.",
            ]
        )

    if confidence == "low" and len(extracted) < 2:
        items_str = ", ".join(f"{k }: {v }" for k, v in extracted.items())
        return "\n".join(
            [
                "ðŸ“· Low confidence extraction (" + image_type + ").",
                "",
                "I could only read: " + items_str,
                notes,
                "",
                "This doesn't look reliable enough to save automatically.",
                "Please type the data directly or use a CSV/Excel file.",
            ]
        )

    lines = [f"{item }: {qty }" for item, qty in extracted.items()]
    text_data = "\n".join(lines)
    mode = "append" if is_menu_image else "none"
    result = process_ai_data_ingestion(restaurant_id, text_data, menu_mode=mode)

    type_labels = {
        "receipt": "receipt",
        "whiteboard": "whiteboard",
        "note": "handwritten note",
        "menu_board": "menu board",
    }
    label = type_labels.get(image_type, image_type)
    return "\n".join(
        [
            f"ðŸ“¸ {label .title ()} processed ({confidence } confidence)!",
            notes,
            "",
            f"Extracted {len (extracted )} item(s).",
            "",
            result,
        ]
    )


def get_shopping_list(restaurant_id: str) -> list:
    """Generate today's ingredient shopping list from forecast quantities."""
    from services.data_miner import compute_item_trends_with_tuning, generate_shopping_list

    db = load_db_for(restaurant_id)
    restaurant = _get_restaurant(db, restaurant_id)
    if not restaurant:
        return []
    trends = compute_item_trends_with_tuning(restaurant, get_today(restaurant).strftime("%A"))
    return generate_shopping_list(restaurant, trends)

