from __future__ import annotations
import os, json, datetime, logging, threading, re
from typing import Optional
from concurrent.futures import ThreadPoolExecutor, as_completed

log = logging.getLogger("india_context")
_PROFILE_TTL = 86400


def trigger_intelligence_gathering(
    restaurant_id, lat, lon, force_refresh=False, venue_type="street_stall"
):
    if not lat or not lon:
        return
    threading.Thread(
        target=_gather_and_store,
        args=(restaurant_id, float(lat), float(lon), force_refresh, venue_type),
        daemon=True,
        name=f"intel-{restaurant_id [:8 ]}",
    ).start()


def get_cached_profile_for_prompt(restaurant_id, lat=None, lon=None):
    from services.cache_layer import cache_get

    profile = cache_get(f"intel:profile:{restaurant_id }")
    if profile is None:
        if lat and lon:
            trigger_intelligence_gathering(restaurant_id, lat, lon)
        return ""
    return _format_for_prompt(profile)


def refresh_all_restaurant_profiles():
    try:
        from services.supabase_db import load_database

        for r in load_database().get("restaurants", []):
            if r.get("latitude") and r.get("longitude") and not r.get("is_demo"):
                trigger_intelligence_gathering(
                    r["id"], r["latitude"], r["longitude"], True, r.get("type", "street_stall")
                )
    except Exception as e:
        log.error(f"[Intel] refresh failed: {e }")


def _gather_and_store(restaurant_id, lat, lon, force_refresh, venue_type="street_stall"):
    from services.cache_layer import cache_get, cache_set

    key = f"intel:profile:{restaurant_id }"
    if not force_refresh and cache_get(key):
        return
    raw = {}
    tasks = {
        "osm": lambda: _call_overpass(lat, lon),
        "geo": lambda: _call_locationiq(lat, lon),
        "weather": lambda: _call_open_meteo(lat, lon),
        "wiki": lambda: _call_wikipedia(lat, lon),
    }
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = {pool.submit(fn): name for name, fn in tasks.items()}
        for f in as_completed(futures, timeout=35):
            name = futures[f]
            try:
                raw[name] = f.result()
            except Exception as e:
                log.warning(f"[Intel] {name } failed: {e }")
                raw[name] = {}
    poi = _analyse_pois(raw.get("osm", {}).get("elements", []))
    local = _infer_local(poi, raw.get("geo", {}), raw.get("weather", {}), venue_type)
    profile = _call_ai_synthesis(lat, lon, raw, poi, local) or {}
    profile.update({k: v for k, v in local.items() if k not in profile})
    profile["_crawled_at"] = datetime.datetime.utcnow().isoformat()
    profile["_lat"] = lat
    profile["_lon"] = lon
    cache_set(key, profile, ttl=_PROFILE_TTL)
    try:
        from services.supabase_db import _sb

        if _sb:
            _sb.table("restaurants").update({"region_intelligence": profile}).eq(
                "id", restaurant_id
            ).execute()
    except Exception as e:
        log.warning(f"[Intel] supabase store failed: {e }")
    log.info(
        f"[Intel] done for {restaurant_id }: {profile .get ('city','?')}, {profile .get ('state','?')}"
    )


def _call_overpass(lat, lon):
    import httpx

    r = 1500
    q = f"""[out:json][timeout:30];
(
  node["amenity"~"place_of_worship"](around:{r },{lat },{lon });
  node["amenity"~"restaurant|cafe|fast_food|dhaba|sweet_shop|juice_bar|food_court"](around:{r },{lat },{lon });
  node["amenity"~"school|college|university"](around:2000,{lat },{lon });
  node["amenity"~"coaching_centre|training_centre"](around:2000,{lat },{lon });
  node["amenity"~"bank|atm"](around:{r },{lat },{lon });
  node["amenity"~"hospital|clinic|pharmacy|dispensary"](around:{r },{lat },{lon });
  node["amenity"~"bus_station|bus_stop|taxi|auto_rickshaw"](around:800,{lat },{lon });
  node["shop"~"mall|supermarket|convenience|grocery|vegetable|meat|butcher|sweet|mithai"](around:{r },{lat },{lon });
  node["building"~"apartments|commercial|office|residential"](around:500,{lat },{lon });
  node["amenity"~"cinema|theatre|park|gym|sports_centre|fitness_centre"](around:{r },{lat },{lon });
  node["landuse"~"commercial|residential|industrial"](around:500,{lat },{lon });
);
out body;"""
    try:
        resp = httpx.post("https://overpass-api.de/api/interpreter", data={"data": q}, timeout=35)
        if resp.status_code == 200:
            return resp.json()
    except Exception as e:
        log.warning(f"[Overpass] {e }")
    return {}


