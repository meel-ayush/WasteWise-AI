from __future__ import annotations
import uuid
import datetime
import math
from typing import Optional


def create_chain(owner_email: str, chain_name: str, chain_type: str) -> dict:
    return {
        "chain_id": f"chain_{uuid .uuid4 ().hex [:8 ]}",
        "name": chain_name.strip(),
        "type": chain_type,
        "owner_email": owner_email.lower(),
        "branch_ids": [],
        "created_at": datetime.datetime.utcnow().isoformat(),
        "settings": {
            "shared_menu_enabled": False,
            "cross_branch_transfer": True,
            "consolidated_telegram": True,
            "anomaly_alerts": True,
        },
    }


def add_branch_to_chain(chain_id: str, restaurant_id: str, db: dict) -> bool:
    chain = next((c for c in db.get("chains", []) if c["chain_id"] == chain_id), None)
    if not chain:
        return False
    if restaurant_id not in chain["branch_ids"]:
        chain["branch_ids"].append(restaurant_id)
    for r in db.get("restaurants", []):
        if r["id"] == restaurant_id:
            r["chain_id"] = chain_id
            break
    return True


def _ewma_last(values: list[float], alpha: float = 0.3) -> float:
    if not values:
        return 0.0
    result = values[0]
    for v in values[1:]:
        result = alpha * v + (1 - alpha) * result
    return result


def _branch_stats(restaurant: dict, today_str: str) -> dict:
    records = restaurant.get("daily_records", [])
    recent = sorted(records, key=lambda r: r.get("date", ""))[-8:]
    today_rec = next((r for r in recent if r.get("date") == today_str), {})
    last_7 = [r for r in recent if r.get("date") != today_str]
    item_totals: dict[str, int] = {}
    for r in recent:
        for item_name, qty in r.get("actual_sales", {}).items():
            item_totals[item_name] = item_totals.get(item_name, 0) + qty
    return {
        "revenue_today": today_rec.get("total_revenue_rm", 0),
        "waste_today": today_rec.get("total_waste_qty", 0),
        "revenue_7d_avg": round(
            sum(r.get("total_revenue_rm", 0) for r in last_7) / max(len(last_7), 1), 2
        ),
        "ewma_revenue": round(_ewma_last([r.get("total_revenue_rm", 0) for r in last_7]), 2),
        "top_item": max(item_totals, key=item_totals.get) if item_totals else None,
        "items_sold": item_totals,
    }


def _detect_anomalies(branch_stats: dict[str, dict]) -> list[dict]:
    revenues = [s["revenue_today"] for s in branch_stats.values() if s["revenue_today"] > 0]
    if len(revenues) < 3:
        return []
    mean_rev = sum(revenues) / len(revenues)
    std_rev = math.sqrt(sum((r - mean_rev) ** 2 for r in revenues) / len(revenues)) + 1e-9
    anomalies = []
    for branch_id, stats in branch_stats.items():
        rev = stats["revenue_today"]
        if rev <= 0:
            continue
        z = (rev - mean_rev) / std_rev
        if abs(z) >= 2.0:
            direction = "significantly above" if z > 0 else "significantly below"
            anomalies.append(
                {
                    "branch_id": branch_id,
                    "revenue_today": rev,
                    "z_score": round(z, 2),
                    "direction": direction,
                    "note": f"Revenue ₹{rev:.2f} is {direction} chain average (₹{mean_rev:.2f}). Z={z:.2f}",
                }
            )
    return anomalies


def _transfer_suggestions(branch_stats: dict[str, dict], restaurants: list[dict]) -> list[dict]:
    suggestions = []
    excess: dict[str, list[tuple[str, int]]] = {}
    deficit: dict[str, list[tuple[str, int]]] = {}
    for rest in restaurants:
        bid = rest["id"]
        if bid not in branch_stats:
            continue
        today_str = datetime.date.today().isoformat()
        today_rec = next(
            (r for r in rest.get("daily_records", []) if r.get("date") == today_str), {}
        )
        forecast = today_rec.get("forecast_qty", {})
        for item_data in rest.get("menu", []):
            item = item_data["item"]
            sold = today_rec.get("actual_sales", {}).get(item, 0)
            fcast = forecast.get(item, item_data.get("base_daily_demand", 50))
            remaining = max(0, fcast - sold)
            if remaining > fcast * 0.4:
                excess.setdefault(item, []).append((bid, int(remaining)))
            elif remaining < fcast * 0.1 and sold > 0:
                deficit.setdefault(item, []).append((bid, int(fcast - remaining)))
    for item, excess_branches in excess.items():
        if item not in deficit:
            continue
        for from_id, eq in excess_branches[:1]:
            for to_id, nq in deficit[item][:1]:
                if from_id != to_id and min(eq, nq) >= 3:
                    suggestions.append(
                        {
                            "item": item,
                            "from_branch": from_id,
                            "to_branch": to_id,
                            "qty": min(eq, nq),
                            "reason": f"Transfer {min (eq ,nq )}× {item } from surplus to deficit branch.",
                        }
                    )
    return suggestions[:5]


