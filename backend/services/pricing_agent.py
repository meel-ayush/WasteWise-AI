from __future__ import annotations
import datetime
import time
from typing import Optional

_state: dict = {}


_WMO_RAIN = {51, 53, 55, 56, 57, 61, 63, 65, 66, 67, 80, 81, 82, 85, 86}
_WMO_THUNDER = {95, 96, 99}
_WMO_DRIZZLE = {51, 53, 55}
_WMO_CLEAR = {0, 1}
_WMO_CLOUDY = {2, 3, 45, 48}


def _wmo_category(code: int) -> str:
    if code in _WMO_THUNDER:
        return "thunderstorm"
    if code in _WMO_RAIN:
        return "rain"
    if code in _WMO_DRIZZLE:
        return "drizzle"
    if code in _WMO_CLEAR:
        return "clear"
    if code in _WMO_CLOUDY:
        return "cloudy"
    return "unknown"


def _is_adverse(category: str) -> bool:
    return category in ("thunderstorm", "rain", "drizzle")


def _get_current_weather(lat: float, lon: float) -> Optional[dict]:
    """
    Fetch current (live) weather conditions + next-2h rain probability.
    Open-Meteo is completely free, no API key required.
    """
    import httpx

    try:
        r = httpx.get(
            "https://api.open-meteo.com/v1/forecast",
            params={
                "latitude": lat,
                "longitude": lon,
                "current": "temperature_2m,precipitation,weather_code,cloud_cover,wind_speed_10m,rain",
                "hourly": "precipitation_probability",
                "timezone": "Asia/Kolkata",
                "forecast_days": 1,
            },
            timeout=10,
        )
        if r.status_code != 200:
            return None
        data = r.json()
        cur = data.get("current", {})
        code = int(cur.get("weather_code", 0))
        rain_mm = float(cur.get("rain", 0))
        precip_mm = float(cur.get("precipitation", 0))
        temp_c = float(cur.get("temperature_2m", 30))
        cloud_pct = float(cur.get("cloud_cover", 0))
        wind_kmh = float(cur.get("wind_speed_10m", 0))

        now_hour = datetime.datetime.now().hour
        hourly = data.get("hourly", {})
        rain_probs = hourly.get("precipitation_probability", [])
        next2h_prob = max(rain_probs[now_hour : now_hour + 3], default=0) if rain_probs else 0

        category = _wmo_category(code)
        rain_intensity = "none"
        if rain_mm > 0 or precip_mm > 0:
            total = max(rain_mm, precip_mm)
            if total >= 10:
                rain_intensity = "heavy"
            elif total >= 3:
                rain_intensity = "moderate"
            elif total >= 0.5:
                rain_intensity = "light"
            else:
                rain_intensity = "trace"
        elif category in ("rain", "drizzle", "thunderstorm"):
            rain_intensity = "light"

        return {
            "code": code,
            "category": category,
            "is_adverse": _is_adverse(category),
            "rain_mm": round(rain_mm + precip_mm, 2),
            "rain_intensity": rain_intensity,
            "temp_c": temp_c,
            "cloud_pct": cloud_pct,
            "wind_kmh": wind_kmh,
            "next2h_rain_pct": next2h_prob,
        }
    except Exception as e:
        print(f"[PricingAgent] Weather fetch error: {e }")
        return None


def _get_coords(rest_id: str, region: str) -> Optional[tuple[float, float]]:
    s = _state.get(rest_id, {})
    if s.get("lat") and s.get("lon"):
        return s["lat"], s["lon"]

    from services.location_intel import geocode_address

    coords = geocode_address(region)
    if coords:
        _state.setdefault(rest_id, {})
        _state[rest_id]["lat"] = coords["lat"]
        _state[rest_id]["lon"] = coords["lon"]
        return coords["lat"], coords["lon"]
    return None


def _minutes_to_prayer(prayer_times: Optional[dict], prayer_key: str) -> Optional[int]:
    """How many minutes until the next prayer? Returns None if >120 min away."""
    if not prayer_times or prayer_key not in prayer_times:
        return None
    try:
        now = datetime.datetime.now()
        pt = prayer_times[prayer_key]
        h, m = map(int, pt.split(":"))
        prayer_dt = now.replace(hour=h, minute=m, second=0, microsecond=0)
        diff_min = int((prayer_dt - now).total_seconds() / 60)
        if 0 <= diff_min <= 120:
            return diff_min
    except Exception:
        pass
    return None


