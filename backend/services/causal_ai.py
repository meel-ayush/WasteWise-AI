from __future__ import annotations
import datetime
import math
from typing import Optional

try:
    import numpy as np

    _NP = True
except ImportError:
    _NP = False

MIN_RECORDS = 7
EWMA_ALPHA = 0.25
RAIN_PRIOR = -0.15


def _ewma(values: list[float], alpha: float = EWMA_ALPHA) -> list[float]:
    if not values:
        return []
    result = [values[0]]
    for v in values[1:]:
        result.append(alpha * v + (1 - alpha) * result[-1])
    return result


def _trend_decompose(revenues: list[float]) -> dict:
    n = len(revenues)
    if n < 4:
        return {"trend_slope": 0.0, "baseline": sum(revenues) / n if n else 0, "residual_std": 0.0}
    smoothed = _ewma(revenues)
    if _NP:
        arr = np.array(smoothed)
        x = np.arange(n, dtype=float)
        xm = x.mean()
        ym = arr.mean()
        slope = float(np.sum((x - xm) * (arr - ym)) / (np.sum((x - xm) ** 2) + 1e-9))
        intercept = float(ym - slope * xm)
        predicted = slope * x + intercept
        residuals = arr - predicted
        return {
            "trend_slope": round(slope, 4),
            "baseline": round(float(intercept + slope * (n - 1)), 2),
            "residual_std": round(float(np.std(residuals)), 2),
        }
    else:
        x_vals = list(range(n))
        xm = sum(x_vals) / n
        ym = sum(smoothed) / n
        num = sum((x - xm) * (y - ym) for x, y in zip(x_vals, smoothed))
        den = sum((x - xm) ** 2 for x in x_vals) + 1e-9
        slope = num / den
        intercept = ym - slope * xm
        predicted = [slope * x + intercept for x in x_vals]
        residuals = [s - p for s, p in zip(smoothed, predicted)]
        residual_std = math.sqrt(sum(r**2 for r in residuals) / n)
        return {
            "trend_slope": round(slope, 4),
            "baseline": round(intercept + slope * (n - 1), 2),
            "residual_std": round(residual_std, 2),
        }


def _compute_weekday_multipliers(records: list[dict]) -> dict[str, float]:
    from collections import defaultdict

    bucket: dict[str, list[float]] = defaultdict(list)
    for r in records:
        try:
            dow = datetime.date.fromisoformat(r["date"]).strftime("%A")
            rev = r.get("total_revenue_rm", 0)
            if rev > 0:
                bucket[dow].append(rev)
        except Exception:
            continue
    if not bucket:
        return {}
    overall_mean = sum(v for vals in bucket.values() for v in vals) / max(
        sum(len(v) for v in bucket.values()), 1
    )
    if overall_mean == 0:
        return {}
    multipliers = {}
    for dow, vals in bucket.items():
        n = len(vals)
        raw_mult = (sum(vals) / n) / overall_mean
        shrunk = (n * raw_mult + 3 * 1.0) / (n + 3)
        multipliers[dow] = round(shrunk, 3)
    return multipliers


def _interrupted_time_series(records: list[dict], event_start: str, event_end: str) -> dict:
    pre = sorted([r for r in records if r.get("date", "") < event_start], key=lambda r: r["date"])
    dur = sorted(
        [r for r in records if event_start <= r.get("date", "") <= event_end],
        key=lambda r: r["date"],
    )
    if len(pre) < 3 or not dur:
        return {"lift_rm": 0, "lift_pct": 0, "confidence": "insufficient_data"}
    pre_revs = [r.get("total_revenue_rm", 0) for r in pre]
    pre_trend = _trend_decompose(pre_revs)
    counterfactual = pre_trend["baseline"] + pre_trend["trend_slope"] * len(dur)
    actual_avg = sum(r.get("total_revenue_rm", 0) for r in dur) / len(dur)
    lift_rm = actual_avg - counterfactual
    lift_pct = round(lift_rm / max(counterfactual, 1) * 100, 1)
    conf = "high" if len(dur) >= 3 else "medium" if len(dur) == 2 else "low"
    return {"lift_rm": round(lift_rm, 2), "lift_pct": lift_pct, "confidence": conf}