def _analyse_pois(elements):
    c = {
        "mosque": 0,
        "temple": 0,
        "gurudwara": 0,
        "church": 0,
        "jain_temple": 0,
        "food_halal": 0,
        "food_veg": 0,
        "food_nonveg": 0,
        "dhaba": 0,
        "school": 0,
        "college": 0,
        "coaching": 0,
        "bank": 0,
        "atm": 0,
        "mall": 0,
        "supermarket": 0,
        "hospital": 0,
        "clinic": 0,
        "pharmacy": 0,
        "bus_stop": 0,
        "auto_stand": 0,
        "apartment": 0,
        "office": 0,
        "cinema": 0,
        "park": 0,
        "gym": 0,
        "butcher_halal": 0,
        "sweet_shop": 0,
    }
    cuisine_list, competitor_names = [], []
    for el in elements:
        t = el.get("tags", {})
        amenity = t.get("amenity", "")
        shop = t.get("shop", "")
        building = t.get("building", "")
        name = t.get("name", "").lower()
        religion = t.get("religion", "").lower()
        cuisine = t.get("cuisine", "").lower()
        is_halal = t.get("diet:halal", "") == "yes" or t.get("halal", "") == "yes"
        is_veg = t.get("diet:vegetarian", "") == "yes" or t.get("vegetarian", "") == "yes"

        if amenity == "place_of_worship":
            if religion == "muslim" or any(
                x in name for x in ("masjid", "mosque", "dargah", "idgah", "khanqah")
            ):
                c["mosque"] += 1
            elif religion == "sikh" or any(
                x in name for x in ("gurudwara", "gurdwara", "singh sabha", "khalsa")
            ):
                c["gurudwara"] += 1
            elif religion == "jain" or any(
                x in name for x in ("jain", "digambar", "shwetambar", "derasar")
            ):
                c["jain_temple"] += 1
            elif religion == "christian" or any(
                x in name for x in ("church", "chapel", "cathedral", "st.")
            ):
                c["church"] += 1
            else:
                c["temple"] += 1

        if amenity in ("restaurant", "cafe", "fast_food", "food_court", "juice_bar"):
            competitor_names.append(name)
            if cuisine:
                cuisine_list.append(cuisine)
            if is_halal or "halal" in name:
                c["food_halal"] += 1
            if is_veg or any(x in name for x in ("pure veg", "udupi", "satvic", "vaishnav")):
                c["food_veg"] += 1
            if cuisine in ("chicken", "mutton", "seafood", "non-vegetarian", "meat", "biryani"):
                c["food_nonveg"] += 1
        if amenity == "dhaba" or "dhaba" in name:
            c["dhaba"] += 1
        if shop == "butcher" and (is_halal or "halal" in name):
            c["butcher_halal"] += 1
        if shop in ("sweet", "mithai") or "sweet" in name or "mithai" in name:
            c["sweet_shop"] += 1
        if shop in ("mall", "shopping_mall"):
            c["mall"] += 1
        if shop in ("supermarket", "convenience", "grocery"):
            c["supermarket"] += 1
        if amenity == "school":
            c["school"] += 1
        if amenity in ("college", "university"):
            c["college"] += 1
        if (
            "coaching" in amenity
            or "coaching" in name
            or "institute" in name
            or "academy" in name
            or "classes" in name
        ):
            c["coaching"] += 1
        if amenity == "bank":
            c["bank"] += 1
        if amenity == "atm":
            c["atm"] += 1
        if amenity == "hospital":
            c["hospital"] += 1
        if amenity in ("clinic", "doctors"):
            c["clinic"] += 1
        if amenity == "pharmacy" or shop == "pharmacy":
            c["pharmacy"] += 1
        if amenity in ("bus_station", "bus_stop"):
            c["bus_stop"] += 1
        if "auto" in amenity or "taxi" in amenity or "auto_stand" in name or "auto stand" in name:
            c["auto_stand"] += 1
        if building in ("apartments", "residential"):
            c["apartment"] += 1
        if building in ("office", "commercial"):
            c["office"] += 1
        if amenity in ("cinema", "theatre"):
            c["cinema"] += 1
        if amenity == "park":
            c["park"] += 1
        if amenity in ("gym", "sports_centre", "fitness_centre"):
            c["gym"] += 1

    total_worship = c["mosque"] + c["temple"] + c["gurudwara"] + c["church"] + c["jain_temple"]
    dominant_religion = "mixed"
    if total_worship > 0:
        rel_map = {
            "muslim": c["mosque"],
            "hindu": c["temple"],
            "sikh": c["gurudwara"],
            "christian": c["church"],
            "jain": c["jain_temple"],
        }
        top = max(rel_map, key=rel_map.get)
        if rel_map[top] >= total_worship * 0.4:
            dominant_religion = top

    total_food = max(1, c["food_halal"] + c["food_veg"] + c["food_nonveg"])
    return {
        "counts": c,
        "dominant_religion": dominant_religion,
        "total_worship_sites": total_worship,
        "halal_food_ratio": round(c["food_halal"] / total_food, 2),
        "veg_food_ratio": round(c["food_veg"] / total_food, 2),
        "top_cuisines": list(dict.fromkeys(cuisine_list))[:8],
        "competitor_names": competitor_names[:10],
        "competitor_count": len(competitor_names),
        "total_education": c["school"] + c["college"] + c["coaching"],
        "is_coaching_hub": (c["coaching"] + c["college"]) >= 3,
        "economic_score": c["bank"] + c["atm"] + c["mall"] * 3 + c["supermarket"],
        "urban_score": c["apartment"] + c["office"] + c["bank"] + c["gym"],
    }


