import datetime
import re
from typing import Optional, Tuple
from dataclasses import dataclass
from collections import defaultdict


@dataclass
class ItemTrend:
    item: str

    holt_level: float
    holt_trend: float
    holt_forecast: float
    ewma: float

    recent_avg: float
    older_avg: float
    trend_pct: float
    trend_dir: str
    velocity: float
    velocity_dir: str

    weekday_mult: float

    sample_size: int
    confidence: str
    has_anomaly: bool
    anomaly_note: str

    recommended_qty: float


@dataclass
class EcosystemSignal:
    item_pattern: str
    direction: str
    restaurant_count: int
    avg_shift_pct: float
    signal_strength: str
    region_types: list
    confidence_score: float


@dataclass
class LearnedMultipliers:
    weekday: dict
    weather_signals: dict
    overall_confidence: str
    best_day: str
    worst_day: str


def arima_forecast(series: list, p: int = 2, d: int = 1) -> float:
    """
    ARIMA(p,d,0) in pure Python — no statsmodels dependency.
    d=1 differencing removes trend. AR(p) captures autocorrelation.
    Returns next-period point forecast.
    """
    s = [float(x) for x in series]
    if len(s) < p + d + 2:
        return s[-1] if s else 0.0
    diff = s[:]
    for _ in range(d):
        diff = [diff[i + 1] - diff[i] for i in range(len(diff) - 1)]
    if len(diff) < p + 1:
        return max(0.0, s[-1])
    n = len(diff)
    X = [[diff[t - k] for k in range(1, p + 1)] for t in range(p, n)]
    y = [diff[t] for t in range(p, n)]
    if p == 1:
        num = sum(X[i][0] * y[i] for i in range(len(y)))
        den = sum(X[i][0] ** 2 for i in range(len(y)))
        beta = [num / den if den != 0 else 0.0]
    elif p == 2:
        X1 = [x[0] for x in X]
        X2 = [x[1] for x in X]
        s11 = sum(a * a for a in X1)
        s12 = sum(a * b for a, b in zip(X1, X2))
        s22 = sum(b * b for b in X2)
        sy1 = sum(a * c for a, c in zip(X1, y))
        sy2 = sum(b * c for b, c in zip(X2, y))
        det = s11 * s22 - s12 * s12
        if abs(det) < 1e-10:
            beta = [0.0, 0.0]
        else:
            beta = [(sy1 * s22 - sy2 * s12) / det, (sy2 * s11 - sy1 * s12) / det]
    else:
        beta = [0.0] * p
    last_diffs = diff[-p:]
    next_diff = sum(beta[k] * last_diffs[-(k + 1)] for k in range(min(p, len(last_diffs))))
    result = next_diff
    for i in range(d):
        result += s[-1 - i]
    return round(max(0.0, result), 1)


def ensemble_forecast(
    series: list, hw_alpha: float = 0.4, hw_beta: float = 0.2, hw_weight: float = 0.6
) -> Tuple[float, float, float, float]:
    """
    Weighted ensemble of Holt-Winters and ARIMA forecasts.
    Research shows ensemble reduces MAPE by 15-30% vs single models.
    Returns (ensemble_fc, hw_fc, arima_fc, confidence_interval_half_width).
    """
    clean = clean_series([float(x) for x in series])
    if len(clean) < 3:
        v = clean[-1] if clean else 0.0
        return v, v, v, v * 0.3
    _, _, hw_fc = holt_winters(clean, hw_alpha, hw_beta)
    ar_fc = arima_forecast(clean)
    ensemble_val = hw_weight * hw_fc + (1.0 - hw_weight) * ar_fc
    ensemble_val = round(max(0.0, ensemble_val), 1)

    if len(clean) >= 5:
        residuals = [
            abs(clean[i] - ensemble_val) for i in range(max(0, len(clean) - 5), len(clean))
        ]
        ci_half = round(sum(residuals) / len(residuals) * 1.28, 1)
    else:
        ci_half = round(ensemble_val * 0.25, 1)
    return ensemble_val, round(hw_fc, 1), round(ar_fc, 1), ci_half


def actionable_accuracy_notes(mape_data: dict, restaurant: dict) -> list:
    """
    For every item with accuracy below 70%, return a plain-English sentence
    the owner can actually act on. No jargon, no percentages — just what to change.
    """
    notes = []
    records = [r for r in restaurant.get("daily_records", []) if r.get("actual_sales")]
    for item, data in mape_data.items():
        accuracy = max(0, 100 - data["mape"])
        if accuracy >= 70:
            continue
        bias = data.get("bias", 0)
        n = data.get("n", 0)
        if n < 3:
            continue
        over = bias > 10
        under = bias < -10
        pct = abs(round(bias))

        weekday_over, weekday_under = None, None
        if records:
            weekday_errors: dict = {}
            pattern = re.compile(
                rf"(?:•\s*)?(?:\*\*)?\s*{re.escape(item)}\s*(?:\*\*)?\s*[:\-]\s*(\d+)",
                re.IGNORECASE,
            )
            for rec in records:
                actual = rec.get("actual_sales", {}).get(item)
                if actual is None or actual <= 0:
                    continue
                m = pattern.search(rec.get("forecast", ""))
                if m:
                    try:
                        d = datetime.date.fromisoformat(rec["date"])
                        wd = d.strftime("%A")
                        err = (float(m.group(1)) - actual) / actual
                        weekday_errors.setdefault(wd, []).append(err)
                    except (ValueError, AttributeError):
                        pass
            if weekday_errors:
                for wd, errs in weekday_errors.items():
                    avg = sum(errs) / len(errs)
                    if avg > 0.20 and len(errs) >= 2:
                        weekday_over = wd
                    elif avg < -0.20 and len(errs) >= 2:
                        weekday_under = wd

        if weekday_over:
            notes.append(
                {
                    "item": item,
                    "note": f"The AI over-prepares {item } every {weekday_over } by ~{pct }%. "
                    f"I've started adjusting {weekday_over } forecasts down automatically.",
                    "action": "forecast_adjusted",
                }
            )
        elif weekday_under:
            notes.append(
                {
                    "item": item,
                    "note": f"You keep selling more {item } than expected on {weekday_under }s (by ~{pct }%). "
                    f"I've started preparing more on {weekday_under }s automatically.",
                    "action": "forecast_adjusted",
                }
            )
        elif over:
            notes.append(
                {
                    "item": item,
                    "note": f"The AI is recommending too much {item } — you're selling about {pct }% fewer "
                    f"than predicted. Reducing tomorrow's target.",
                    "action": "reduce_forecast",
                }
            )
        elif under:
            notes.append(
                {
                    "item": item,
                    "note": f"You're running out of {item } before the day ends — selling ~{pct }% more "
                    f"than forecast. Increasing tomorrow's target.",
                    "action": "increase_forecast",
                }
            )
        else:
            notes.append(
                {
                    "item": item,
                    "note": f"{item } forecast is off by {100 -int (accuracy )}%. "
                    f"Log sales daily for a week and accuracy will improve automatically.",
                    "action": "needs_more_data",
                }
            )
    return notes


