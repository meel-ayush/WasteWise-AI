import datetime
from services.nlp import get_today


def compute_remaining_inventory(restaurant: dict) -> list:
    today_str = get_today(restaurant).isoformat()
    daily_records = restaurant.get("daily_records", [])
    today_rec = next((r for r in daily_records if r.get("date") == today_str), None)

    actual_sales = {}
    forecast_qtys = {}

    if today_rec:
        actual_sales = today_rec.get("actual_sales") or {}

        forecast_text = today_rec.get("forecast", "")
        if forecast_text:
            for line in forecast_text.splitlines():
                if line.startswith("•"):
                    parts = line[1:].strip().split(":")
                    if len(parts) == 2:
                        item_name = parts[0].strip()
                        qty_part = parts[1].strip().split()[0]
                        try:
                            forecast_qtys[item_name.lower()] = int(qty_part)
                        except ValueError:
                            pass

    ordered_qtys = {}
    for order in restaurant.get("marketplace_orders", []):
        if order.get("date") == today_str and order.get("status") != "cancelled":
            for oi in order.get("items", []):
                key = oi.get("item", "").lower()
                ordered_qtys[key] = ordered_qtys.get(key, 0) + int(oi.get("qty", 0))

    result = []
    for menu_item in restaurant.get("menu", []):
        item_name = menu_item["item"]
        item_key = item_name.lower()

        forecasted = forecast_qtys.get(item_key, menu_item.get("base_daily_demand", 50))

        sold = 0
        for k, v in actual_sales.items():
            if k.lower() == item_key:
                sold = int(v) if isinstance(v, (int, float)) else 0
                break

        sold += ordered_qtys.get(item_key, 0)

        remaining = max(0, forecasted - sold)
        result.append(
            {
                "item": item_name,
                "forecasted": forecasted,
                "sold": sold,
                "remaining": remaining,
                "profit_margin_rm": menu_item.get("profit_margin_rm", 3.0),
                "unit": "portions",
            }
        )

    return result


def compute_discounted_price(original_price_rm: float, discount_pct: int) -> float:
    return round(original_price_rm * (1 - discount_pct / 100), 2)


def compute_profit_split(sale_amount_rm: float) -> dict:
    platform_fee = round(sale_amount_rm * 0.10, 2)
    shopkeeper_earnings = round(sale_amount_rm - platform_fee, 2)
    return {
        "total": sale_amount_rm,
        "shopkeeper": shopkeeper_earnings,
        "platform": platform_fee,
    }


def build_closing_time_telegram_message(restaurant: dict, remaining_items: list) -> str:
    name = restaurant.get("name", "Your Restaurant")
    discount_pct = restaurant.get("discount_pct", 30)
    now_str = datetime.datetime.now().strftime("%d %b %Y, %I:%M %p")

    lines = [
        f"🏪 *Closing Time Report — {name }*",
        f"📅 {now_str }",
        "",
        "📦 *Remaining Inventory:*",
    ]

    total_remaining = 0
    for item in remaining_items:
        if item["remaining"] > 0:
            original_price = item["profit_margin_rm"]
            discounted = compute_discounted_price(original_price, discount_pct)
            lines.append(
                f"• {item ['item']}: *{item ['remaining']}* portions "
                f"(RM {original_price :.2f} → RM {discounted :.2f} at {discount_pct }% off)"
            )
            total_remaining += item["remaining"]

    if total_remaining == 0:
        lines.append("✅ All items sold out — great job today!")
        lines.append("")
        lines.append(
            "🤖 WasteWise AI will use today's perfect sell-through to improve tomorrow's forecast!"
        )
        return "\n".join(lines)

    lines += [
        "",
        f"📊 *Today's Summary:*",
    ]

    today_str = get_today(restaurant).isoformat()
    daily_records = restaurant.get("daily_records", [])
    today_rec = next((r for r in daily_records if r.get("date") == today_str), None)
    if today_rec and today_rec.get("actual_sales"):
        actual = today_rec["actual_sales"]
        total_sold = sum(v for v in actual.values() if isinstance(v, (int, float)))
        lines.append(f"• Total sold today: *{total_sold }* portions")

    lines += [
        f"• Remaining to sell: *{total_remaining }* portions at *{discount_pct }% discount*",
        "",
        "🛍️ *Customer Marketplace is now LIVE!*",
        f"Customers can order remaining stock at {discount_pct }% off.",
        "",
        "💡 _Tip: Share your store link so customers can order!_",
        "",
        "📈 _This sell-through data improves tomorrow's forecast automatically._",
    ]

    return "\n".join(lines)