def _call_locationiq(lat, lon):
    import httpx

    key = os.environ.get("LOCATIONIQ_API_KEY", "")
    if not key:
        return {}
    try:
        r = httpx.get(
            "https://us1.locationiq.com/v1/reverse",
            params={"key": key, "lat": lat, "lon": lon, "format": "json", "addressdetails": 1},
            timeout=10,
        )
        if r.status_code == 200:
            data = r.json()
            addr = data.get("address", {})
            return {
                "neighbourhood": addr.get("neighbourhood") or addr.get("suburb", ""),
                "city": addr.get("city") or addr.get("town") or addr.get("village", ""),
                "district": addr.get("county") or addr.get("district", ""),
                "state": addr.get("state", ""),
                "postcode": addr.get("postcode", ""),
                "display": data.get("display_name", ""),
            }
    except Exception as e:
        log.warning(f"[LocationIQ] {e }")
    return {}


def _call_open_meteo(lat, lon):
    import httpx

    today = datetime.date.today()
    start = (today - datetime.timedelta(days=30)).isoformat()
    try:
        r = httpx.get(
            "https://archive-api.open-meteo.com/v1/archive",
            params={
                "latitude": lat,
                "longitude": lon,
                "start_date": start,
                "end_date": today.isoformat(),
                "daily": "temperature_2m_max,temperature_2m_min,precipitation_sum",
                "timezone": "Asia/Kolkata",
            },
            timeout=15,
        )
        if r.status_code == 200:
            d = r.json().get("daily", {})
            temps = [t for t in d.get("temperature_2m_max", []) if t is not None]
            precip = d.get("precipitation_sum", [])
            month = today.month
            avg = round(sum(temps) / len(temps), 1) if temps else 30.0
            return {
                "avg_max_temp": avg,
                "max_temp_30d": round(max(temps), 1) if temps else avg,
                "min_temp_30d": round(min(temps), 1) if temps else avg,
                "rain_days_30d": sum(1 for p in precip if p and p > 1.0),
                "season": (
                    "summer"
                    if month in (3, 4, 5, 6)
                    else (
                        "monsoon"
                        if month in (7, 8, 9)
                        else "winter" if month in (11, 12, 1, 2) else "post_monsoon"
                    )
                ),
                "heat_stress": avg > 38,
            }
    except Exception as e:
        log.warning(f"[OpenMeteo] {e }")
    return {}