def compute_bias_correction(restaurant: dict, item: str) -> float:
    """
    Learn from past forecast errors to correct systematic bias.
    If we consistently over-predict by 15%, apply a 0.925 multiplier.
    Returns a correction multiplier (0.7 to 1.3).
    """
    records = [
        r
        for r in restaurant.get("daily_records", [])
        if r.get("actual_sales") and r.get("forecast")
    ]
    if len(records) < 5:
        return 1.0
    errors = []
    for rec in records[-10:]:
        actual = rec["actual_sales"].get(item)
        if actual is None or actual <= 0:
            continue

        forecast_text = rec.get("forecast", "")
        pattern = re.compile(rf"•\s*{re.escape(item)}[:\s]+(\d+)", re.IGNORECASE)
        m = pattern.search(forecast_text)
        if m:
            predicted = float(m.group(1))
            bias = (predicted - actual) / actual
            errors.append(bias)
    if not errors:
        return 1.0
    avg_bias = sum(errors) / len(errors)
    correction = 1.0 - avg_bias * 0.5
    return round(max(0.7, min(1.3, correction)), 3)


def compute_mape_per_item(restaurant: dict) -> dict:
    """
    Compute Mean Absolute Percentage Error per menu item
    from historical forecast vs actual pairs.
    Returns dict: item_name → {"mape": float, "bias": float, "n": int}
    """
    records = [
        r
        for r in restaurant.get("daily_records", [])
        if r.get("actual_sales") and r.get("forecast")
    ]
    if not records:
        return {}
    item_errors: dict = defaultdict(list)
    for rec in records:
        actual_sales = rec.get("actual_sales", {})
        forecast_text = rec.get("forecast", "")
        for item, actual in actual_sales.items():
            if actual <= 0:
                continue
            pattern = re.compile(
                rf"(?:•\s*)?(?:\*\*)?\s*{re.escape(item)}\s*(?:\*\*)?\s*[:\-]\s*(\d+)",
                re.IGNORECASE,
            )
            m = pattern.search(forecast_text)
            if m:
                predicted = float(m.group(1))
                pct_err = abs(predicted - actual) / actual * 100
                bias = (predicted - actual) / actual * 100
                item_errors[item].append((pct_err, bias))
    result = {}
    for item, errs in item_errors.items():
        mape = sum(e for e, _ in errs) / len(errs)
        bias = sum(b for _, b in errs) / len(errs)
        result[item] = {"mape": round(mape, 1), "bias": round(bias, 1), "n": len(errs)}
    return result


def compute_item_correlations(restaurant: dict) -> list:
    """
    Find which items move together. Pearson correlation on daily sales.
    High positive correlation: if A sells well, B also sells well.
    Useful for group-scaling: if Masala Chai surges, Pyaaz Kachori likely also surges.
    Returns list of (item_a, item_b, correlation, description).
    """
    records = [r for r in restaurant.get("daily_records", []) if r.get("actual_sales")]
    if len(records) < 7:
        return []
    items = list({k for r in records for k in r.get("actual_sales", {}).keys()})
    n_items = len(items)
    if n_items < 2:
        return []
    data = {item: [] for item in items}
    for rec in records:
        sales = rec.get("actual_sales", {})
        for item in items:
            data[item].append(float(sales.get(item, 0)))

    def pearson(x: list, y: list) -> float:
        n = len(x)
        if n < 3:
            return 0.0
        mx, my = sum(x) / n, sum(y) / n
        num = sum((xi - mx) * (yi - my) for xi, yi in zip(x, y))
        den = (sum((xi - mx) ** 2 for xi in x) * sum((yi - my) ** 2 for yi in y)) ** 0.5
        return round(num / den, 3) if den > 1e-9 else 0.0

    results = []
    for i in range(n_items):
        for j in range(i + 1, n_items):
            a, b = items[i], items[j]
            r = pearson(data[a], data[b])
            if abs(r) >= 0.5:
                if r >= 0.7:
                    desc = "strongly move together"
                elif r >= 0.5:
                    desc = "tend to move together"
                elif r <= -0.7:
                    desc = "strongly move opposite"
                else:
                    desc = "tend to move opposite"
                results.append((a, b, r, desc))
    return sorted(results, key=lambda x: abs(x[2]), reverse=True)[:10]


def check_image_quality(image_bytes: bytes) -> Tuple[bool, str]:
    """
    Check if an image is usable before sending to Gemini Vision.
    Returns (is_acceptable, reason_if_rejected).
    Uses Laplacian variance as sharpness metric.
    Threshold calibrated for typical receipt/whiteboard photos.
    """
    try:
        from PIL import Image, ImageFilter, ImageStat
        import io

        img = Image.open(io.BytesIO(image_bytes))

        w, h = img.size
        if w < 100 or h < 100:
            return False, "Image too small (minimum 100×100 pixels). Please take a closer photo."

        if len(image_bytes) < 5_000:
            return False, "Image file is too small — it may be corrupted. Please try again."

        gray = img.convert("L")
        edges = gray.filter(ImageFilter.FIND_EDGES)
        stat = ImageStat.Stat(edges)
        variance = stat.var[0]
        BLUR_THRESHOLD = 200
        if variance < BLUR_THRESHOLD:
            return False, (
                f"Photo is too blurry (sharpness score: {variance :.0f}/200+). "
                "Please retake with better lighting and hold the camera still."
            )

        brightness = ImageStat.Stat(gray).mean[0]
        if brightness < 30:
            return False, "Photo is too dark. Please take in better lighting."
        if brightness > 240:
            return False, "Photo is overexposed (too bright). Please reduce glare."
        return True, "OK"
    except Exception:
        return True, "OK"


