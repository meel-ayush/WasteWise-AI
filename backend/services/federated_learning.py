from __future__ import annotations
import datetime
import math
from typing import Optional

try:
    import numpy as np

    _NUMPY = True
except ImportError:
    _NUMPY = False

FEATURE_DIM = 8
HIDDEN1 = 16
HIDDEN2 = 8
OUTPUT_DIM = 1
W1_SIZE = FEATURE_DIM * HIDDEN1
B1_SIZE = HIDDEN1
W2_SIZE = HIDDEN1 * HIDDEN2
B2_SIZE = HIDDEN2
W3_SIZE = HIDDEN2 * OUTPUT_DIM
B3_SIZE = OUTPUT_DIM
PARAM_SIZE = W1_SIZE + B1_SIZE + W2_SIZE + B2_SIZE + W3_SIZE + B3_SIZE
DP_EPSILON = 1.0
GRAD_CLIP = 1.0


def _unpack(w: "np.ndarray"):
    idx = 0
    W1 = w[idx : idx + W1_SIZE].reshape(FEATURE_DIM, HIDDEN1)
    idx += W1_SIZE
    b1 = w[idx : idx + B1_SIZE]
    idx += B1_SIZE
    W2 = w[idx : idx + W2_SIZE].reshape(HIDDEN1, HIDDEN2)
    idx += W2_SIZE
    b2 = w[idx : idx + B2_SIZE]
    idx += B2_SIZE
    W3 = w[idx : idx + W3_SIZE].reshape(HIDDEN2, OUTPUT_DIM)
    idx += W3_SIZE
    b3 = w[idx : idx + B3_SIZE]
    return W1, b1, W2, b2, W3, b3


def _init() -> "np.ndarray":
    np.random.seed(42)
    W1 = np.random.randn(FEATURE_DIM, HIDDEN1) * math.sqrt(2.0 / FEATURE_DIM)
    W2 = np.random.randn(HIDDEN1, HIDDEN2) * math.sqrt(2.0 / HIDDEN1)
    W3 = np.random.randn(HIDDEN2, OUTPUT_DIM) * math.sqrt(2.0 / HIDDEN2)
    return np.concatenate(
        [
            W1.ravel(),
            np.zeros(HIDDEN1),
            W2.ravel(),
            np.zeros(HIDDEN2),
            W3.ravel(),
            np.zeros(OUTPUT_DIM),
        ]
    )


def _relu(x: "np.ndarray") -> "np.ndarray":
    return np.maximum(0, x)


def _forward(x: "np.ndarray", w: "np.ndarray") -> float:
    W1, b1, W2, b2, W3, b3 = _unpack(w)
    return float((_relu(_relu(x @ W1 + b1) @ W2 + b2) @ W3 + b3)[0])


def _gradients(X: "np.ndarray", y: "np.ndarray", w: "np.ndarray") -> "np.ndarray":
    W1, b1, W2, b2, W3, b3 = _unpack(w)
    n = len(X)
    z1 = X @ W1 + b1
    h1 = _relu(z1)
    z2 = h1 @ W2 + b2
    h2 = _relu(z2)
    pred = (h2 @ W3 + b3).ravel()
    err = pred - y
    dz3 = (2.0 / n) * err.reshape(-1, 1)
    dz2 = (dz3 @ W3.T) * (z2 > 0).astype(float)
    dz1 = (dz2 @ W2.T) * (z1 > 0).astype(float)
    return np.concatenate(
        [
            (X.T @ dz1).ravel(),
            dz1.sum(0),
            (h1.T @ dz2).ravel(),
            dz2.sum(0),
            (h2.T @ dz3).ravel(),
            dz3.sum(0),
        ]
    )


def _features(record: dict) -> Optional[list[float]]:
    if not _NUMPY:
        return None
    try:
        d = datetime.date.fromisoformat(record["date"])
        w = record.get("weather", "")
        return [
            math.sin(2 * math.pi * d.weekday() / 7),
            math.cos(2 * math.pi * d.weekday() / 7),
            math.sin(2 * math.pi * (d.month - 1) / 12),
            math.cos(2 * math.pi * (d.month - 1) / 12),
            1.0 if d.weekday() >= 5 else 0.0,
            1.0 if any(x in w for x in ("rain", "thunder", "drizzle", "shower")) else 0.0,
            1.0 if record.get("active_events") or record.get("foot_traffic") == "high" else 0.0,
            1.0,
        ]
    except Exception:
        return None


def train_local_model(restaurant: dict, global_weights: list[float]) -> Optional[list[float]]:
    if not _NUMPY:
        return None
    records = restaurant.get("daily_records", [])[-45:]
    if len(records) < 7:
        return None
    X_list, y_list = [], []
    for rec in records:
        f = _features(rec)
        t = rec.get("total_revenue_rm", 0)
        if f and t > 0:
            X_list.append(f)
            y_list.append(t)
    if len(X_list) < 7:
        return None
    X = np.array(X_list, dtype=float)
    y = np.array(y_list, dtype=float)
    y_norm = (y - y.mean()) / (y.std() + 1e-8)
    w = np.array(global_weights if len(global_weights) == PARAM_SIZE else _init(), dtype=float)
    vel = np.zeros_like(w)
    for _ in range(15):
        g = _gradients(X, y_norm, w)
        g_norm = np.linalg.norm(g) + 1e-9
        if g_norm > GRAD_CLIP:
            g = g * (GRAD_CLIP / g_norm)
        vel = 0.9 * vel - 0.005 * g
        w = w + vel
    w += np.random.laplace(0, 1.0 / DP_EPSILON, size=w.shape)
    global_w = np.array(
        global_weights if len(global_weights) == PARAM_SIZE else _init(), dtype=float
    )
    return (w - global_w).tolist()


def run_federated_round(db: dict) -> dict:
    if not _NUMPY:
        return {
            "updated_weights": [],
            "participants": 0,
            "skipped": 0,
            "error": "numpy not installed",
        }
    raw = db.get("federated_model", {}).get("weights", [])
    gw = raw if len(raw) == PARAM_SIZE else _init().tolist()
    all_d: list[list[float]] = []
    all_w: list[float] = []
    skipped = 0
    for rest in db.get("restaurants", []):

        if rest.get("is_demo", False):
            skipped += 1
            continue
        delta = train_local_model(rest, gw)
        if delta and len(delta) == PARAM_SIZE:
            all_d.append(delta)
            all_w.append(float(len(rest.get("daily_records", []))))
        else:
            skipped += 1
    if not all_d:
        return {"updated_weights": gw, "participants": 0, "skipped": skipped}
    total = sum(all_w) or 1.0
    agg = np.zeros(PARAM_SIZE)
    for d, pw in zip(all_d, all_w):
        agg += np.array(d) * (pw / total)
    new_w = (np.array(gw) + agg).tolist()
    db.setdefault("federated_model", {}).update(
        {
            "weights": new_w,
            "updated_at": datetime.datetime.utcnow().isoformat(),
            "version": db.get("federated_model", {}).get("version", 0) + 1,
            "architecture": f"MLP {FEATURE_DIM }x{HIDDEN1 }x{HIDDEN2 }x1",
            "dp_epsilon": DP_EPSILON,
            "param_count": PARAM_SIZE,
        }
    )
    return {
        "updated_weights": new_w,
        "participants": len(all_d),
        "skipped": skipped,
        "dp_epsilon": DP_EPSILON,
    }