def _call_wikipedia(lat, lon):
    import httpx

    try:
        r = httpx.get(
            "https://en.wikipedia.org/w/api.php",
            params={
                "action": "query",
                "list": "geosearch",
                "gscoord": f"{lat }|{lon }",
                "gsradius": 10000,
                "gslimit": 3,
                "format": "json",
            },
            timeout=10,
        )
        if r.status_code != 200:
            return {}
        articles = r.json().get("query", {}).get("geosearch", [])
        if not articles:
            return {}
        r2 = httpx.get(
            f"https://en.wikipedia.org/api/rest_v1/page/summary/{articles [0 ]['title'].replace (' ','_')}",
            headers={"User-Agent": "WasteWise-AI/9.1"},
            timeout=10,
        )
        summary = r2.json().get("extract", "")[:600] if r2.status_code == 200 else ""
        return {
            "primary_article": articles[0]["title"],
            "nearby_articles": [a["title"] for a in articles],
            "summary": summary,
        }
    except Exception as e:
        log.warning(f"[Wikipedia] {e }")
    return {}


def _infer_local(poi, geo, weather, venue_type="street_stall"):
    c = poi.get("counts", {})
    eco = poi.get("economic_score", 0)
    edu = poi.get("total_education", 0)
    area_type = "mixed"
    if poi.get("is_coaching_hub") or edu >= 5:
        area_type = "educational"
    elif eco >= 10 or poi.get("urban_score", 0) >= 8:
        area_type = "urban_commercial"
    elif c.get("office", 0) >= 3:
        area_type = "office_district"
    elif c.get("apartment", 0) >= 5:
        area_type = "residential"
    elif c.get("bus_stop", 0) >= 3:
        area_type = "transit_hub"
    return {
        "city": geo.get("city", ""),
        "neighbourhood": geo.get("neighbourhood", ""),
        "district": geo.get("district", ""),
        "state": geo.get("state", ""),
        "area_type": area_type,
        "venue_type": venue_type,
        "dominant_religion": poi.get("dominant_religion", "mixed"),
        "halal_food_ratio": poi.get("halal_food_ratio", 0),
        "veg_food_ratio": poi.get("veg_food_ratio", 0),
        "is_coaching_hub": poi.get("is_coaching_hub", False),
        "competitor_count": poi.get("competitor_count", 0),
        "top_cuisines": poi.get("top_cuisines", []),
        "economic_tier": "premium" if eco >= 10 else ("mid" if eco >= 5 else "budget"),
        "price_sensitivity": "high" if eco < 5 else ("medium" if eco < 10 else "low"),
        "avg_max_temp": weather.get("avg_max_temp") or 30.0,
        "season": weather.get("season", "unknown"),
        "heat_stress": weather.get("heat_stress", False),
        "rain_days_30d": weather.get("rain_days_30d", 0),
        "nearby_malls": c.get("mall", 0),
        "currency": "INR",
    }