def _in_business_hours(restaurant: dict) -> bool:
    now = datetime.datetime.now()
    if now.hour < 6 or now.hour >= 23:
        return False
    closing_time = restaurant.get("closing_time", "")
    if closing_time:
        try:
            h, m = map(int, closing_time.split(":"))
            close_dt = now.replace(hour=h, minute=m, second=0)
            if now > close_dt + datetime.timedelta(minutes=30):
                return False
        except Exception:
            pass
    return True


def _hours_to_closing(restaurant: dict) -> Optional[float]:
    closing_time = restaurant.get("closing_time", "")
    if not closing_time:
        return None
    try:
        now = datetime.datetime.now()
        h, m = map(int, closing_time.split(":"))
        close_dt = now.replace(hour=h, minute=m, second=0, microsecond=0)
        diff = (close_dt - now).total_seconds() / 3600
        return round(diff, 2) if diff > 0 else None
    except Exception:
        return None


def _time_bucket(now: datetime.datetime) -> str:
    h = now.hour
    if 6 <= h < 10:
        return "breakfast_rush"
    if 10 <= h < 12:
        return "mid_morning"
    if 12 <= h < 15:
        return "lunch_peak"
    if 15 <= h < 17:
        return "afternoon_lull"
    if 17 <= h < 21:
        return "dinner_rush"
    if 21 <= h < 23:
        return "pre_closing"
    return "off_hours"


def _inventory_pressure(restaurant: dict, hours_left: Optional[float]) -> dict:
    """
    Score 0-100 indicating urgency to sell stock.
    100 = must discount heavily, 0 = no pressure.
    """
    listings = restaurant.get("marketplace_listings", {})
    listed_count = sum(1 for v in listings.values() if v.get("listed"))
    total_items = len(restaurant.get("menu", []))

    if listed_count == 0 or total_items == 0:
        return {"score": 0, "listed": 0, "total": total_items}

    listing_ratio = listed_count / max(total_items, 1)
    base_score = listing_ratio * 40

    time_score = 0
    if hours_left is not None:
        if hours_left < 0.5:
            time_score = 60
        elif hours_left < 1:
            time_score = 45
        elif hours_left < 2:
            time_score = 25
        elif hours_left < 3:
            time_score = 10

    total_score = min(100, base_score + time_score)
    return {
        "score": int(total_score),
        "listed": listed_count,
        "total": total_items,
    }