def _compute_weather_effect_scm(records: list[dict]) -> dict:
    rainy_revs = []
    sunny_revs = []
    for r in records:
        try:
            rev = r.get("total_revenue_rm", 0)
            if rev <= 0:
                continue
            w = r.get("weather", "")
            if any(x in w for x in ("rain", "thunder", "drizzle", "shower")):
                rainy_revs.append(rev)
            elif any(x in w for x in ("sunny", "hot", "warm", "partly")):
                sunny_revs.append(rev)
        except Exception:
            continue
    if len(rainy_revs) < 2 or len(sunny_revs) < 2:
        return {
            "att_pct": RAIN_PRIOR * 100,
            "n_treated": len(rainy_revs),
            "n_control": len(sunny_revs),
            "ci_low": -25.0,
            "ci_high": -5.0,
            "source": "prior",
        }
    mean_rainy = sum(rainy_revs) / len(rainy_revs)
    mean_sunny = sum(sunny_revs) / len(sunny_revs)
    att_pct = round((mean_rainy - mean_sunny) / max(mean_sunny, 1) * 100, 1)
    n = len(rainy_revs)
    std_r = math.sqrt(sum((v - mean_rainy) ** 2 for v in rainy_revs) / max(n - 1, 1))
    se = std_r / math.sqrt(n) / max(mean_sunny, 1) * 100
    return {
        "att_pct": att_pct,
        "n_treated": n,
        "n_control": len(sunny_revs),
        "ci_low": round(att_pct - 1.96 * se, 1),
        "ci_high": round(att_pct + 1.96 * se, 1),
        "source": "data",
    }


def analyse_underperformance(restaurant: dict, target_date: str, item: Optional[str] = None) -> dict:
    records = sorted(restaurant.get("daily_records", []), key=lambda r: r.get("date", ""))[-60:]
    target = next((r for r in records if r.get("date") == target_date), None)
    if not target or len(records) < MIN_RECORDS:
        return {
            "available": False,
            "reason": f"Need at least {MIN_RECORDS } days of records to run causal analysis.",
        }

    def _get_val(r):
        dt = r.get("date")
        m_orders = [o for o in restaurant.get("marketplace_orders", []) if o.get("date") == dt and o.get("status") != "cancelled"]
        if item:
            sales = r.get("actual_sales") or {}
            val = int(sales.get(item, 0))
            for o in m_orders:
                for oi in o.get("items", []):
                    if oi.get("item", "").lower() == item.lower():
                        val += oi.get("qty", 0)
            return val
        
        base_rev = r.get("total_revenue_rm") or 0
        market_rev = sum(o.get("total_rm", 0) for o in m_orders)
        return base_rev + market_rev

    other_revs = [
        _get_val(r)
        for r in records
        if r.get("date") != target_date and _get_val(r) > 0
    ]
    if len(other_revs) < MIN_RECORDS:
        return {"available": False, "reason": "Insufficient data points."}

    decomp = _trend_decompose(other_revs)
    
    # We still use total revenue for computing weekday multipliers, since item-level might be sparse,
    # but optionally we can compute it for the item if we have enough data.
    # Let's keep it simple: weekday multipliers computed on total revenue acts as proxy for footfall.
    weekday_mults = _compute_weekday_multipliers(records)
    target_dow = datetime.date.fromisoformat(target_date).strftime("%A")
    dow_mult = weekday_mults.get(target_dow, 1.0)
    
    expected = decomp["baseline"] * dow_mult
    actual = _get_val(target)
    shortfall = expected - actual
    shortfall_pct = round(shortfall / max(expected, 1) * 100, 1)

    if shortfall_pct < 5:
        return {
            "available": True,
            "message": f"{target_date} performed within 5% of expected ({shortfall_pct:+.1f}%). No underperformance detected.",
            "actual_rm": actual,
            "expected_rm": round(expected, 2),
            "verdict": "No underperformance detected",
            "recommendation": "Your sales are holding steady! Keep doing what you're doing.",
            "factors": {}
        }

    causal_factors = []
    attributed_pct = 0.0
    weather_scm = _compute_weather_effect_scm(records)
    target_weather = target.get("weather") or ""
    is_rainy = any(w in target_weather for w in ("rain", "thunder", "drizzle", "shower"))
    if is_rainy and weather_scm["att_pct"] < -3:
        effect = weather_scm["att_pct"]
        causal_factors.append(
            {
                "factor": "weather",
                "contribution_pct": effect,
                "note": f"Rainy weather reduced your sales by around {abs(effect):.0f}%. People tend to order less on rainy days.",
                "method": "SCM Average Treatment Effect on Treated (ATT)",
            }
        )
        attributed_pct += abs(effect)

    dow_effect_pct = round((dow_mult - 1.0) * 100, 1)
    if abs(dow_effect_pct) > 4:
        causal_factors.append(
            {
                "factor": "day_of_week_seasonality",
                "contribution_pct": dow_effect_pct,
                "note": f"{target_dow}s typically bring in {abs(dow_effect_pct):.0f}% {'less' if dow_effect_pct < 0 else 'more'} revenue than your average day.",
                "method": "Bayesian Shrinkage Estimator",
            }
        )
        attributed_pct += abs(dow_effect_pct)

    for ev in restaurant.get("active_events", []):
        start = ev.get("date") or ev.get("start_date", "")
        end = ev.get("expires_at") or ev.get("end_date", "")
        if start and end and start <= target_date <= end:
            its = _interrupted_time_series(records, start, end)
            if abs(its.get("lift_pct", 0)) > 3:
                causal_factors.append(
                    {
                        "factor": "event_effect",
                        "contribution_pct": its["lift_pct"],
                        "note": f"The '{ev.get('description','unknown')}' event shifted your sales by {its['lift_pct']:+.0f}%.",
                        "method": "Interrupted Time-Series (ITS) Counterfactual",
                    }
                )
            break

    if decomp["trend_slope"] < -2.0:
        weekly_decline = decomp["trend_slope"] * 7
        trend_effect = round(weekly_decline / max(decomp["baseline"], 1) * 100, 1)
        causal_factors.append(
            {
                "factor": "structural_decline",
                "contribution_pct": trend_effect,
                "note": f"Your overall sales have been dropping by about ₹{abs(decomp['trend_slope']):.0f} per day. This is a persistent downward trend.",
                "method": "Linear Trend (OLS on EWMA-smoothed series)",
            }
        )
        attributed_pct += abs(trend_effect)

    residual_pct = max(0.0, shortfall_pct - attributed_pct)
    if residual_pct > 2:
        causal_factors.append(
            {
                "factor": "residual_unexplained",
                "contribution_pct": -residual_pct,
                "note": "We couldn't pinpoint this remaining drop based on weather, weekday, or trends. It might be due to external factors.",
                "method": "Residual after SCM attribution",
            }
        )

    primary = (
        max(causal_factors, key=lambda f: abs(f["contribution_pct"])) if causal_factors else None
    )
    factors_dict = {f["factor"]: f["contribution_pct"] for f in causal_factors}
    recommendation = " ".join([f["note"] for f in causal_factors]) if causal_factors else "No actionable factors found."
    return {
        "available": True,
        "target_date": target_date,
        "actual_revenue_rm": actual,
        "expected_revenue_rm": round(expected, 2),
        "shortfall_rm": round(shortfall, 2),
        "shortfall_pct": shortfall_pct,
        "causal_factors": causal_factors,
        "factors": factors_dict,
        "verdict": f"Underperformed by {shortfall_pct}%",
        "recommendation": recommendation,
        "primary_cause": primary["factor"] if primary else "unknown",
        "trend_info": decomp,
        "weekday_multipliers": weekday_mults,
        "method": "Structural Causal Model (SCM) + ITS + Bayesian ATT",
    }


