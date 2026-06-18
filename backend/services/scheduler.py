import datetime
import threading
import time
import httpx

_scheduler_started = False



# Shared synchronous httpx client for scheduler Telegram notifications.
# Kept open across all calls so keepalive connections survive between
# nightly checkins and the occasional manual trigger.
_tg_sync_client: httpx.Client | None = None
_tg_sync_lock = threading.Lock()


def _get_tg_sync_client() -> httpx.Client:
    """Return (or lazily create) the shared synchronous Telegram HTTP client."""
    global _tg_sync_client
    if _tg_sync_client is None or _tg_sync_client.is_closed:
        with _tg_sync_lock:
            if _tg_sync_client is None or _tg_sync_client.is_closed:
                _tg_sync_client = httpx.Client(
                    transport=httpx.HTTPTransport(
                        retries=1,
                        limits=httpx.Limits(
                            max_connections=5,
                            max_keepalive_connections=3,
                            keepalive_expiry=60,
                        ),
                    ),
                    timeout=httpx.Timeout(connect=35.0, read=30.0, write=15.0, pool=10.0),
                )
    return _tg_sync_client



def _run_daily_checkin(bot_token: str) -> None:
    """8 PM daily: message every owner asking how many they actually sold."""
    from services.nlp import load_database

    db = load_database()
    today_str = datetime.date.today().isoformat()

    for restaurant in db.get("restaurants", []):
        rest_id = restaurant["id"]
        chat_id = restaurant.get("telegram_chat_id")
        if not chat_id:
            continue
        records = restaurant.get("daily_records", [])
        today_rec = next((r for r in records if r.get("date") == today_str), None)
        if today_rec and today_rec.get("actual_sales"):
            continue
        if today_rec and today_rec.get("forecast"):
            lines = ["Ã°Å¸â€œÅ  *Daily Sales Checkin* Ã¢â‚¬â€ How did it go today?\n"]
            for m in restaurant.get("menu", []):
                lines.append(f"Ã¢â‚¬Â¢ {m ['item']} Ã¢â‚¬â€ sold how many?")
            lines.append("\nJust reply naturally: _'Pyaaz Kachori 85, Masala Chai 62'_")
            msg = "\n".join(lines)
        else:
            msg = f"Ã°Å¸â€œÅ  How was today's business at {restaurant ['name']}? Reply with what you sold!"
        pass


def _run_weekly_autotuning() -> None:
    """Every Sunday: Holt-Winters auto-tuning for all restaurants."""
    from services.nlp import load_database, save_database
    from services.data_miner import run_weekly_auto_tune

    if datetime.date.today().strftime("%A") != "Sunday":
        return
    db = load_database()
    for restaurant in db.get("restaurants", []):
        result = run_weekly_auto_tune(restaurant)
        if result["updated_items"]:
            print(
                f"[AutoTune] {restaurant ['name']}: updated {len (result ['updated_items'])} items"
            )
    save_database(db)


def _run_auto_ai_optimizer(bot_token: str) -> None:
    """
    Daily at 10:00 AM: auto-run AI discount optimizer for every restaurant
    that has at least one marketplace item listed. Sends Telegram summary.
    """
    from services.nlp import load_database, save_database
    from services.inventory import ai_optimize_discounts

    db = load_database()
    changed = False

    for restaurant in db.get("restaurants", []):
        chat_id = restaurant.get("telegram_chat_id")

        listings = restaurant.get("marketplace_listings", {})
        listed_items = [k for k, v in listings.items() if v.get("listed")]
        if not listed_items:
            continue

        try:
            result = ai_optimize_discounts(restaurant)
            changed = True

            lines = [f"Ã°Å¸Â¤â€“ *AI Discount Optimizer Ã¢â‚¬â€ {restaurant ['name']}*\n"]
            lines.append(f"_Ran automatically at 10:00 AM_\n")
            updated = result.get("updated_items", [])
            if updated:
                for item_info in updated[:10]:
                    name = item_info.get("item", "")
                    disc = item_info.get("new_discount", 0)
                    note = item_info.get("ai_last_action", "")
                    lines.append(f"Ã¢â‚¬Â¢ *{name }*: {disc }% off Ã¢â‚¬â€ _{note }_")
            else:
                lines.append("No discount changes needed today.")

            lines.append(f"\nÃ°Å¸â€™Â¡ To run manually: tap *AI Optimize* in the dashboard.")
            msg = "\n".join(lines)

            if chat_id:
                pass

            print(f"[AutoOptimizer] Done: {restaurant ['name']} ({len (updated )} items updated)")

        except Exception as e:
            print(f"[AutoOptimizer] Error for {restaurant .get ('id')}: {e }")

    if changed:
        save_database(db)


