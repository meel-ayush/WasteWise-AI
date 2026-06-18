from __future__ import annotations
import datetime
import math
from typing import Optional

try:
    import numpy as np

    _NP = True
except ImportError:
    _NP = False

try:
    from services.ai_provider import call_ai

    _AI = True
except ImportError:
    call_ai = None
    _AI = False


def _ewma(values: list[float], alpha: float = 0.3) -> list[float]:
    if not values:
        return []
    result = [values[0]]
    for v in values[1:]:
        result.append(alpha * v + (1 - alpha) * result[-1])
    return result


def _trend_velocity(values: list[float]) -> float:
    if len(values) < 3:
        return 0.0
    n = len(values)
    xm = (n - 1) / 2
    ym = sum(values) / n
    num = sum((i - xm) * (v - ym) for i, v in enumerate(values))
    den = sum((i - xm) ** 2 for i in range(n)) + 1e-9
    return round((num / den) / max(ym, 1) * 100, 2)


def _compute_contribution_margins(restaurant: dict, daily_records: list[dict]) -> dict[str, dict]:
    menu = {m["item"]: m for m in restaurant.get("menu", [])}
    window = daily_records[-21:] if len(daily_records) >= 21 else daily_records
    totals: dict[str, dict] = {}

    for item_name, item_data in menu.items():
        margin = item_data.get("profit_margin_rm", 0)
        qty_series = []
        for rec in window:
            actual_sales = rec.get("actual_sales") or {}
            sold = actual_sales.get(item_name, 0)
            qty_series.append(sold)
        if not qty_series:
            continue
        smoothed = _ewma(qty_series)
        avg_qty = sum(qty_series) / len(qty_series)
        totals[item_name] = {
            "avg_qty": round(avg_qty, 2),
            "margin_rm": margin,
            "contribution_rm": round(avg_qty * margin, 2),
            "velocity_pct_day": _trend_velocity(
                smoothed[-14:] if len(smoothed) >= 14 else smoothed
            ),
            "qty_series": qty_series,
        }

    total_contribution = sum(v["contribution_rm"] for v in totals.values()) or 1.0
    for v in totals.values():
        v["contribution_pct"] = round(v["contribution_rm"] / total_contribution * 100, 1)
    return totals


def _detect_cannibalization(cm_data: dict[str, dict]) -> list[tuple[str, str, float]]:
    items = list(cm_data.keys())
    pairs = []
    for i in range(len(items)):
        for j in range(i + 1, len(items)):
            a_series = cm_data[items[i]]["qty_series"]
            b_series = cm_data[items[j]]["qty_series"]
            n = min(len(a_series), len(b_series))
            if n < 7:
                continue
            a, b = a_series[-n:], b_series[-n:]
            am, bm = sum(a) / n, sum(b) / n
            num = sum((x - am) * (y - bm) for x, y in zip(a, b))
            den = math.sqrt(sum((x - am) ** 2 for x in a) * sum((y - bm) ** 2 for y in b)) + 1e-9
            r = num / den
            if r < -0.65:
                pairs.append((items[i], items[j], round(r, 3)))
    return pairs


def _compute_hhi(cm_data: dict[str, dict]) -> float:
    pcts = [v["contribution_pct"] / 100 for v in cm_data.values()]
    return round(sum(p**2 for p in pcts), 3)