def _ask_ai_for_decision(context: dict) -> Optional[dict]:
    """
    Send all business context to AI. Returns structured pricing decision.
    """
    import json
    from services.ai_provider import call_ai

    r = context["restaurant"]
    wx = context["weather"]
    now = context["now"]
    name = r.get("name", "Unknown")
    region = r.get("region", "India")

    hours_left = context.get("hours_left")
    time_bucket = context.get("time_bucket", "unknown")
    dow = now.strftime("%A")
    rain_impact = r.get("rain_impact", -0.15)
    weekend_mult = r.get("weekend_multiplier", 1.1)
    is_weekend = dow in ("Saturday", "Sunday")
    inv = context.get("inventory", {})
    prayer_alert = context.get("prayer_alert")
    events = r.get("active_events", [])
    current_disc = r.get("discount_pct", 0)
    state = _state.get(r["id"], {})
    agent_disc = state.get("agent_discount", 0)
    last_action = state.get("last_decision", "none")
    prev_weather = state.get("last_weather_category", "unknown")

    if wx:
        wx_summary = (
            f"NOW: {wx ['category'].upper ()} "
            f"({wx ['rain_mm']}mm, intensity={wx ['rain_intensity']}, "
            f"temp={wx ['temp_c']}°C, wind={wx ['wind_kmh']}km/h)\n"
            f"NEXT 2H: {wx ['next2h_rain_pct']}% chance of rain\n"
            f"PREVIOUS: {prev_weather }"
        )
        wx_transition = ""
        if prev_weather != wx['category']:
            if not _is_adverse(prev_weather) and _is_adverse(wx['category']):
                wx_transition = "⚠️ WEATHER JUST WORSENED (was clear/cloudy, now adverse)"
            elif _is_adverse(prev_weather) and not _is_adverse(wx['category']):
                wx_transition = "✅ WEATHER JUST IMPROVED (was adverse, now clearing)"
    else:
        wx_summary = "Weather data unavailable — assume normal"
        wx_transition = ""

    prayer_note = ""
    if prayer_alert:
        prayer_note = f"\n⏰ PRAYER TIME: {prayer_alert ['name']} in {prayer_alert ['minutes']} min — footfall typically drops 20-30% for 30-45 min"

    inv_note = (
        f"Listed items: {inv .get ('listed',0 )}/{inv .get ('total',0 )} menu items on marketplace\n"
        f"Inventory pressure score: {inv .get ('score',0 )}/100"
    )

    events_note = ""
    if events:
        ev_names = [e.get("description", "event")[:30] for e in events[:3]]
        events_note = f"\n📅 EVENTS TODAY: {', '.join (ev_names )} — may increase/decrease footfall"

    prompt = f"""You are an autonomous pricing intelligence AI for "{name }", a hawker food stall in {region }, India.

YOUR MISSION: Maximize total revenue + ensure ALL stock sells before closing. Zero food waste.
You control the global discount % (0-60%). You must justify every decision.

═══════════════════════ CURRENT SITUATION ═══════════════════════

🕐 TIME: {now .strftime ('%I:%M %p')} ({dow }) — {time_bucket .replace ('_',' ')}
⏰ HOURS UNTIL CLOSING: {f'{hours_left :.1f}h'if hours_left else 'not set (assume 3h left)'}
📅 DAY TYPE: {'Weekend'if is_weekend else 'Weekday'} (demand multiplier: {weekend_mult :.1f}x)

═══════════════════════ WEATHER ═══════════════════════
{wx_summary }
{wx_transition }
📊 Historical rain impact on this stall: {int (abs (rain_impact )*100 )}% fewer customers when raining

═══════════════════════ INVENTORY ═══════════════════════
{inv_note }

═══════════════════════ PRICING HISTORY ═══════════════════════
Current discount: {current_disc }%
Agent last set: {agent_disc }% (action: {last_action })
{prayer_note }{events_note }

═══════════════════════ DECISION RULES ═══════════════════════
1. WEATHER ADVERSE + many hours left → moderate discount to attract early customers
2. WEATHER ADVERSE + near closing + stock remaining → higher discount, urgency to clear
3. WEATHER CLEARS → reduce weather-driven discount, return toward profit-optimal
4. NEAR CLOSING (< 1.5h) + high inventory pressure → raise discount to avoid waste
5. PEAK HOURS + good weather → minimize discount, maximize profit per item
6. PRAYER TIME approaching → slight pre-prayer discount burst to capture customers before
7. VERY NEAR CLOSING (< 30 min) + stock left → maximum practical discount
8. Stock already low + selling well → REMOVE discount, don't over-discount fast movers
9. Next 2h rain likely → proactively apply discount before rain starts
10. Rain has stopped → begin removing weather discount gradually

Respond ONLY with valid JSON (no markdown fences):
{{
  "action": "apply_discount" | "increase_discount" | "decrease_discount" | "remove_discount" | "no_change",
  "recommended_discount_pct": <integer 0-60>,
  "confidence": "high" | "medium" | "low",
  "primary_trigger": "<the main reason for this decision in 5 words>",
  "reasoning": "<2-3 sentence explanation combining ALL factors>",
  "estimated_revenue_impact": "<e.g. +15% volume, -5% margin = net +10%>",
  "will_stock_clear": <true | false>,
  "notify_owner": <true | false>,
  "telegram_message": "<friendly 3-4 line owner message (only if notify_owner=true, else empty string)>"
}}"""

    try:
        raw = call_ai(prompt)
        clean = raw.strip()
        if "```" in clean:
            lines = clean.split("\n")
            clean = "\n".join(l for l in lines if not l.strip().startswith("```"))
        result = json.loads(clean)
        result["recommended_discount_pct"] = max(
            0, min(60, int(result.get("recommended_discount_pct", 0)))
        )
        return result
    except Exception as e:
        print(f"[PricingAgent] AI parse error: {e }")
        return None