def _send_telegram(bot_token: str, chat_id: int, text: str) -> bool:
    try:
        return True
    except Exception as e:
        print(f"[Scheduler] Telegram send error: {e}")
        return False


def _auto_generate_pre_closing_stock(restaurant: dict, db: dict, today_str: str) -> None:
    """
    If shopkeeper didn't reply to Stage 1 in 30 min, AI auto-computes remaining
    stock from (forecasted - sold_today) and posts it with dynamic discount.
    """
    from services.nlp import save_database
    from services.inventory import compute_remaining_inventory, get_dynamic_discount

    remaining = compute_remaining_inventory(restaurant)
    closing_time = restaurant.get("closing_time", "")
    dyn = get_dynamic_discount(closing_time, restaurant.get("discount_pct", 30))
    disc_pct = max(dyn["discount_pct"], 10)

    closing_stock = []
    for item in remaining:
        if item["remaining"] > 0:
            orig = item["profit_margin_rm"]
            closing_stock.append(
                {
                    "item": item["item"],
                    "qty_available": item["remaining"],
                    "original_price_rm": orig,
                    "discounted_price_rm": round(orig * (1 - disc_pct / 100), 2),
                    "discount_pct": disc_pct,
                    "source": "ai_auto",
                }
            )

    restaurant["closing_stock"] = closing_stock
    restaurant["closing_stock_date"] = today_str
    restaurant["closing_stock_time"] = datetime.datetime.now().strftime("%H:%M")
    restaurant["pre_closing_stock"] = {s["item"]: s["qty_available"] for s in closing_stock}
    restaurant["pre_closing_discount_pct"] = disc_pct
    restaurant.pop(f"awaiting_pre_closing_inventory_{today_str }", None)
    save_database(db)
    print(
        f"[Scheduler] Auto-generated closing stock for {restaurant ['name']}: {len (closing_stock )} items at {disc_pct }% off"
    )