def _call_ai_synthesis(lat, lon, raw, poi, local):
    geo = raw.get("geo", {})
    weather = raw.get("weather", {})
    wiki_summary = raw.get("wiki", {}).get("summary", "N/A")[:400]
    c = poi.get("counts", {})
    venue_type = local.get("venue_type", "street_stall")
    prompt = (
        f"You are analysing a specific food business to understand which external factors actually affect its demand.\n"
        f"Business type: {venue_type }\n"
        f"Location: {geo .get ('city','?')}, {geo .get ('district','?')}, {geo .get ('state','?')}\n"
        f"Neighbourhood: {geo .get ('neighbourhood','?')} | Coords: {lat },{lon }\n"
        f"Climate: avg_max={weather .get ('avg_max_temp','?')}C season={weather .get ('season','?')} rain_days={weather .get ('rain_days_30d','?')}\n"
        f"Nearby malls: {c .get ('mall',0 )} | supermarkets: {c .get ('supermarket',0 )} | offices: {c .get ('office',0 )} | apartments: {c .get ('apartment',0 )}\n"
        f"Religious sites: mosques={c .get ('mosque',0 )} temples={c .get ('temple',0 )} gurudwaras={c .get ('gurudwara',0 )} churches={c .get ('church',0 )} jain={c .get ('jain_temple',0 )}\n"
        f"Dominant religion: {poi .get ('dominant_religion','mixed')} | halal_ratio={poi .get ('halal_food_ratio',0 )} veg_ratio={poi .get ('veg_food_ratio',0 )}\n"
        f"Education: schools={c .get ('school',0 )} colleges={c .get ('college',0 )} coaching={c .get ('coaching',0 )}\n"
        f"Economic: banks={c .get ('bank',0 )} ATMs={c .get ('atm',0 )}\n"
        f"Transport: bus_stops={c .get ('bus_stop',0 )} auto_stands={c .get ('auto_stand',0 )}\n"
        f"Cinemas: {c .get ('cinema',0 )} | parks: {c .get ('park',0 )} | gyms: {c .get ('gym',0 )}\n"
        f"Food competitors nearby: {poi .get ('competitor_count',0 )} | top cuisines: {poi .get ('top_cuisines',[])}\n"
        f"Wikipedia: {wiki_summary }\n\n"
        f"For each metric below, reason from the actual data above whether it affects THIS specific business. "
        f"A food court inside a mall is not affected by rain or heat. A street stall near a park with shade has reduced heat impact. "
        f"A cloud kitchen is unaffected by ALL weather. An office-area restaurant is unaffected by school exam schedules.\n"
        f"Return ONLY valid JSON:\n"
        f"{{\"area_label\":\"str\",\"community_culture\":\"str\","
        f"\"dietary_rules\":[\"str\"],\"popular_dish_types\":[\"str\"],\"dishes_to_avoid\":[\"str\"],"
        f"\"area_type\":\"educational|residential|office_district|transit_hub|market|tourist|mixed|urban_commercial\","
        f"\"economic_tier\":\"budget|mid|premium\",\"price_sensitivity\":\"high|medium|low\","
        f"\"peak_hours\":{{\"weekday\":\"str\",\"sunday\":\"str\"}},"
        f"\"metric_relevance\":{{\n"
        f"  \"rain\":{{\"impact_factor\":0.0_to_1.0,\"reason\":\"str\"}},\n"
        f"  \"heat\":{{\"impact_factor\":0.0_to_1.0,\"reason\":\"str\"}},\n"
        f"  \"coaching_exam_day\":{{\"impact_factor\":-1.0_to_0.0,\"reason\":\"str\"}},\n"
        f"  \"coaching_break\":{{\"impact_factor\":0.0_to_1.0,\"reason\":\"str\"}},\n"
        f"  \"weekend\":{{\"impact_factor\":0.0_to_1.0,\"reason\":\"str\"}},\n"
        f"  \"festival\":{{\"impact_factor\":0.0_to_2.0,\"reason\":\"str\"}},\n"
        f"  \"office_lunch_hour\":{{\"impact_factor\":0.0_to_1.0,\"reason\":\"str\"}}\n"
        f"}},"
        f"\"upcoming_festivals\":[{{\"name\":\"str\",\"religion\":\"str\",\"approx_date\":\"str\",\"demand_impact\":\"str\"}}],"
        f"\"demand_events\":[{{\"name\":\"str\",\"type\":\"str\",\"impact\":\"str\",\"period\":\"str\"}}],"
        f"\"competitor_density\":\"low|medium|high\",\"key_demand_drivers\":[\"str\"],"
        f"\"dominant_religion\":\"str\",\"ai_confidence\":\"high|medium|low\"}}"
    )
    for provider, key in [
        ("gemini", os.environ.get("GEMINI_API_KEY", "")),
        ("groq", os.environ.get("GROQ_API_KEY", "")),
    ]:
        if not key:
            continue
        try:
            import httpx

            if provider == "gemini":
                r = httpx.post(
                    f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={key }",
                    json={
                        "contents": [{"parts": [{"text": prompt}]}],
                        "generationConfig": {"maxOutputTokens": 1500, "temperature": 0.1},
                    },
                    timeout=30,
                )
                if r.status_code == 200:
                    text = r.json()["candidates"][0]["content"]["parts"][0]["text"]
                    m = re.search(r'\{.*\}', text, re.DOTALL)
                    if m:
                        return json.loads(m.group())
            elif provider == "groq":
                r = httpx.post(
                    "https://api.groq.com/openai/v1/chat/completions",
                    headers={"Authorization": f"Bearer {key }"},
                    json={
                        "model": "llama3-8b-8192",
                        "messages": [{"role": "user", "content": prompt}],
                        "max_tokens": 1500,
                        "temperature": 0.1,
                    },
                    timeout=30,
                )
                if r.status_code == 200:
                    text = r.json()["choices"][0]["message"]["content"]
                    m = re.search(r'\{.*\}', text, re.DOTALL)
                    if m:
                        return json.loads(m.group())
        except Exception as e:
            log.warning(f"[Intel AI {provider }] {e }")
    return None