def format_causal_report_telegram(restaurant: dict, target_date: str, item: Optional[str] = None) -> Optional[str]:
    result = analyse_underperformance(restaurant, target_date, item)
    if not result.get("available"):
        return f"ℹ️ {result .get ('reason','Analysis unavailable.')}"
    if "message" in result:
        return f"✅ {result ['message']}"
    name = restaurant.get("name", "Your Restaurant")
    shortfall_pct = result.get("shortfall_pct", 0)
    
    val_name = "Revenue" if not item else f"Sales for {item}"
    unit = "₹" if not item else ""
    
    lines = [
        f"🔬 *Causal Analysis — {name }*",
        f"📅 Date: {target_date }\n",
        f"📉 {val_name}: {unit}{result['actual_revenue_rm']:.2f} vs expected {unit}{result['expected_revenue_rm']:.2f}",
        f"   *{shortfall_pct :.1f}% shortfall*\n",
        "*Causal Attribution (SCM + ITS):*",
    ]
    emoji_map = {
        "weather": "🌧️",
        "day_of_week_seasonality": "📅",
        "event_effect": "🎉",
        "structural_decline": "📉",
        "residual_unexplained": "❓",
    }
    for factor in result.get("causal_factors", []):
        emoji = emoji_map.get(factor["factor"], "•")
        pct = factor["contribution_pct"]
        lines.append(f"{emoji } *{factor ['factor'].replace ('_',' ').title ()}*: {pct :+.0f}%")
        lines.append(f"   _{factor ['note']}_")
    if result.get("primary_cause"):
        lines.append(
            f"\n💡 *Primary driver: {result ['primary_cause'].replace ('_',' ').title ()}*"
        )
        lines.append(f"_Method: {result .get ('method','SCM')}_")
    return "\n".join(lines)