def calculate_waste_metrics(restaurant: dict, item_trends: dict) -> dict:
    """
    Calculate estimated food waste in RM and kg per day based on
    difference between what would be prepared vs what will actually sell.
    """
    menu_map = {m["item"]: m for m in restaurant.get("menu", [])}
    total_waste_rm = 0.0
    total_waste_kg = 0.0
    item_waste = []
    bom_map = restaurant.get("bom", {})

    for item, trend in item_trends.items():
        m = menu_map.get(item, {})
        base_qty = m.get("base_daily_demand", 50)
        recommended = trend.recommended_qty
        naive_qty = base_qty
        saving = max(0, naive_qty - recommended)
        if saving > 0:

            bom_entry = bom_map.get(item, {})
            item_cost = bom_entry.get("cost_rm", None)
            if item_cost is None:

                profit = m.get("profit_margin_rm", 0)
                item_cost = max(0.30, profit * 0.6) if profit > 0 else 0.80

            total_g = sum(
                v for k, v in bom_entry.items() if k.endswith("_g") and isinstance(v, (int, float))
            )
            total_ml = sum(
                v for k, v in bom_entry.items() if k.endswith("_ml") and isinstance(v, (int, float))
            )
            item_kg = (
                (total_g + total_ml * 0.001 * 1000) / 1000 if (total_g + total_ml) > 0 else 0.15
            )
            waste_rm = saving * item_cost
            waste_kg = saving * item_kg
            total_waste_rm += waste_rm
            total_waste_kg += waste_kg
            item_waste.append(
                {
                    "item": item,
                    "naive": naive_qty,
                    "recommended": int(recommended),
                    "saving": int(saving),
                    "saved_rm": round(waste_rm, 2),
                    "saved_kg": round(waste_kg, 2),
                }
            )
    return {
        "total_saved_rm": round(total_waste_rm, 2),
        "total_saved_kg": round(total_waste_kg, 2),
        "weekly_saved_rm": round(total_waste_rm * 7, 2),
        "monthly_saved_rm": round(total_waste_rm * 30, 2),
        "item_breakdown": sorted(item_waste, key=lambda x: x["saved_rm"], reverse=True)[:8],
    }


def compute_data_quality_score(restaurant: dict) -> dict:
    """
    Score 0-100 how good the AI's data is for this restaurant.
    Higher score = more accurate forecasts.
    Penalises: too few records, gaps in data, no recent uploads.
    """
    records = restaurant.get("daily_records", [])
    records_with_sales = [r for r in records if r.get("actual_sales")]
    menu_count = len(restaurant.get("menu", []))
    score = 0
    reasons = []
    from services.nlp import get_today
    today = get_today(restaurant)

    n = len(records_with_sales)
    if n >= 30:
        score += 35
        reasons.append("30+ days of data ✅")
    elif n >= 14:
        score += 25
        reasons.append("14-29 days of data ⚠")
    elif n >= 7:
        score += 15
        reasons.append("7-13 days of data ⚠")
    elif n >= 3:
        score += 8
        reasons.append("3-6 days of data ❌")
    else:
        score += 0
        reasons.append("< 3 days of data ❌")

    if menu_count >= 8:
        score += 20
    elif menu_count >= 4:
        score += 12
    elif menu_count >= 1:
        score += 6

    if n > 0:
        dates = sorted(
            [datetime.date.fromisoformat(r["date"]) for r in records_with_sales if r.get("date")]
        )
        if dates:
            recency_days = (today - dates[-1]).days
            if recency_days == 0:
                score += 25
                reasons.append("Data uploaded today ✅")
            elif recency_days <= 2:
                score += 18
                reasons.append("Data updated recently ✅")
            elif recency_days <= 7:
                score += 10
                reasons.append("Data slightly stale ⚠")
            else:
                score += 0
                reasons.append(f"Data {recency_days } days old ❌")

            if len(dates) >= 3:
                gaps = [(dates[i + 1] - dates[i]).days for i in range(len(dates) - 1)]
                max_gap = max(gaps)
                if max_gap <= 2:
                    score += 10
                elif max_gap <= 7:
                    score += 5
            else:
                score += 5

    has_memory = len(restaurant.get("recent_feedback_memory", [])) >= 3
    if has_memory:
        score += 10
        reasons.append("Owner notes present ✅")

    grade = "A" if score >= 80 else "B" if score >= 60 else "C" if score >= 40 else "D"
    return {
        "score": min(100, score),
        "grade": grade,
        "label": {"A": "Excellent", "B": "Good", "C": "Fair", "D": "Needs data"}[grade],
        "n_records": n,
        "reasons": reasons,
    }