def classify_menu_items(restaurant: dict) -> dict[str, list[dict]]:
    daily_records = restaurant.get("daily_records", [])
    cm_data = _compute_contribution_margins(restaurant, daily_records)
    if not cm_data:
        return {
            "stars": [],
            "ploughhorses": [],
            "puzzles": [],
            "dogs": [],
            "rising_stars": [],
            "falling_stars": [],
        }

    qty_vals = sorted(v["avg_qty"] for v in cm_data.values())
    margin_vals = sorted(v["margin_rm"] for v in cm_data.values())
    n = len(qty_vals)
    median_qty = qty_vals[n // 2]
    median_mg = margin_vals[n // 2]

    result: dict[str, list[dict]] = {
        "stars": [],
        "ploughhorses": [],
        "puzzles": [],
        "dogs": [],
        "rising_stars": [],
        "falling_stars": [],
    }
    for item, data in cm_data.items():
        hi_demand = data["avg_qty"] >= median_qty
        hi_margin = data["margin_rm"] >= median_mg
        vel = data["velocity_pct_day"]
        entry = {
            "item": item,
            "avg_daily_qty": data["avg_qty"],
            "margin_rm": data["margin_rm"],
            "contribution_pct": data["contribution_pct"],
            "velocity_pct_day": vel,
            "trend": "rising" if vel > 2 else "falling" if vel < -2 else "stable",
        }
        if hi_demand and hi_margin:
            cat = "stars"
        elif hi_demand:
            cat = "ploughhorses"
        elif hi_margin:
            cat = "puzzles"
        else:
            cat = "dogs"
        result[cat].append(entry)
        if cat in ("dogs", "puzzles") and vel > 5:
            result["rising_stars"].append(
                {**entry, "note": f"In '{cat }' but growing {vel :+.1f}%/day."}
            )
        if cat == "stars" and vel < -5:
            result["falling_stars"].append(
                {**entry, "note": f"Star item declining {vel :.1f}%/day — act now."}
            )
    return result


def generate_menu_recommendations(restaurant: dict) -> list[dict]:
    classification = classify_menu_items(restaurant)
    daily_records = restaurant.get("daily_records", [])
    cm_data = _compute_contribution_margins(restaurant, daily_records)
    cannibalization = _detect_cannibalization(cm_data)
    hhi = _compute_hhi(cm_data)
    region = restaurant.get("region", "India")
    rtype = restaurant.get("type", "hawker")
    recs: list[dict] = []

    for entry in classification.get("dogs", []):
        if entry["trend"] == "falling":
            recs.append(
                {
                    "action": "remove",
                    "item": entry["item"],
                    "priority": "high",
                    "reason": f"'{entry ['item']}' has low demand, low margin, and declining at {entry ['velocity_pct_day']:.1f}%/day. Remove to simplify operations and reduce waste.",
                    "evidence": f"Contribution: {entry ['contribution_pct']}% of portfolio",
                }
            )
        else:
            recs.append(
                {
                    "action": "reprice_or_bundle",
                    "item": entry["item"],
                    "priority": "medium",
                    "reason": f"'{entry ['item']}' contributes only {entry ['contribution_pct']}% of revenue. Try bundling with a star item or reducing prep cost.",
                    "evidence": f"Avg qty: {entry ['avg_daily_qty']:.0f}/day, Margin: ₹{entry ['margin_rm']:.2f}",
                }
            )

    for entry in classification.get("falling_stars", []):
        recs.append(
            {
                "action": "investigate_falling_star",
                "item": entry["item"],
                "priority": "critical",
                "reason": f"⚠️ '{entry ['item']}' is your top earner but declining {entry ['velocity_pct_day']:.1f}%/day. Check quality, pricing, or competitor changes.",
                "evidence": entry.get("note", ""),
            }
        )

    for entry in classification.get("rising_stars", []):
        recs.append(
            {
                "action": "promote_rising_star",
                "item": entry["item"],
                "priority": "high",
                "reason": f"'{entry ['item']}' is growing {entry ['velocity_pct_day']:+.1f}%/day despite low current rank. Promote it and increase prep quantity.",
                "evidence": entry.get("note", ""),
            }
        )

    for entry in classification.get("ploughhorses", []):
        recs.append(
            {
                "action": "test_price_increase",
                "item": entry["item"],
                "priority": "medium",
                "reason": f"'{entry ['item']}' sells well ({entry ['avg_daily_qty']:.0f}/day) but margin is below median. A 10-15% price increase likely won't hurt demand.",
                "evidence": f"Margin: ₹{entry ['margin_rm']:.2f}. Velocity: {entry ['velocity_pct_day']:+.1f}%/day",
            }
        )

    for entry in classification.get("puzzles", []):
        recs.append(
            {
                "action": "promote_puzzle",
                "item": entry["item"],
                "priority": "high",
                "reason": f"'{entry ['item']}' makes great profit (₹{entry ['margin_rm']:.2f} per plate) but isn't selling enough. Suggest making it a daily special or improving its visibility on your menu board.",
                "evidence": f"Margin: ₹{entry ['margin_rm']:.2f}",
            }
        )

    for entry in classification.get("stars", []):
        recs.append(
            {
                "action": "maintain_star",
                "item": entry["item"],
                "priority": "low",
                "reason": f"'{entry ['item']}' is your star! It's highly profitable and highly popular. Make sure ingredient quality never drops, and advertise it as your signature dish.",
                "evidence": f"Contribution: {entry ['contribution_pct']}% of portfolio",
            }
        )

    for item_a, item_b, r in cannibalization[:2]:
        recs.append(
            {
                "action": "address_cannibalization",
                "item": f"{item_a } ↔ {item_b }",
                "priority": "medium",
                "reason": f"'{item_a }' and '{item_b }' are strongly negatively correlated (r={r :.2f}). When one sells, the other doesn't. Position them as complements, not alternatives.",
                "evidence": f"Pearson r = {r :.2f}",
            }
        )

    if hhi > 0.35:
        top_item = max(cm_data, key=lambda k: cm_data[k]["contribution_pct"])
        recs.append(
            {
                "action": "diversify_revenue",
                "item": top_item,
                "priority": "medium",
                "reason": f"Your menu is highly concentrated (HHI={hhi :.2f}). '{top_item }' drives {cm_data [top_item ]['contribution_pct']}% of contribution — one bad day hurts you hard.",
                "evidence": f"HHI {hhi :.2f} — healthy range is <0.25",
            }
        )

    if call_ai and len(daily_records) >= 7:
        stars = [e["item"] for e in classification.get("stars", [])]
        dogs = [e["item"] for e in classification.get("dogs", [])]
        puzzles = [e["item"] for e in classification.get("puzzles", [])]
        rising = [e["item"] for e in classification.get("rising_stars", [])]
        prompt = (
            f"You are a menu profit expert for a {rtype } in {region }, India.\n"
            f"Stars (high demand, high margin): {', '.join (stars )or 'none'}\n"
            f"Dogs (low demand, low margin): {', '.join (dogs )or 'none'}\n"
            f"Puzzles (good margin, low demand): {', '.join (puzzles )or 'none'}\n"
            f"Rising items: {', '.join (rising )or 'none'}\n"
            f"Portfolio HHI: {hhi :.2f}\n\n"
            "Give exactly 3 specific, actionable recommendations for this indian food business. "
            "Consider local food culture and seasonal ingredients. Numbered list, max 2 sentences each."
        )
        try:
            ai_text = call_ai(prompt)
            if ai_text:
                recs.append(
                    {
                        "action": "ai_insight",
                        "item": None,
                        "reason": ai_text,
                        "priority": "info",
                        "evidence": "Gemini AI",
                    }
                )
        except Exception:
            pass

    return recs


def get_weekly_menu_report_telegram(restaurant: dict, language: str = "english") -> Optional[str]:
    if len(restaurant.get("daily_records", [])) < 7:
        return None
    classification = classify_menu_items(restaurant)
    recs = generate_menu_recommendations(restaurant)
    cm_data = _compute_contribution_margins(restaurant, restaurant.get("daily_records", []))
    hhi = _compute_hhi(cm_data)
    name = restaurant.get("name", "Your Restaurant")
    lines = [f"📊 *Weekly Menu Intelligence — {name }*\n"]

    stars = classification.get("stars", [])
    if stars:
        lines.append(f"⭐ *Stars:* {', '.join (e ['item']for e in stars [:3 ])}")
    falling = classification.get("falling_stars", [])
    if falling:
        lines.append(f"⚠️ *Falling Stars (act now):* {', '.join (e ['item']for e in falling )}")
    rising = classification.get("rising_stars", [])
    if rising:
        lines.append(f"🚀 *Rising:* {', '.join (e ['item']for e in rising )}")
    dogs = classification.get("dogs", [])
    if dogs:
        lines.append(f"🐕 *Underperformers:* {', '.join (e ['item']for e in dogs [:2 ])}")

    hhi_label = "⚠️ High" if hhi > 0.35 else "✅ Healthy"
    lines.append(f"\n🎯 *Portfolio Diversity (HHI):* {hhi :.2f} — {hhi_label }")
    lines.append("\n*Top Recommendations:*")
    for rec in recs[:3]:
        if rec["action"] == "ai_insight":
            lines.append(rec["reason"])
        elif rec["priority"] in ("critical", "high"):
            emoji = {
                "remove": "🗑️",
                "investigate_falling_star": "🔍",
                "promote_rising_star": "📢",
            }.get(rec["action"], "💡")
            lines.append(f"{emoji } {rec ['reason'][:200 ]}")
    return "\n".join(lines)


def format_engineering_report(restaurant: dict, item: Optional[str] = None) -> str:
    classification = classify_menu_items(restaurant)
    recommendations = generate_menu_recommendations(restaurant)
    emoji_map = {"star": "⭐", "ploughhorse": "🐴", "puzzle": "❓", "dog": "🐶"}
    
    lines = [f"🧠 *Menu Engineering — {restaurant.get('name', 'Your Restaurant')}*\n"]

    if item:
        # Find the category for this item
        item_cat = None
        item_data = None
        for cat, items in classification.items():
            if isinstance(items, list):
                for d in items:
                    if d["item"] == item:
                        item_cat = cat
                        item_data = d
                        break
            if item_cat:
                break
        
        if not item_data:
            return f"❌ Item '{item}' not found in menu analysis data."
            
        category_explanations = {
            "stars": "Top Performer (Highly Popular, Highly Profitable)",
            "ploughhorses": "Crowd Favorite (Highly Popular, Low Profit)",
            "puzzles": "Hidden Gem (Low Popularity, Highly Profitable)",
            "dogs": "Underperformer (Low Popularity, Low Profit)",
            "rising_stars": "Rising Star (Growing Popularity)",
            "falling_stars": "Falling Star (Declining Popularity)",
        }
        
        display_cat = category_explanations.get(item_cat, item_cat.replace('_', ' ').title())
        lines.append(f"{emoji_map.get(item_cat, '')} *{item}* is a _{display_cat}_.")
        lines.append(f"• Usually sells around {item_data['avg_daily_qty']:.0f} portions daily.")
        lines.append(f"• Makes you a profit of ₹{item_data['margin_rm']:.2f} per portion.")
        lines.append(f"• Brings in {item_data['contribution_pct']}% of your total profits overall.")
        
        trend = "Growing" if item_data['velocity_pct_day'] > 0 else "Dropping"
        lines.append(f"• Popularity is {trend} by {abs(item_data['velocity_pct_day']):.1f}% per day.")
        
        item_recs = [r for r in recommendations if r.get("item") == item]
        if item_recs:
            lines.append("\n💡 *Recommendations for this item:*")
            for r in item_recs:
                lines.append(f"• {r['reason']}")
    else:
        # Full report
        for cat, items in classification.items():
            if not isinstance(items, list):
                continue
            if cat in ("rising_stars", "falling_stars"):
                continue
            for d in items:
                lines.append(f"{emoji_map.get(cat, '')} {d['item']} — _{cat.capitalize()}_")
                
        if recommendations:
            lines.append("\n💡 *Top Recommendations:*")
            for r in recommendations[:5]:
                if r.get("action") == "ai_insight":
                    lines.append(f"• {r['reason']}")
                else:
                    lines.append(f"• {r['reason']}")
                    
        lines.append("\n_⭐ Star = high profit + popular | 🐴 Ploughhorse = popular but low margin_")
        lines.append("_❓ Puzzle = high margin but slow | 🐶 Dog = low on both — consider removing_")
        
    return "\n".join(lines)