def get_chain_summary(chain_id: str, db: dict) -> Optional[dict]:
    chain = next((c for c in db.get("chains", []) if c["chain_id"] == chain_id), None)
    if not chain:
        return None
    today_str = datetime.date.today().isoformat()
    branch_ids = chain.get("branch_ids", [])
    restaurants = [r for r in db.get("restaurants", []) if r["id"] in branch_ids]
    bstats = {rest["id"]: _branch_stats(rest, today_str) for rest in restaurants}
    all_items: dict[str, int] = {}
    for s in bstats.values():
        for item, qty in s.get("items_sold", {}).items():
            all_items[item] = all_items.get(item, 0) + qty
    return {
        "chain_id": chain_id,
        "chain_name": chain["name"],
        "branch_count": len(restaurants),
        "total_revenue_rm": round(sum(s["revenue_today"] for s in bstats.values()), 2),
        "total_waste_qty": sum(s["waste_today"] for s in bstats.values()),
        "top_item_chain": max(all_items, key=all_items.get) if all_items else None,
        "branches": [
            {
                "id": r["id"],
                "name": r["name"],
                "region": r.get("region", ""),
                "revenue_today": bstats.get(r["id"], {}).get("revenue_today", 0),
                "ewma_revenue": bstats.get(r["id"], {}).get("ewma_revenue", 0),
                "top_item": bstats.get(r["id"], {}).get("top_item"),
            }
            for r in restaurants
        ],
        "anomalies": _detect_anomalies(bstats),
        "transfer_suggestions": _transfer_suggestions(bstats, restaurants),
        "today_str": today_str,
    }


def push_menu_template_to_chain(chain_id: str, menu: list, db: dict) -> int:
    updated = 0
    for rest in db.get("restaurants", []):
        if rest.get("chain_id") != chain_id:
            continue
        existing = {m["item"].lower(): m for m in rest.get("menu", [])}
        for tpl in menu:
            key = tpl.get("item", "").lower()
            if not key:
                continue
            if key not in existing:
                rest.setdefault("menu", []).append(tpl.copy())
            else:
                existing[key].update({k: v for k, v in tpl.items() if k != "price_rm"})
        updated += 1
    return updated


def format_chain_telegram_summary(chain_id: str, db: dict) -> Optional[str]:
    summary = get_chain_summary(chain_id, db)
    if not summary:
        return None
    chain = next((c for c in db.get("chains", []) if c["chain_id"] == chain_id), None)
    name = chain.get("name", "Your Chain") if chain else "Your Chain"
    lines = [
        f"🏢 *{name } — Daily Chain Report*",
        f"📅 {summary ['today_str']}\n",
        f"📍 {summary['branch_count']} branches | 💰 ₹{summary['total_revenue_rm']:.2f} total revenue",
        f"🗑️ Total waste: {summary ['total_waste_qty']} portions",
    ]
    if summary.get("top_item_chain"):
        lines.append(f"⭐ Best seller: *{summary ['top_item_chain']}*")
    lines.append("\n*Branch Performance:*")
    for branch in sorted(
        summary.get("branches", []), key=lambda b: b["revenue_today"], reverse=True
    ):
        lines.append(
            f"• {branch['name']} ({branch['region']}): ₹{branch['revenue_today']:.2f}"
        )
    if summary.get("anomalies"):
        lines.append("\n⚠️ *Anomalies:*")
        for a in summary["anomalies"]:
            bn = next(
                (b["name"] for b in summary.get("branches", []) if b["id"] == a["branch_id"]),
                a["branch_id"],
            )
            lines.append(f"• {bn }: {a ['note']}")
    if summary.get("transfer_suggestions"):
        lines.append("\n📦 *Transfer Suggestions:*")
        for t in summary["transfer_suggestions"][:2]:
            lines.append(
                f"• {t ['qty']}× {t ['item']}: branch {t ['from_branch'][-4 :]} → {t ['to_branch'][-4 :]}"
            )
    return "\n".join(lines)