def get_today_profit_summary(restaurant: dict) -> dict:
    today_str = get_today(restaurant).isoformat()
    daily_records = restaurant.get("daily_records", [])
    today_rec = next((r for r in daily_records if r.get("date") == today_str), None)

    regular_sales_rm = 0.0
    if today_rec and today_rec.get("actual_sales"):
        actual = today_rec["actual_sales"]
        menu_margins = {
            m["item"].lower(): m.get("profit_margin_rm", 3.0) for m in restaurant.get("menu", [])
        }
        for item_name, qty in actual.items():
            margin = menu_margins.get(item_name.lower(), 3.0)
            regular_sales_rm += margin * int(qty) if isinstance(qty, (int, float)) else 0

    marketplace_revenue = 0.0
    platform_fee_total = 0.0
    shopkeeper_marketplace = 0.0

    orders = restaurant.get("marketplace_orders", [])
    today_orders = [o for o in orders if o.get("date") == today_str]
    for order in today_orders:
        if order.get("status") != "cancelled":
            amount = order.get("total_rm", 0.0)
            split = compute_profit_split(amount)
            marketplace_revenue += amount
            platform_fee_total += split["platform"]
            shopkeeper_marketplace += split["shopkeeper"]

    total_shopkeeper = regular_sales_rm + shopkeeper_marketplace
    total_platform = platform_fee_total

    return {
        "date": today_str,
        "regular_sales_rm": round(regular_sales_rm, 2),
        "marketplace_revenue_rm": round(marketplace_revenue, 2),
        "platform_fee_rm": round(total_platform, 2),
        "shopkeeper_earnings_rm": round(total_shopkeeper, 2),
        "total_orders": len(today_orders),
        "completed_orders": len([o for o in today_orders if o.get("status") == "completed"]),
    }


def get_weekly_profit_data(restaurant: dict) -> list:
    import datetime
    result = []
    menu_margins = {
        m["item"].lower(): m.get("profit_margin_rm", 3.0) for m in restaurant.get("menu", [])
    }

    valid_records = [r for r in restaurant.get("daily_records", []) if r.get("actual_sales")]
    valid_records.sort(key=lambda x: x.get("date", ""))
    records = valid_records[-7:]
    for rec in records:
        day_str = rec.get("date")
        if not day_str: continue
        try:
            day = datetime.date.fromisoformat(day_str)
        except:
            continue

        regular_profit = 0.0
        if rec.get("actual_sales"):
            for item_name, qty in rec["actual_sales"].items():
                margin = menu_margins.get(item_name.lower(), 3.0)
                regular_profit += margin * int(qty) if isinstance(qty, (int, float)) else 0

        marketplace_profit = 0.0
        orders = restaurant.get("marketplace_orders", [])
        day_orders = [
            o for o in orders if o.get("date") == day_str and o.get("status") != "cancelled"
        ]
        for order in day_orders:
            split = compute_profit_split(order.get("total_rm", 0.0))
            marketplace_profit += split["shopkeeper"]

        result.append(
            {
                "date": day_str,
                "day": day.strftime("%d %b"),
                "weekday": day.strftime("%a"),
                "regular_profit_rm": round(regular_profit, 2),
                "marketplace_profit_rm": round(marketplace_profit, 2),
                "total_profit_rm": round(regular_profit + marketplace_profit, 2),
            }
        )

    return result


def get_dynamic_discount(closing_time_str: str, closing_discount_pct: int = 30) -> dict:
    if not closing_time_str:
        return {"discount_pct": 0, "label": "Open", "urgency": "none", "minutes_to_close": None}

    try:
        now = datetime.datetime.now()
        h, m = map(int, closing_time_str.split(":"))
        closing_dt = now.replace(hour=h, minute=m, second=0, microsecond=0)

        if closing_dt < now:
            return {
                "discount_pct": 0,
                "label": "Closed",
                "urgency": "closed",
                "minutes_to_close": 0,
            }
        minutes_to_close = int((closing_dt - now).total_seconds() / 60)
    except Exception:
        return {"discount_pct": 0, "label": "Open", "urgency": "none", "minutes_to_close": None}

    if minutes_to_close > 120:
        return {
            "discount_pct": 0,
            "label": "Open",
            "urgency": "none",
            "minutes_to_close": minutes_to_close,
        }
    if minutes_to_close > 60:
        return {
            "discount_pct": 10,
            "label": "Closing Soon",
            "urgency": "low",
            "minutes_to_close": minutes_to_close,
        }
    if minutes_to_close > 30:
        return {
            "discount_pct": 20,
            "label": "Last Hour Deals!",
            "urgency": "medium",
            "minutes_to_close": minutes_to_close,
        }
    return {
        "discount_pct": 35,
        "label": "Almost Closing!",
        "urgency": "high",
        "minutes_to_close": minutes_to_close,
    }