def _apply_decision(
    restaurant: dict,
    db: dict,
    decision: dict,
    bot_token: str,
) -> bool:
    """
    Apply the AI's pricing decision to the restaurant record.
    Returns True if discount was changed (DB needs saving).
    """

    rest_id = restaurant["id"]
    chat_id = restaurant.get("telegram_chat_id")
    action = decision.get("action", "no_change")
    new_disc = decision.get("recommended_discount_pct", restaurant.get("discount_pct", 0))
    old_disc = restaurant.get("discount_pct", 0)
    s = _state.setdefault(rest_id, {})

    changed = False

    if action != "no_change" and new_disc != old_disc:

        if s.get("agent_discount", 0) == 0 and action != "remove_discount":
            s["base_discount"] = old_disc

        restaurant["discount_pct"] = new_disc
        s["agent_discount"] = new_disc if action != "remove_discount" else 0
        s["last_action_time"] = time.time()
        s["last_decision"] = action
        changed = True
        print(
            f"[PricingAgent] {restaurant ['name']}: {action } → {new_disc }% "
            f"(was {old_disc }%) | {decision .get ('primary_trigger','')}"
        )

    now_epoch = time.time()
    last_notify = s.get("last_notify_time", 0)
    cooldown_ok = (now_epoch - last_notify) >= 20 * 60

    msg = decision.get("telegram_message", "").strip()
    if decision.get("notify_owner") and msg and chat_id and cooldown_ok:
        s["last_notify_time"] = now_epoch

    return changed


def run_for_restaurant(
    restaurant: dict,
    db: dict,
    bot_token: str,
) -> bool:
    """
    Main entry point called by scheduler for each restaurant.
    Returns True if DB needs to be saved.
    """
    rest_id = restaurant["id"]

    if not _in_business_hours(restaurant):
        return False

    listings = restaurant.get("marketplace_listings", {})
    listed_count = sum(1 for v in listings.values() if v.get("listed"))

    s = _state.get(rest_id, {})
    if time.time() - s.get("last_action_time", 0) < 12 * 60 and listed_count == 0:
        return False

    region = restaurant.get("region", "")
    coords = _get_coords(rest_id, region)

    weather = None
    if coords:
        weather = _get_current_weather(coords[0], coords[1])

    if weather:
        _state.setdefault(rest_id, {})["last_weather_code"] = weather["code"]
        _state.setdefault(rest_id, {})["last_weather_category"] = weather["category"]

    prayer_alert = None
    if coords:
        try:
            from services.location_intel import get_prayer_times

            pt = get_prayer_times(coords[0], coords[1])
            for prayer_key, prayer_name in [
                ("dhuhr", "Zuhr"),
                ("asr", "Asr"),
                ("maghrib", "Maghrib"),
            ]:
                mins = _minutes_to_prayer(pt, prayer_key)
                if mins is not None and 5 <= mins <= 45:
                    prayer_alert = {"name": prayer_name, "minutes": mins}
                    break
        except Exception:
            pass

    now = datetime.datetime.now()
    hours_left = _hours_to_closing(restaurant)
    inv_data = _inventory_pressure(restaurant, hours_left)

    context = {
        "restaurant": restaurant,
        "weather": weather,
        "now": now,
        "hours_left": hours_left,
        "time_bucket": _time_bucket(now),
        "inventory": inv_data,
        "prayer_alert": prayer_alert,
    }

    decision = _ask_ai_for_decision(context)
    if not decision:
        return False

    return _apply_decision(restaurant, db, decision, bot_token)


def run_for_all(bot_token: str) -> None:
    """
    Called by scheduler every 15 minutes.
    Iterates all restaurants and runs the pricing agent.
    """
    from services.nlp import load_database, save_database

    db = load_database()
    changed = False

    for restaurant in db.get("restaurants", []):
        try:
            if run_for_restaurant(restaurant, db, bot_token):
                changed = True
        except Exception as e:
            print(f"[PricingAgent] Error for {restaurant .get ('id','?')}: {e }")

    if changed:
        save_database(db)
        print("[PricingAgent] Database saved after pricing updates.")