def detect_outliers(series: list, threshold: float = 3.5) -> list:
    """
    Modified Z-score using Median Absolute Deviation.
    More robust than standard Z-score for small samples.
    Returns boolean mask: True = outlier.
    """
    if len(series) < 4:
        return [False] * len(series)
    values = [float(v) for v in series]
    median = sorted(values)[len(values) // 2]
    mad = sorted([abs(v - median) for v in values])[len(values) // 2]
    if mad == 0:
        return [False] * len(values)
    scores = [0.6745 * abs(v - median) / mad for v in values]
    return [s > threshold for s in scores]


def clean_series(series: list) -> list:
    """Remove statistical outliers from series for cleaner trend computation."""
    if len(series) < 4:
        return series
    outliers = detect_outliers(series)
    cleaned = [v for v, is_out in zip(series, outliers) if not is_out]
    return cleaned if len(cleaned) >= 3 else series


def compute_ewma(series: list, alpha: float = 0.35) -> float:
    if not series:
        return 0.0
    clean = clean_series([float(v) for v in series])
    ewma = clean[0]
    for val in clean[1:]:
        ewma = alpha * val + (1 - alpha) * ewma
    return round(ewma, 1)


def holt_winters(series: list, alpha: float = 0.4, beta: float = 0.2) -> tuple:
    """
    Double exponential smoothing (Holt's method).
    Captures BOTH level (current value) and trend (slope/velocity).
    Returns (level, trend, one-step-ahead forecast).

    alpha: smoothing factor for level (0.4 = responsive to recent changes)
    beta:  smoothing factor for trend  (0.2 = smoother trend line)
    """
    clean = clean_series([float(v) for v in series])
    if len(clean) < 2:
        v = clean[0] if clean else 0.0
        return v, 0.0, v

    level = clean[0]
    trend = clean[1] - clean[0]

    for val in clean[1:]:
        prev_level = level
        level = alpha * val + (1 - alpha) * (level + trend)
        trend = beta * (level - prev_level) + (1 - beta) * trend

    forecast = level + trend
    return round(level, 1), round(trend, 2), round(max(0, forecast), 1)


def compute_velocity(series: list) -> float:
    n = len(series)
    if n < 2:
        return 0.0
    clean = clean_series([float(v) for v in series])
    n = len(clean)
    x_vals = list(range(n))
    sx, sy = sum(x_vals), sum(clean)
    sxy = sum(x_vals[i] * clean[i] for i in range(n))
    sx2 = sum(x**2 for x in x_vals)
    denom = n * sx2 - sx**2
    return round((n * sxy - sx * sy) / denom, 2) if denom != 0 else 0.0


def compute_weekday_seasonality(restaurant: dict) -> dict:
    """
    Compute per-weekday demand multiplier from actual historical data.
    E.g. if Saturdays average 140% of the weekly mean → Saturday multiplier = 1.40

    Returns: {"Monday": 0.92, "Tuesday": 0.88, ..., "Sunday": 1.15}
    """
    records = [r for r in restaurant.get("daily_records", []) if r.get("actual_sales")]
    weekday_totals = defaultdict(list)
    for rec in records:
        try:
            d = datetime.date.fromisoformat(rec["date"])
        except ValueError:
            continue
        total = sum(v for v in rec["actual_sales"].values() if isinstance(v, (int, float)))
        if total > 0:
            weekday_totals[d.strftime("%A")].append(float(total))

    if not weekday_totals:
        return {}

    weekday_avgs = {}
    for day, vals in weekday_totals.items():
        cleaned = clean_series(vals) if len(vals) >= 3 else vals
        weekday_avgs[day] = sum(cleaned) / len(cleaned)

    grand_avg = sum(weekday_avgs.values()) / len(weekday_avgs)
    if grand_avg == 0:
        return {}

    return {day: round(avg / grand_avg, 3) for day, avg in weekday_avgs.items()}


def infer_weather_correlation(global_events: list) -> dict:
    """
    Mine the global_learning_events text for weather-demand patterns.
    Simple keyword frequency analysis since we don't store structured weather+sales pairs.
    Returns qualitative signals: {"rainy": -0.2, "hot": +0.15}
    """
    signals = {"rainy": [], "hot": [], "cold": []}
    for ev in global_events:
        pattern = ev.get("pattern", "").lower()
        if "rain" in pattern or "drizzle" in pattern or "thunder" in pattern:

            if any(w in pattern for w in ("drop", "lower", "less", "fewer", "decline")):
                signals["rainy"].append(-1)
            elif any(w in pattern for w in ("surge", "higher", "more", "increase", "boost")):
                signals["rainy"].append(+1)
        if "hot" in pattern or "heat" in pattern or "warm" in pattern:
            if any(
                w in pattern for w in ("cold drink", "ice", "cendol", "dessert", "surge", "boost")
            ):
                signals["hot"].append(+1)
            elif any(w in pattern for w in ("drop", "lower", "less")):
                signals["hot"].append(-1)

    result = {}
    for condition, vals in signals.items():
        if vals:
            result[condition] = round(sum(vals) / len(vals) * 0.25, 2)
    return result


def compute_item_trends(restaurant: dict, today_weekday: str = None) -> dict:
    """
    Full ML analysis per menu item using Holt-Winters + seasonality + anomaly detection.
    Returns dict: item_name → ItemTrend

    This runs in microseconds (pure Python math, no I/O, no AI).
    Called every time new sales data is uploaded → instant model update.
    """
    from services.nlp import get_today
    if today_weekday is None:
        today_weekday = get_today(restaurant).strftime("%A")

    records = sorted(
        [r for r in restaurant.get("daily_records", []) if r.get("actual_sales")],
        key=lambda r: r["date"],
    )
    if not records:
        return {}

    weekday_mults = compute_weekday_seasonality(restaurant)
    today_mult = weekday_mults.get(today_weekday, 1.0)

    item_series = defaultdict(list)
    for rec in records:
        date = rec["date"]
        for item, qty in rec.get("actual_sales", {}).items():
            if isinstance(qty, (int, float)) and qty >= 0:
                item_series[item].append((date, float(qty)))

    trends = {}
    from services.nlp import get_today
    today = get_today(restaurant)

    for item, time_series in item_series.items():
        time_series.sort(key=lambda x: x[0])
        all_qtys = [q for _, q in time_series]

        recent_w, older_w = [], []
        for date_str, qty in time_series:
            try:
                d = datetime.date.fromisoformat(date_str)
            except ValueError:
                continue
            days_ago = (today - d).days
            if days_ago <= 7:
                recent_w.append(qty)
            elif days_ago <= 30:
                older_w.append(qty)

        recent_avg = sum(recent_w) / len(recent_w) if recent_w else 0.0
        older_avg = sum(older_w) / len(older_w) if older_w else recent_avg

        level, hw_trend, hw_forecast = holt_winters(all_qtys)
        ewma_val = compute_ewma(all_qtys)
        velocity = compute_velocity(all_qtys[-14:])

        if older_avg > 0:
            trend_pct = ((recent_avg - older_avg) / older_avg) * 100
        elif recent_avg > 0:
            trend_pct = 100.0
        else:
            trend_pct = 0.0

        n = len(time_series)
        if n < 2:
            trend_dir = "new"
        elif trend_pct >= 30:
            trend_dir = "strongly_rising"
        elif trend_pct >= 10:
            trend_dir = "rising"
        elif trend_pct <= -30:
            trend_dir = "strongly_falling"
        elif trend_pct <= -10:
            trend_dir = "falling"
        else:
            trend_dir = "stable"

        if velocity > 1.5:
            velocity_dir = "accelerating_up"
        elif velocity < -1.5:
            velocity_dir = "accelerating_down"
        else:
            velocity_dir = "steady"

        outliers = detect_outliers(all_qtys)
        has_anomaly = outliers[-1] if outliers else False
        anomaly_note = ""
        if has_anomaly and all_qtys:
            last = all_qtys[-1]
            prev_avg = sum(all_qtys[:-1]) / max(1, len(all_qtys) - 1)
            direction = "spike" if last > prev_avg else "drop"
            anomaly_note = f"Last reading ({last :.0f}) was an unusual {direction } — excluded from trend calculation"

        trend_mults = {
            "strongly_rising": 1.12,
            "rising": 1.06,
            "stable": 1.00,
            "falling": 0.94,
            "strongly_falling": 0.85,
            "new": 1.00,
        }
        base_qty = hw_forecast * today_mult
        final_qty = base_qty * trend_mults.get(trend_dir, 1.0)

        confidence = "high" if n >= 7 else ("medium" if n >= 3 else "low")

        trends[item] = ItemTrend(
            item=item,
            holt_level=level,
            holt_trend=hw_trend,
            holt_forecast=hw_forecast,
            ewma=ewma_val,
            recent_avg=round(recent_avg, 1),
            older_avg=round(older_avg, 1),
            trend_pct=round(trend_pct, 1),
            trend_dir=trend_dir,
            velocity=velocity,
            velocity_dir=velocity_dir,
            weekday_mult=today_mult,
            sample_size=n,
            confidence=confidence,
            has_anomaly=has_anomaly,
            anomaly_note=anomaly_note,
            recommended_qty=max(1.0, round(final_qty, 0)),
        )

    return trends


def compute_learned_multipliers(restaurant: dict, global_events: list = None) -> LearnedMultipliers:
    weekday = compute_weekday_seasonality(restaurant)
    wx_sigs = infer_weather_correlation(global_events or [])
    records = [r for r in restaurant.get("daily_records", []) if r.get("actual_sales")]
    confidence = "high" if len(records) >= 14 else ("medium" if len(records) >= 7 else "low")
    best = max(weekday, key=weekday.get) if weekday else "N/A"
    worst = min(weekday, key=weekday.get) if weekday else "N/A"
    return LearnedMultipliers(
        weekday=weekday,
        weather_signals=wx_sigs,
        overall_confidence=confidence,
        best_day=best,
        worst_day=worst,
    )


CATEGORY_KEYWORDS = {
    "ice_cream": ["ice cream", "aiskrim", "ais krim", "gelato", "sorbet", "soft serve"],
    "cold_drinks": [
        "ais ",
        " ice",
        "cold",
        "sejuk",
        "cincau",
        "bandung",
        "soda",
        "juice",
        "smoothie",
        "frappe",
    ],
    "hot_drinks": [
        "garam",
        "Masala Chai",
        "kopi",
        "coffee",
        "tea",
        "milo",
        "cocoa",
        "latte",
        "flat white",
    ],
    "rice_dishes": ["nasi", " rice", "biryani", "pulut", "don", "donburi"],
    "noodles": ["mee", "noodle", "laksa", "ramen", "udon", "soba", "pho", "maggi", "pasta"],
    "desserts": [
        "cendol",
        "ais kacang",
        "kuih",
        "cake",
        "tart",
        "pudding",
        "sweet",
        "bao",
        "waffle",
    ],
    "grilled": ["satay", "bbq", "bakar", "grill", "yakitori", "yakiniku"],
    "fried": ["goreng", "fried", "katsu", "karaage", "crispy", "fritters"],
    "dim_sum": ["bao", "har gao", "siu mai", "dim sum", "dumpling", "gyoza", "takoyaki"],
    "bread_pastry": ["roti", "bread", "toast", "croissant", "muffin", "bun", "tosai"],
    "seafood": ["udang", "prawn", "ikan", "fish", "sotong", "squid", "crab"],
    "meat": ["ayam", "chicken", "daging", "beef", "lamb", "kambing", "pork"],
}


def categorize_item(item_name: str) -> str:
    name_lower = item_name.lower()
    for category, keywords in CATEGORY_KEYWORDS.items():
        if any(kw in name_lower for kw in keywords):
            return category
    return "other"


def mine_ecosystem_signals(all_restaurants: list, regions_data: dict) -> list:
    """
    Advanced cross-restaurant demand signal mining.

    Uses:
    - Trend agreement across restaurants (more agreements = higher confidence)
    - Confidence weighting (high-confidence trends count more)
    - Category-level and item-level signals
    - Regional correlation (same pattern in same region type = stronger signal)
    """
    restaurant_trends = []
    for rest in all_restaurants:
        if not rest.get("daily_records"):
            continue
        region = rest.get("region", "")
        region_type = regions_data.get(region, {}).get("type", "unknown")
        trends = compute_item_trends(rest)
        if trends:
            restaurant_trends.append(
                {
                    "id": rest["id"],
                    "region_type": region_type,
                    "region": region,
                    "trends": trends,
                }
            )

    if len(restaurant_trends) < 2:
        return []

    cat_signals = defaultdict(list)
    item_signals = defaultdict(list)

    for rt in restaurant_trends:
        for item_name, trend in rt["trends"].items():
            if trend.sample_size < 3:
                continue
            category = categorize_item(item_name)
            direction = (
                "rising"
                if trend.trend_pct > 8
                else ("falling" if trend.trend_pct < -8 else "neutral")
            )
            if direction == "neutral":
                continue
            weight = {"high": 1.0, "medium": 0.6, "low": 0.3}.get(trend.confidence, 0.3)
            entry = (direction, trend.trend_pct, rt["region_type"], rt["region"], weight)
            cat_signals[category].append(entry)
            item_signals[item_name.lower()].append(entry)

    signals = []

    def build_signal(pattern: str, observations: list) -> Optional[EcosystemSignal]:
        if len(observations) < 2:
            return None
        rising = [(p, r, w) for d, p, rt, r, w in observations if d == "rising"]
        falling = [(p, r, w) for d, p, rt, r, w in observations if d == "falling"]

        for direction, group in [("rising", rising), ("falling", falling)]:
            if len(group) < 2:
                continue
            count = len(group)
            total_w = sum(w for _, _, w in group)
            avg_pct = abs(sum(p for p, _, _ in group) / count)

            conf_score = round(min(1.0, total_w / count * (count / max(count, 3))), 2)
            strength = (
                "strong"
                if count >= 3 and conf_score >= 0.7
                else ("moderate" if count >= 2 else "weak")
            )
            region_types = list({rt for d, p, rt, r, w in observations})

            return EcosystemSignal(
                item_pattern=pattern,
                direction=direction,
                restaurant_count=count,
                avg_shift_pct=round(avg_pct, 1),
                signal_strength=strength,
                region_types=region_types,
                confidence_score=conf_score,
            )
        return None

    for category, obs in cat_signals.items():
        sig = build_signal(category, obs)
        if sig:
            signals.append(sig)

    for item_key, obs in item_signals.items():
        sig = build_signal(f"item:{item_key }", obs)
        if sig and sig.signal_strength in ("strong", "moderate"):
            signals.append(sig)

    seen, unique = set(), []
    for s in sorted(signals, key=lambda x: x.confidence_score, reverse=True):
        key = (s.item_pattern, s.direction)
        if key not in seen:
            seen.add(key)
            unique.append(s)

    return unique[:10]


def format_intelligence_report(restaurant_id: str, db: dict) -> str:
    """
    Produce the pre-computed statistical intelligence briefing.
    Pure computation — no I/O, no AI calls. Returns in <5ms.

    This is what makes WasteWise genuinely intelligent:
    The LLM narrates computed facts, not guesses from raw data.
    """
    from services.nlp import _get_restaurant, get_today

    restaurant = _get_restaurant(db, restaurant_id)
    if not restaurant or not restaurant.get("daily_records"):
        return "⚙️ No historical sales data yet. Forecasting from baseline only."

    today_weekday = get_today(restaurant).strftime("%A")
    all_restaurants = db.get("restaurants", [])
    regions_data = db.get("regions", {})
    global_events = db.get("global_learning_events", [])

    item_trends = compute_item_trends(restaurant, today_weekday)
    learned = compute_learned_multipliers(restaurant, global_events)
    eco_signals = mine_ecosystem_signals(all_restaurants, regions_data)

    lines = [
        "═══ COMPUTED ML INTELLIGENCE (Holt-Winters + Seasonality + Cross-Restaurant Mining) ═══"
    ]

    if item_trends:
        lines.append(
            f"\n📊 ITEM DEMAND TRENDS (today={today_weekday }, weekday_mult={learned .weekday .get (today_weekday ,1.0 ):.2f}x learned from data):"
        )
        sorted_t = sorted(item_trends.values(), key=lambda t: abs(t.trend_pct), reverse=True)
        for t in sorted_t:
            arrow = {
                "strongly_rising": "⬆⬆",
                "rising": "⬆",
                "stable": "→",
                "falling": "⬇",
                "strongly_falling": "⬇⬇",
                "new": "✦",
            }.get(t.trend_dir, "→")
            pct_s = f"{t .trend_pct :+.0f}%" if t.trend_dir != "new" else "new item"
            vdir = (
                f" | velocity={t .velocity :+.1f}/day({t .velocity_dir })"
                if t.velocity_dir != "steady"
                else ""
            )
            lines.append(
                f"  {arrow } {t .item }: HW_forecast={t .holt_forecast :.0f} | trend={pct_s } | "
                f"recommended={t .recommended_qty :.0f} | conf={t .confidence }{vdir }"
            )
            if t.has_anomaly and t.anomaly_note:
                lines.append(f"    ⚠ ANOMALY: {t .anomaly_note }")
            if t.trend_dir in ("strongly_falling", "strongly_rising") and t.confidence != "low":
                w = "SURGING" if "rising" in t.trend_dir else "DECLINING"
                acc = (
                    f" and ACCELERATING"
                    if "down" in t.velocity_dir or "up" in t.velocity_dir
                    else ""
                )
                lines.append(
                    f"    🔴 SHIFT ALERT: {t .item } demand is {w } ({abs (t .trend_pct ):.0f}%{acc }) — adjust significantly"
                )

    if learned.weekday:
        today_m = learned.weekday.get(today_weekday)
        lines.append(
            f"\n📅 LEARNED SEASONALITY ({learned .overall_confidence } confidence, from actual sales):"
        )
        if today_m:
            demand_str = f"{'ABOVE'if today_m >1.05 else 'BELOW'if today_m <0.95 else 'AT'} average"
            lines.append(
                f"  Today ({today_weekday }): {today_m :.2f}x — {demand_str } average demand"
            )
        lines.append(
            f"  Best day: {learned .best_day } ({learned .weekday .get (learned .best_day ,1.0 ):.2f}x) | "
            f"Slowest: {learned .worst_day } ({learned .weekday .get (learned .worst_day ,1.0 ):.2f}x)"
        )
        if learned.weather_signals:
            wx_parts = [
                f"{k }: {'+'if v >0 else ''}{v *100 :.0f}%"
                for k, v in learned.weather_signals.items()
            ]
            lines.append(f"  Inferred weather effects: {' | '.join (wx_parts )}")

    relevant = [s for s in eco_signals if s.signal_strength in ("strong", "moderate")]
    if relevant:
        lines.append(
            f"\n🌍 CROSS-RESTAURANT ECOSYSTEM SIGNALS ({len (relevant )} signals detected):"
        )
        for sig in relevant:
            pattern = sig.item_pattern.replace("item:", "").replace("_", " ").title()
            dw = "↑ INCREASING" if sig.direction == "rising" else "↓ DECLINING"
            lines.append(
                f"  {dw }: {pattern } across {sig .restaurant_count } restaurant(s), "
                f"avg {sig .avg_shift_pct :.0f}% shift [{sig .signal_strength }, conf={sig .confidence_score :.1f}]"
            )

    if len(lines) == 1:
        return "⚙️ Collecting baseline data... Forecasting from menu defaults."

    lines.append("═══════════════════════════════════════════════════════════════")
    return "\n".join(lines)


def _mape(actual: list, forecast: list) -> float:
    """Mean Absolute Percentage Error."""
    errors = []
    for a, f in zip(actual, forecast):
        if a > 0:
            errors.append(abs(a - f) / a)
    return sum(errors) / len(errors) * 100 if errors else 0.0


def _cross_validate_holt(series: list, alpha: float, beta: float) -> float:
    """Walk-forward MAPE for given alpha/beta on a series."""
    if len(series) < 6:
        return 999.0
    actuals, forecasts = [], []
    train = series[:4]
    for i in range(4, len(series)):
        _, _, fc = holt_winters(train, alpha, beta)
        forecasts.append(fc)
        actuals.append(series[i])
        train = series[: i + 1]
    return _mape(actuals, forecasts)


def auto_tune_item(series: list) -> Tuple[float, float, float]:
    """
    Grid search over alpha/beta to find the best Holt-Winters parameters
    for this specific item's sales pattern.
    Returns (best_alpha, best_beta, best_mape).
    Only runs when series has 15+ data points.
    """
    if len(series) < 15:
        return 0.4, 0.2, 999.0

    candidates = [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7]
    best_alpha, best_beta, best_mape = 0.4, 0.2, float("inf")

    for alpha in candidates:
        for beta in candidates:
            if alpha + beta > 1.0:
                continue
            mape = _cross_validate_holt(series, alpha, beta)
            if mape < best_mape:
                best_mape = mape
                best_alpha = alpha
                best_beta = beta

    return best_alpha, best_beta, round(best_mape, 1)


def compute_item_trends_with_tuning(restaurant: dict, today_weekday: str = None) -> dict:
    """
    Same as compute_item_trends but uses auto-tuned Holt-Winters parameters
    for items with 15+ data points.
    """
    from services.nlp import get_today
    if today_weekday is None:
        today_weekday = get_today(restaurant).strftime("%A")

    records = sorted(
        [r for r in restaurant.get("daily_records", []) if r.get("actual_sales")],
        key=lambda r: r["date"],
    )
    if not records:
        return {}

    weekday_mults = compute_weekday_seasonality(restaurant)
    today_mult = weekday_mults.get(today_weekday, 1.0)
    item_series = defaultdict(list)

    for rec in records:
        for item, qty in rec.get("actual_sales", {}).items():
            if isinstance(qty, (int, float)) and qty >= 0:
                item_series[item].append((datetime.date.fromisoformat(rec["date"]), float(qty)))

    trends = {}
    from services.nlp import get_today
    today = get_today(restaurant)
    tuning_cache = restaurant.get("_hw_tuning", {})

    for item, ts in item_series.items():
        ts.sort(key=lambda x: x[0])
        all_qtys = [q for _, q in ts]

        if len(all_qtys) >= 15:
            cached = tuning_cache.get(item, {})
            alpha = cached.get("alpha", 0.4)
            beta = cached.get("beta", 0.2)
        else:
            alpha, beta = 0.4, 0.2

        recent_w = [q for d, q in ts if (today - d).days <= 7]
        older_w = [q for d, q in ts if 8 <= (today - d).days <= 30]
        recent_avg = sum(recent_w) / len(recent_w) if recent_w else 0.0
        older_avg = sum(older_w) / len(older_w) if older_w else recent_avg

        level, hw_trend, hw_fc = holt_winters(all_qtys, alpha, beta)
        ewma_val = compute_ewma(all_qtys)
        velocity = compute_velocity(all_qtys[-14:])

        if older_avg > 0:
            trend_pct = ((recent_avg - older_avg) / older_avg) * 100
        elif recent_avg > 0:
            trend_pct = 100.0
        else:
            trend_pct = 0.0

        n = len(ts)
        if n < 2:
            trend_dir = "new"
        elif trend_pct >= 30:
            trend_dir = "strongly_rising"
        elif trend_pct >= 10:
            trend_dir = "rising"
        elif trend_pct <= -30:
            trend_dir = "strongly_falling"
        elif trend_pct <= -10:
            trend_dir = "falling"
        else:
            trend_dir = "stable"

        if velocity > 1.5:
            velocity_dir = "accelerating_up"
        elif velocity < -1.5:
            velocity_dir = "accelerating_down"
        else:
            velocity_dir = "steady"

        outliers = detect_outliers(all_qtys)
        has_anomaly = outliers[-1] if outliers else False
        anomaly_note = ""
        if has_anomaly and all_qtys:
            last = all_qtys[-1]
            prev = sum(all_qtys[:-1]) / max(1, len(all_qtys) - 1)
            anomaly_note = f"Last reading ({last :.0f}) is an unusual {'spike'if last >prev else 'drop'} — excluded from trend"

        trend_mults = {
            "strongly_rising": 1.12,
            "rising": 1.06,
            "stable": 1.0,
            "falling": 0.94,
            "strongly_falling": 0.85,
            "new": 1.0,
        }
        final_qty = hw_fc * today_mult * trend_mults.get(trend_dir, 1.0)
        confidence = "high" if n >= 7 else ("medium" if n >= 3 else "low")

        trends[item] = ItemTrend(
            item=item,
            holt_level=level,
            holt_trend=hw_trend,
            holt_forecast=hw_fc,
            ewma=ewma_val,
            recent_avg=round(recent_avg, 1),
            older_avg=round(older_avg, 1),
            trend_pct=round(trend_pct, 1),
            trend_dir=trend_dir,
            velocity=velocity,
            velocity_dir=velocity_dir,
            weekday_mult=today_mult,
            sample_size=n,
            confidence=confidence,
            has_anomaly=has_anomaly,
            anomaly_note=anomaly_note,
            recommended_qty=max(1.0, round(final_qty, 0)),
        )
    return trends


def run_weekly_auto_tune(restaurant: dict) -> dict:
    """
    Run grid search for all items with 15+ data points.
    Saves best alpha/beta to restaurant['_hw_tuning'].
    Returns summary of changes.
    """
    records = [r for r in restaurant.get("daily_records", []) if r.get("actual_sales")]
    item_series: dict = defaultdict(list)
    for rec in records:
        for item, qty in rec.get("actual_sales", {}).items():
            if isinstance(qty, (int, float)) and qty > 0:
                item_series[item].append(float(qty))

    tuning = restaurant.get("_hw_tuning", {})
    updated = []

    for item, series in item_series.items():
        if len(series) < 15:
            continue
        best_alpha, best_beta, best_mape = auto_tune_item(series)
        old = tuning.get(item, {})
        old_mape = old.get("mape", 999.0)
        if best_mape < old_mape - 0.5:
            tuning[item] = {"alpha": best_alpha, "beta": best_beta, "mape": best_mape}
            updated.append(f"{item }: α={best_alpha }, β={best_beta }, MAPE={best_mape :.1f}%")

    restaurant["_hw_tuning"] = tuning
    return {"updated_items": updated, "total_tuned": len(tuning)}


def compute_weather_pearson(restaurant: dict) -> dict:
    """
    Compute actual Pearson correlation between weather conditions and demand.
    Requires daily_records to have both actual_sales and weather stored.
    Falls back to keyword mining if no structured weather data.
    """
    records_with_weather = [
        r for r in restaurant.get("daily_records", []) if r.get("actual_sales") and r.get("weather")
    ]

    if len(records_with_weather) < 10:
        return infer_weather_correlation(restaurant.get("global_events_snapshot", []))

    demands, temps, is_rainy = [], [], []
    for rec in records_with_weather:
        total = sum(v for v in rec["actual_sales"].values() if isinstance(v, (int, float)))
        weather_str = rec["weather"].lower()
        temp_match = re.search(r"(\d+)°c", weather_str)
        temp = int(temp_match.group(1)) if temp_match else 32
        rain = 1 if any(w in weather_str for w in ("rain", "drizzle", "thunder")) else 0
        demands.append(float(total))
        temps.append(float(temp))
        is_rainy.append(float(rain))

    def pearson(x: list, y: list) -> float:
        n = len(x)
        if n < 3:
            return 0.0
        mx, my = sum(x) / n, sum(y) / n
        num = sum((xi - mx) * (yi - my) for xi, yi in zip(x, y))
        den = (sum((xi - mx) ** 2 for xi in x) * sum((yi - my) ** 2 for yi in y)) ** 0.5
        return round(num / den, 3) if den != 0 else 0.0

    temp_corr = pearson(temps, demands)
    rain_corr = pearson(is_rainy, demands)

    return {
        "temperature_demand_r": temp_corr,
        "rain_demand_r": rain_corr,
        "interpretation": {
            "temperature": f"{'positive'if temp_corr >0.1 else 'negative'if temp_corr <-0.1 else 'neutral'} correlation ({temp_corr :+.2f})",
            "rain": f"{'positive'if rain_corr >0.1 else 'negative'if rain_corr <-0.1 else 'neutral'} correlation ({rain_corr :+.2f})",
        },
        "data_points": len(records_with_weather),
    }


DEFAULT_BOM = {
    "Pyaaz Kachori": {"rice_g": 200, "coconut_milk_ml": 50, "anchovy_g": 20},
    "Dal Baati Churma": {"flour_g": 120, "ghee_g": 20, "egg": 0.5},
    "Masala Chai": {"tea_g": 5, "condensed_milk_ml": 30, "milk_ml": 100},
    "kopi": {"coffee_g": 8, "condensed_milk_ml": 25},
    "milo": {"milo_g": 20, "milk_ml": 150},
    "Gatte ki Sabzi": {"noodle_g": 150, "egg": 1, "oil_ml": 15},
    "nasi goreng": {"rice_g": 200, "egg": 1, "oil_ml": 15, "soy_sauce_ml": 10},
    "ayam goreng": {"chicken_g": 200, "flour_g": 30, "oil_ml": 20},
    "satay": {"meat_g": 100, "peanut_sauce_g": 30},
    "cendol": {"coconut_milk_ml": 150, "pandan_jelly_g": 50, "gula_melaka_g": 40},
    "ais kacang": {"shaved_ice_g": 200, "red_bean_g": 50, "syrup_ml": 30},
    "ice cream": {"ice_cream_scoop_g": 80},
    "ramen": {"noodle_g": 150, "broth_ml": 400, "egg": 1},
    "gyoza": {"dumpling_g": 90, "oil_ml": 10},
    "dim sum": {"dough_g": 50, "filling_g": 40},
}


def match_bom(item_name: str) -> dict:
    """Fuzzy-match a menu item to a bill-of-materials template."""
    name_lower = item_name.lower()
    for key, bom in DEFAULT_BOM.items():
        if key in name_lower:
            return bom

    for key, bom in DEFAULT_BOM.items():
        words = key.split()
        if any(w in name_lower for w in words if len(w) > 3):
            return bom
    return {}


def generate_shopping_list(restaurant: dict, item_trends: dict) -> list:
    """
    Generate daily ingredient shopping list from forecast quantities × owner-defined BOM.
    Uses the restaurant's own BOM ratios first. Falls back to common defaults for unrecognised items.
    Returns list of {ingredient, quantity, unit} sorted by ingredient name.
    """
    owner_bom = restaurant.get("bom", {})
    totals: dict = {}
    for menu_item in restaurant.get("menu", []):
        name = menu_item["item"]
        t = item_trends.get(name)
        qty = int(t.recommended_qty) if t else menu_item.get("base_daily_demand", 50)
        bom = owner_bom.get(name) or match_bom(name)
        for ingredient, per_unit in bom.items():
            if ingredient == "cost_rm":
                continue
            totals[ingredient] = totals.get(ingredient, 0) + per_unit * qty

    units_map = {
        "rice_g": "g",
        "flour_g": "g",
        "noodle_g": "g",
        "chicken_g": "g",
        "meat_g": "g",
        "anchovy_g": "g",
        "tea_g": "g",
        "coffee_g": "g",
        "milo_g": "g",
        "peanut_sauce_g": "g",
        "pandan_jelly_g": "g",
        "gula_melaka_g": "g",
        "red_bean_g": "g",
        "ice_cream_scoop_g": "g",
        "filling_g": "g",
        "dough_g": "g",
        "broth_ml": "ml",
        "coconut_milk_ml": "ml",
        "condensed_milk_ml": "ml",
        "milk_ml": "ml",
        "oil_ml": "ml",
        "syrup_ml": "ml",
        "soy_sauce_ml": "ml",
        "ghee_g": "g",
        "shaved_ice_g": "g",
        "egg": "eggs",
    }

    result = []
    for ingr, total in sorted(totals.items()):
        unit = units_map.get(ingr, "units")
        display_name = ingr.replace("_g", "").replace("_ml", "").replace("_", " ").title()
        result.append(
            {
                "ingredient": display_name,
                "quantity": round(total, 0),
                "unit": unit,
            }
        )
    return result