def get_marketplace_menu(restaurant: dict) -> list:
    today_str = get_today(restaurant).isoformat()
    closing_time = restaurant.get("closing_time", "")
    global_discount_pct = restaurant.get("discount_pct", 30)
    dyn = get_dynamic_discount(closing_time, global_discount_pct)
    listings = restaurant.get("marketplace_listings", {})

    remaining_map: dict = {}
    try:
        for r in compute_remaining_inventory(restaurant):
            remaining_map[r["item"].lower()] = r["remaining"]
    except Exception:
        pass

    items = []
    for m in restaurant.get("menu", []):
        item_name = m["item"]
        item_key = item_name.lower()
        cfg = listings.get(item_name, listings.get(item_key, {}))

        if not cfg.get("listed", True):
            continue

        base_price = cfg.get("price_rm") or m.get("price_rm") or m.get("profit_margin_rm", 3.0)

        item_disc = cfg.get("discount_pct")
        if item_disc is not None:
            disc_pct = int(item_disc)
            has_item_discount = True
        else:
            disc_pct = dyn["discount_pct"]
            has_item_discount = False

        final_price = (
            round(base_price * (1 - disc_pct / 100), 2) if disc_pct > 0 else round(base_price, 2)
        )

        raw_remaining = remaining_map.get(item_key, 0)
        qty_available = max(0, raw_remaining)

        items.append(
            {
                "item": item_name,
                "original_price_rm": round(base_price, 2),
                "price_rm": final_price,
                "discount_pct": disc_pct,
                "qty_available": qty_available,
                "is_closing_stock": qty_available > 0,
                "photo_b64": cfg.get("photo_b64"),
                "has_item_discount": has_item_discount,
                "ai_last_action": cfg.get("ai_last_action"),
            }
        )
    return items


def get_all_marketplace_restaurants(db: dict) -> list:
    result = []
    for restaurant in db.get("restaurants", []):
        today_str = get_today(restaurant).isoformat()
        if not restaurant.get("marketplace_enabled", True):
            continue
        if not restaurant.get("menu"):
            continue

        closing_time = restaurant.get("closing_time", "")
        dyn = get_dynamic_discount(closing_time, restaurant.get("discount_pct", 30))

        today_orders = [
            o
            for o in restaurant.get("marketplace_orders", [])
            if o.get("date") == today_str and o.get("status") != "cancelled"
        ]

        menu_items = get_marketplace_menu(restaurant)

        result.append(
            {
                "id": restaurant["id"],
                "name": restaurant["name"],
                "region": restaurant.get("region", "India"),
                "type": restaurant.get("type", "hawker"),
                "closing_time": closing_time,
                "discount_pct": dyn["discount_pct"],
                "discount_label": dyn["label"],
                "urgency": dyn["urgency"],
                "minutes_to_close": dyn["minutes_to_close"],
                "menu": menu_items,
                "total_items": len(menu_items),
                "orders_today": len(today_orders),
                "is_closing_stock": any(i.get("is_closing_stock") for i in menu_items),
            }
        )

    urgency_order = {"high": 0, "medium": 1, "low": 2, "none": 3, "closed": 4}
    result.sort(key=lambda r: urgency_order.get(r["urgency"], 5))
    return result