def _format_for_prompt(profile):
    if not profile:
        return ""
    parts = []
    loc = ", ".join(
        filter(
            None,
            [profile.get("neighbourhood", ""), profile.get("city", ""), profile.get("state", "")],
        )
    )
    if loc:
        parts.append(f"Location: {loc }")
    if profile.get("area_label"):
        parts.append(f"Area: {profile ['area_label']}")
    if profile.get("community_culture"):
        parts.append(f"Community: {profile ['community_culture']}")
    if profile.get("dietary_rules"):
        parts.append(f"Dietary rules: {', '.join (profile ['dietary_rules'])}")
    if profile.get("popular_dish_types"):
        parts.append(f"Popular here: {', '.join (profile ['popular_dish_types'][:5 ])}")
    if profile.get("dishes_to_avoid"):
        parts.append(f"Avoid: {', '.join (profile ['dishes_to_avoid'][:3 ])}")

    mr = profile.get("metric_relevance", {})
    for metric, data in mr.items():
        if not isinstance(data, dict):
            continue
        factor = data.get("impact_factor", 0)
        reason = data.get("reason", "")
        if factor == 0 or factor == 0.0:
            continue
        label = metric.replace("_", " ").title()
        parts.append(f"{label }: {factor :+.0%} — {reason }")
    peak = profile.get("peak_hours", {})
    if peak.get("weekday"):
        parts.append(f"Peak hours: {peak ['weekday']}")
    festivals = profile.get("upcoming_festivals", [])
    if festivals:
        f0 = festivals[0]
        parts.append(
            f"Upcoming: {f0 .get ('name')} ~{f0 .get ('approx_date')} ({f0 .get ('demand_impact','+30%')})"
        )
    drivers = profile.get("key_demand_drivers", [])
    if drivers:
        parts.append(f"Demand drivers: {', '.join (drivers )}")
    if not parts:
        return ""
    return "\nLocation intelligence:\n" + "\n".join(f"- {p }" for p in parts) + "\n"