def _run_closing_time_check(bot_token: str) -> None:
    """
    Every 30 seconds:
    1. 2hrs before close Ã¢â€ â€™ Stage 1: ask shopkeeper for remaining stock
    2. Auto-generate Stage 1 if no reply after 30 min
    3. At closing time Ã¢â€ â€™ Stage 2: ask for final leftover
    """
    from services.nlp import load_database, save_database

    now = datetime.datetime.now()
    today_str = datetime.date.today().isoformat()

    db = load_database()
    changed = False

    for restaurant in db.get("restaurants", []):
        closing_time_str = restaurant.get("closing_time", "")
        if not closing_time_str:
            continue

        chat_id = restaurant.get("telegram_chat_id")
        if not chat_id:
            continue

        try:
            h, m = map(int, closing_time_str.split(":"))
            closing_dt = now.replace(hour=h, minute=m, second=0, microsecond=0)
            minutes_left = int((closing_dt - now).total_seconds() / 60)
        except Exception:
            continue

        stage1_key = f"pre_closing_sent_{today_str }"
        if not restaurant.get(stage1_key) and 115 <= minutes_left <= 125:
            menu_items = [m["item"] for m in restaurant.get("menu", [])]
            disc_pct = restaurant.get("discount_pct", 30)

            lines = [
                f"Ã°Å¸ÂÂª *Pre-Closing Inventory Check Ã¢â‚¬â€ {restaurant ['name']}*",
                "",
                f"Ã¢ÂÂ° You close at *{closing_time_str }* Ã¢â‚¬â€ that's 2 hours from now!",
                "",
                "Ã°Å¸â€œÂ¦ *What stock do you have left right now?*",
                "Reply with item names and quantities. You can also set your own discount!",
                "",
            ]
            if menu_items:
                example = ", ".join(f"{m } 15" for m in menu_items[:3])
                lines.append(f"_Example: '{example }'_")
                lines.append(f"_With custom discount: '{menu_items [0 ]} 15 at 20%'_")
            lines += [
                "",
                f"Ã°Å¸â€™Â¡ Default discount is *{disc_pct }%* off if you don't specify.",
                f"Ã°Å¸â€ºÂÃ¯Â¸Â Items go *LIVE on the marketplace* once you reply!",
                "",
                "_Didn't reply? No worries Ã¢â‚¬â€ AI will auto-post estimates in 30 minutes._",
                "",
                "_Reply 'none' or 'habis' if everything is already sold out._",
            ]

            if _send_telegram(bot_token, chat_id, "\n".join(lines)):
                restaurant[stage1_key] = True
                restaurant[f"pre_closing_sent_at_{today_str }"] = now.isoformat()
                restaurant[f"awaiting_pre_closing_inventory_{today_str }"] = True
                changed = True
                print(
                    f"[Scheduler] Stage 1 sent to {restaurant ['name']} ({minutes_left } min to close)"
                )

        awaiting_s1 = restaurant.get(f"awaiting_pre_closing_inventory_{today_str }")
        sent_at_str = restaurant.get(f"pre_closing_sent_at_{today_str }", "")
        if awaiting_s1 and sent_at_str:
            try:
                sent_at = datetime.datetime.fromisoformat(
                    str(sent_at_str).replace("Z", "+00:00")
                ).replace(tzinfo=None)
                mins_since_sent = int((now - sent_at).total_seconds() / 60)
                if mins_since_sent >= 30:
                    print(
                        f"[Scheduler] No Stage 1 reply from {restaurant ['name']} after 30min Ã¢â‚¬â€ auto-generating"
                    )
                    _auto_generate_pre_closing_stock(restaurant, db, today_str)
                    changed = True
            except Exception as e:
                print(f"[Scheduler] Auto-generate error: {e }")

        stage2_key = f"post_closing_sent_{today_str }"
        if not restaurant.get(stage2_key) and -2 <= minutes_left <= 2:
            pre_stock = restaurant.get("pre_closing_stock", {})
            lines2 = [
                f"Ã°Å¸ÂÂ *Closing Time Ã¢â‚¬â€ {restaurant ['name']}!*",
                "",
                "Ã°Å¸â€œÅ  *Final Check: What's actually left unsold?*",
                "This helps AI learn how accurate today's forecast was.",
                "",
            ]
            if pre_stock:
                lines2.append("We put these items on sale 2hrs ago:")
                for item, qty in pre_stock.items():
                    lines2.append(f"  Ã¢â‚¬Â¢ {item }: {qty } portions listed")
                lines2.append("")
                lines2.append("*How many are still unsold?* Reply like:")
                items_list = list(pre_stock.keys())
                if items_list:
                    example2 = ", ".join(f"{i } 2" for i in items_list[:3])
                    lines2.append(f"_'{example2 }'_")
                    lines2.append(f"_(or 'none' if all sold!)_")
            else:
                lines2 += [
                    "How many portions are still unsold at closing?",
                    "_Reply: 'Pyaaz Kachori 3, Masala Chai 0' or 'none'_",
                ]
            lines2 += [
                "",
                "Ã°Å¸Â§Â  _This data trains the AI to predict better tomorrow!_",
                "Ã°Å¸â€œË† _WasteWise AI tracks: full-price sales + discount sales + waste._",
            ]

            if _send_telegram(bot_token, chat_id, "\n".join(lines2)):
                restaurant[stage2_key] = True
                restaurant[f"awaiting_post_closing_inventory_{today_str }"] = True
                changed = True
                print(f"[Scheduler] Stage 2 sent to {restaurant ['name']}")

    if changed:
        save_database(db)