def get_marketplace_listings(restaurant: dict) -> list:
    listings = restaurant.get("marketplace_listings", {})
    global_disc = restaurant.get("discount_pct", 30)

    remaining_map: dict = {}
    try:
        for r in compute_remaining_inventory(restaurant):
            remaining_map[r["item"].lower()] = r
    except Exception:
        pass

    result = []
    for m in restaurant.get("menu", []):
        item_name = m["item"]
        item_key = item_name.lower()
        cfg = listings.get(item_name, listings.get(item_key, {}))
        inv = remaining_map.get(item_key, {})

        base_price = cfg.get("price_rm") or m.get("price_rm") or m.get("profit_margin_rm", 3.0)
        item_disc = cfg.get("discount_pct")
        eff_disc = item_disc if item_disc is not None else global_disc

        result.append(
            {
                "item": item_name,
                "listed": cfg.get("listed", True),
                "price_rm": round(base_price, 2),
                "menu_price_rm": round(m.get("profit_margin_rm", 3.0), 2),
                "discount_pct": item_disc,
                "effective_discount_pct": eff_disc,
                "photo_b64": cfg.get("photo_b64"),
                "ai_last_action": cfg.get("ai_last_action"),
                "ai_last_action_at": cfg.get("ai_last_action_at"),
                "forecasted": inv.get("forecasted", m.get("base_daily_demand", 50)),
                "sold": inv.get("sold", 0),
                "remaining": inv.get("remaining", 0),
            }
        )
    return result


def ai_optimize_discounts(restaurant: dict) -> dict:
    closing_time = restaurant.get("closing_time", "")
    global_disc = restaurant.get("discount_pct", 30)
    dyn = get_dynamic_discount(closing_time, global_disc)
    listings = restaurant.get("marketplace_listings", {})
    now_iso = datetime.datetime.now().isoformat()

    try:
        remaining_items = compute_remaining_inventory(restaurant)
    except Exception:
        return {"actions": {}, "changes": {}, "global_change": None}

    actions: dict = {}
    changes: dict = {}
    boost_count = 0
    total_count = len(remaining_items)

    for item in remaining_items:
        item_name = item["item"]
        item_key = item_name.lower()
        cfg = {**listings.get(item_name, listings.get(item_key, {}))}

        forecasted = max(1, item["forecasted"])
        remaining = item["remaining"]
        ratio = remaining / forecasted
        old_disc = cfg.get("discount_pct")
        new_disc = old_disc
        action = None
        mins = dyn.get("minutes_to_close")

        if remaining <= 0 and old_disc is not None:
            new_disc = None
            action = f"✅ Sold out — removed item discount."
        elif ratio < 0.15 and remaining > 0:
            new_disc = None
            action = f"🔥 Only {remaining } left ({int (ratio *100 )}% remaining) — scarcity pricing, discount removed."
        elif ratio > 0.60 and mins is not None and mins <= 60:
            new_disc = min(70, global_disc + 15)
            action = f"📈 {remaining } portions unsold with {mins }min to close — boosted to {new_disc }% discount."
            boost_count += 1
        elif ratio > 0.40 and mins is not None and mins <= 120:
            new_disc = min(70, global_disc + 10)
            action = f"⏰ {remaining } portions unsold with ~{mins }min to close — set {new_disc }% discount."
            boost_count += 1

        if action:
            actions[item_name] = {"action": action, "old_disc": old_disc, "new_disc": new_disc}
            cfg["discount_pct"] = new_disc
            cfg["ai_last_action"] = action
            cfg["ai_last_action_at"] = now_iso
            changes[item_name] = cfg

    global_change = None
    if (
        total_count > 0
        and boost_count >= (total_count * 0.7)
        and dyn.get("urgency") in ("medium", "high")
    ):
        global_change = min(70, global_disc + 10)

        for item_name in list(changes.keys()):
            if (
                changes[item_name].get("discount_pct")
                and changes[item_name]["discount_pct"] > global_disc
            ):
                changes[item_name]["discount_pct"] = None
                changes[item_name][
                    "ai_last_action"
                ] = f"🌐 Global discount raised to {global_change }% — item override cleared."

    return {"actions": actions, "changes": changes, "global_change": global_change}


