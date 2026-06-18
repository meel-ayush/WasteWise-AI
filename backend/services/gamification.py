from __future__ import annotations
import datetime
from typing import Optional

ACCURACY_MILESTONES = [80, 90, 95]


STREAK_MESSAGES = {
    3: "🔥 3-day streak! You're building momentum.",
    7: "⭐ 1 week streak! Your AI accuracy is improving fast.",
    14: "🏆 2-week streak! Your forecast is now 90%+ accurate.",
    21: "💎 3-week streak! You're in the top tier of WasteWise users.",
    30: "👑 30-day streak! Your AI has learned your stall inside out.",
}


def update_streak(restaurant: dict) -> dict:
    today = datetime.date.today().isoformat()
    gam = restaurant.setdefault(
        "gamification",
        {
            "current_streak": 0,
            "longest_streak": 0,
            "last_log_date": None,
            "total_logs": 0,
            "accuracy_milestones": [],
        },
    )

    last_log = gam.get("last_log_date")
    milestone_msg = None

    if last_log == today:

        return {"streak": gam["current_streak"], "milestone_message": None, "already_logged": True}

    yesterday = (datetime.date.today() - datetime.timedelta(days=1)).isoformat()

    if last_log == yesterday:

        gam["current_streak"] = gam.get("current_streak", 0) + 1
    else:

        gam["current_streak"] = 1

    gam["last_log_date"] = today
    gam["total_logs"] = gam.get("total_logs", 0) + 1
    gam["longest_streak"] = max(gam.get("longest_streak", 0), gam["current_streak"])

    streak = gam["current_streak"]
    if streak in STREAK_MESSAGES:
        milestone_msg = STREAK_MESSAGES[streak]

    return {
        "streak": streak,
        "milestone_message": milestone_msg,
        "already_logged": False,
        "total_logs": gam["total_logs"],
    }


def check_accuracy_milestone(restaurant: dict, current_accuracy_pct: float) -> Optional[str]:
    gam = restaurant.setdefault("gamification", {"accuracy_milestones": []})
    achieved = set(gam.get("accuracy_milestones", []))

    for milestone in ACCURACY_MILESTONES:
        if current_accuracy_pct >= milestone and milestone not in achieved:
            achieved.add(milestone)
            gam["accuracy_milestones"] = sorted(list(achieved))

            badge_messages = {
                80: (
                    f"🎯 *Accuracy Milestone: 80%!*\n\n"
                    f"Your AI forecast has reached 80% accuracy. "
                    f"Keep logging daily and it will only get sharper! 📈"
                ),
                90: (
                    f"🏅 *Accuracy Milestone: 90%!*\n\n"
                    f"Outstanding! Your AI is now 90% accurate. "
                    f"You're saving money and reducing waste like a pro. 🌿"
                ),
                95: (
                    f"🏆 *Accuracy Milestone: 95%!*\n\n"
                    f"Elite tier! Your WasteWise AI is among the most accurate in the system. "
                    f"You're a food waste reduction champion! 👑"
                ),
            }
            return badge_messages.get(milestone)

    return None


def format_streak_telegram_message(
    streak_data: dict, restaurant_name: str, language: str = "english"
) -> Optional[str]:
    if streak_data.get("already_logged"):
        return None

    streak = streak_data["streak"]
    milestone_msg = streak_data.get("milestone_message")

    if milestone_msg:
        if language == "malay":
            return f"🔥 *Cabutan berturut-turut hari ke-{streak}!*\n\n{milestone_msg}"
        return f"🔥 *Day {streak} streak!*\n\n{milestone_msg}"

    if streak % 3 == 0 and streak > 0:
        if language == "malay":
            return (
                f"✅ *Hari ke-{streak} berturut-turut!*\n"
                f"Teruskan log jualan harian untuk ketepatan AI yang lebih baik 📊"
            )
        return f"✅ *Day {streak} streak!*\n" f"Keep logging daily to improve your AI accuracy 📊"

    return None


def get_weekly_leaderboard_position(restaurant: dict, all_restaurants: list) -> Optional[dict]:
    region = restaurant.get("region", "")
    if not region:
        return None

    region_restaurants = [
        r for r in all_restaurants if r.get("region") == region and r["id"] != restaurant["id"]
    ]

    if len(region_restaurants) < 4:
        return None

    def weekly_waste_reduction(rest: dict) -> float:
        records = rest.get("daily_records", [])
        today = datetime.date.today()
        this_week_start = today - datetime.timedelta(days=7)
        last_week_start = today - datetime.timedelta(days=14)

        this_week_waste = sum(
            r.get("total_waste_qty", 0)
            for r in records
            if this_week_start.isoformat() <= r.get("date", "") <= today.isoformat()
        )
        last_week_waste = sum(
            r.get("total_waste_qty", 0)
            for r in records
            if last_week_start.isoformat() <= r.get("date", "") < this_week_start.isoformat()
        )

        if last_week_waste == 0:
            return 0.0
        return round((last_week_waste - this_week_waste) / last_week_waste * 100, 1)

    my_reduction = weekly_waste_reduction(restaurant)
    all_reductions = sorted(
        [weekly_waste_reduction(r) for r in region_restaurants] + [my_reduction], reverse=True
    )

    try:
        position = all_reductions.index(my_reduction) + 1
    except ValueError:
        return None

    total = len(all_reductions)
    return {
        "position": position,
        "total": total,
        "my_reduction_pct": my_reduction,
        "region": region,
    }