def _check_pending_orders(bot_token: str) -> None:
    """
    Every 30s: scan all pending marketplace orders.
    - If past pickup_deadline and reminder not sent  Ã¢â€ â€™ send Telegram reminder.
    - If 60 min past pickup_deadline and still pending Ã¢â€ â€™ auto-expire, release stock.
    """
    from services.nlp import load_database, save_database

    now = datetime.datetime.now()
    db = load_database()
    changed = False

    for restaurant in db.get("restaurants", []):
        chat_id = restaurant.get("telegram_chat_id")
        for order in restaurant.get("marketplace_orders", []):
            if order.get("status") != "pending":
                continue

            deadline_str = order.get("pickup_deadline")
            if not deadline_str:
                continue

            try:
                deadline_dt = datetime.datetime.fromisoformat(
                    str(deadline_str).replace("Z", "+00:00")
                ).replace(tzinfo=None)
            except Exception:
                continue

            mins_overdue = int((now - deadline_dt).total_seconds() / 60)

            if mins_overdue < 0:
                continue

            order_id = order.get("order_id", "")
            items_str = ", ".join(
                f"{oi .get ('qty',1 )}x {oi .get ('item','')}" for oi in order.get("items", [])
            )
            customer = order.get("customer_name", "Customer")
            phone = order.get("phone", "")
            total = order.get("total_rm", 0)

            if not order.get("reminder_sent") and 0 <= mins_overdue < 60:
                reminder = (
                    f"\u23f0 *Pickup Reminder Ã¢â‚¬â€ {restaurant ['name']}*\n\n"
                    f"Order `{order_id }` from *{customer }* ({phone })\n"
                    f"\ud83c\udf7d\ufe0f {items_str }\n"
                    f"\U0001f4b0 \u20b9{total:.2f}\n\n"
                    f"Deadline was *{deadline_dt .strftime ('%I:%M %p')}* "
                    f"({mins_overdue } min ago)\n\n"
                    f"Was this order picked up?\n"
                    f"\u2705 `done {order_id }` Ã¢â‚¬â€ Yes, collected\n"
                    f"\u274c `miss {order_id }` Ã¢â‚¬â€ No, not picked up\n\n"
                    f"_Auto-expires in {60 -mins_overdue } min if no response._"
                )
                if chat_id and _send_telegram(bot_token, chat_id, reminder):
                    order["reminder_sent"] = True
                    changed = True
                    print(f"[Orders] Reminder sent for {order_id } ({restaurant ['name']})")

            elif mins_overdue >= 60 and not order.get("auto_expired"):
                order["status"] = "expired"
                order["auto_expired"] = True
                changed = True
                print(f"[Orders] Auto-expired {order_id } ({restaurant ['name']})")
                expiry_msg = (
                    f"\u23f3 *Order Auto-Expired Ã¢â‚¬â€ {restaurant ['name']}*\n\n"
                    f"Order `{order_id }` from *{customer }* ({phone }) was not confirmed\n"
                    f"within 60 minutes of the pickup deadline.\n\n"
                    f"\u2139\ufe0f Status set to *expired*. Stock has been released back\n"
                    f"to inventory for accurate data tracking.\n\n"
                    f"If the customer did collect Ã¢â‚¬â€ reply `done {order_id }` to correct it."
                )
                if chat_id:
                    _send_telegram(bot_token, chat_id, expiry_msg)

    if changed:
        save_database(db)


def _scheduler_loop(bot_token: str) -> None:
    print("[Scheduler] Background learning loop started (2-stage closing alerts)")
    last_checkin_date = None
    last_tune_date = None
    last_optimizer_date = None
    last_pricing_run = 0.0

    while True:
        now = datetime.datetime.now()
        today = datetime.date.today()

        if now.hour == 20 and today != last_checkin_date:
            try:
                _run_daily_checkin(bot_token)
                last_checkin_date = today
            except Exception as e:
                print(f"[Scheduler] Checkin error: {e }")

        if now.hour == 10 and today != last_optimizer_date:
            try:
                _run_auto_ai_optimizer(bot_token)
                last_optimizer_date = today
            except Exception as e:
                print(f"[Scheduler] AutoOptimizer error: {e }")

        if today.strftime("%A") == "Sunday" and today != last_tune_date:
            try:
                _run_weekly_autotuning()
                last_tune_date = today
            except Exception as e:
                print(f"[Scheduler] AutoTune error: {e }")

        if time.time() - last_pricing_run >= 15 * 60 and 6 <= now.hour < 23:
            try:
                from services.pricing_agent import run_for_all

                run_for_all(bot_token)
                last_pricing_run = time.time()
            except Exception as e:
                print(f"[Scheduler] PricingAgent error: {e }")
                last_pricing_run = time.time()

        try:
            _run_closing_time_check(bot_token)
        except Exception as e:
            print(f"[Scheduler] Closing check error: {e }")

        try:
            _check_pending_orders(bot_token)
        except Exception as e:
            print(f"[Scheduler] Order check error: {e}")

        time.sleep(30)


def start_scheduler(bot_token: str) -> None:
    global _scheduler_started
    if _scheduler_started or not bot_token:
        return
    _scheduler_started = True
    t = threading.Thread(target=_scheduler_loop, args=(bot_token,), daemon=True)
    t.start()