def record_post_closing_learning(restaurant: dict, leftover: dict) -> dict:
    today_str = get_today(restaurant).isoformat()
    daily_records = restaurant.get("daily_records", [])
    today_rec = next((r for r in daily_records if r.get("date") == today_str), None)

    pre_stock = restaurant.get("pre_closing_stock", {})
    disc_pct_used = restaurant.get("pre_closing_discount_pct", restaurant.get("discount_pct", 30))
    menu_margins = {
        m["item"].lower(): m.get("profit_margin_rm", 3.0) for m in restaurant.get("menu", [])
    }
    actual_sales = {}
    if today_rec and today_rec.get("actual_sales"):
        actual_sales = {k.lower(): v for k, v in today_rec["actual_sales"].items()}

    analysis = {
        "date": today_str,
        "items": {},
        "totals": {
            "full_price_revenue_rm": 0.0,
            "discount_revenue_rm": 0.0,
            "waste_qty": 0,
            "waste_loss_rm": 0.0,
            "total_revenue_rm": 0.0,
        },
    }

    for item_name, pre_qty in pre_stock.items():
        item_key = item_name.lower()
        margin = menu_margins.get(item_key, 3.0)
        cost_est = margin * 0.4

        leftover_qty = leftover.get(item_name, leftover.get(item_key, 0))

        discount_qty_sold = max(0, pre_qty - leftover_qty)

        full_price_qty = int(actual_sales.get(item_key, 0))

        full_price_rev = round(margin * full_price_qty, 2)
        discount_price = round(margin * (1 - disc_pct_used / 100), 2)
        discount_rev = round(discount_price * discount_qty_sold, 2)
        waste_loss = round(cost_est * leftover_qty, 2)

        analysis["items"][item_name] = {
            "full_price_sold": full_price_qty,
            "discount_sold": discount_qty_sold,
            "unsold_waste": leftover_qty,
            "discount_pct": disc_pct_used,
            "full_price_revenue_rm": full_price_rev,
            "discount_revenue_rm": discount_rev,
            "waste_loss_rm": waste_loss,
        }

        analysis["totals"]["full_price_revenue_rm"] += full_price_rev
        analysis["totals"]["discount_revenue_rm"] += discount_rev
        analysis["totals"]["waste_qty"] += leftover_qty
        analysis["totals"]["waste_loss_rm"] += waste_loss
        analysis["totals"]["total_revenue_rm"] += full_price_rev + discount_rev

    for k, v in analysis["totals"].items():
        if isinstance(v, float):
            analysis["totals"][k] = round(v, 2)

    if today_rec:
        today_rec["post_closing_analysis"] = analysis
        today_rec["post_closing_leftover"] = {k: int(v) for k, v in leftover.items()}
    else:
        daily_records.append(
            {
                "date": today_str,
                "post_closing_analysis": analysis,
                "post_closing_leftover": {k: int(v) for k, v in leftover.items()},
            }
        )
    restaurant["daily_records"] = daily_records

    return analysis


def format_post_closing_telegram(restaurant: dict, analysis: dict) -> str:
    name = restaurant.get("name", "Your Restaurant")
    t = analysis["totals"]

    lines = [
        f"📊 *End-of-Day Report — {name }*",
        f"📅 {datetime .datetime .now ().strftime ('%d %b %Y')}",
        "",
        "─────────────────────────",
        "💰 *Revenue Breakdown*",
        f"  🏷️  Full-price sales: *RM {t ['full_price_revenue_rm']:.2f}*",
        f"  🔥  Discount sales:   *RM {t ['discount_revenue_rm']:.2f}*",
        f"  📉  Waste (cost lost): RM {t ['waste_loss_rm']:.2f}",
        f"  ✅  Total revenue:    *RM {t ['total_revenue_rm']:.2f}*",
        "",
        "─────────────────────────",
        "🍽️ *Item Breakdown*",
    ]

    for item_name, d in analysis["items"].items():
        tag = ""
        if d["unsold_waste"] == 0 and d["discount_sold"] == 0:
            tag = "🟢 full price sell-through"
        elif d["unsold_waste"] == 0:
            tag = f"🟡 cleared at {d ['discount_pct']}% off"
        elif d["unsold_waste"] > 0:
            tag = f"🔴 {d ['unsold_waste']} portions wasted"
        lines.append(
            f"  • {item_name }: {d ['full_price_sold']} full + {d ['discount_sold']} discounted · {tag }"
        )

    lines += [
        "",
        "─────────────────────────",
        "🧠 *AI Learning*",
        f"  📦 Total waste today: {t ['waste_qty']} portions",
    ]

    if t["waste_qty"] == 0:
        lines.append("  🎉 *Perfect sell-through! Zero waste today!*")
        lines.append("  📈 AI learned: demand was fully met — forecast was accurate.")
    elif t["waste_qty"] <= 5:
        lines.append("  ✅ Very low waste — AI will slightly reduce tomorrow's forecast.")
    else:
        lines.append("  ⚠️ High waste — AI will reduce forecast quantities for these items.")

    lines.append("")
    lines.append("📈 _WasteWise AI has updated your forecast for tomorrow!_")
    return "\n".join(lines)
