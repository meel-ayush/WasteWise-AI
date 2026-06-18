from __future__ import annotations
import re
from typing import Optional

UNIT_MAP: dict[str, tuple[str, float]] = {
    "kg": ("kg", 1.0),
    "kilogram": ("kg", 1.0),
    "kilo": ("kg", 1.0),
    "g": ("kg", 0.001),
    "gram": ("kg", 0.001),
    "grams": ("kg", 0.001),
    "l": ("L", 1.0),
    "liter": ("L", 1.0),
    "litre": ("L", 1.0),
    "ml": ("L", 0.001),
    "milliliter": ("L", 0.001),
    "bag": ("bag", 1.0),
    "bags": ("bag", 1.0),
    "beg": ("bag", 1.0),
    "bottle": ("bottle", 1.0),
    "botol": ("bottle", 1.0),
    "bottles": ("bottle", 1.0),
    "pack": ("pack", 1.0),
    "paket": ("pack", 1.0),
    "pcs": ("pcs", 1.0),
    "piece": ("pcs", 1.0),
    "pieces": ("pcs", 1.0),
    "tin": ("tin", 1.0),
    "tins": ("tin", 1.0),
    "box": ("box", 1.0),
    "kotak": ("box", 1.0),
}

MALAY: dict[str, str] = {
    "beras": "rice",
    "ayam": "chicken",
    "daging": "beef",
    "ikan": "fish",
    "telur": "egg",
    "minyak": "cooking oil",
    "garam": "salt",
    "gula": "sugar",
    "tepung": "flour",
    "santan": "coconut milk",
    "cili": "chili",
    "bawang": "onion",
    "kentang": "potato",
    "sayur": "vegetables",
    "tauhu": "tofu",
    "mee": "noodles",
    "mihun": "vermicelli",
}

_UNIT_PAT = "|".join(re.escape(u) for u in sorted(UNIT_MAP.keys(), key=len, reverse=True))


def _parse_quantities(text: str) -> dict[str, dict]:
    results: dict[str, dict] = {}
    for line in text.replace(",", "\n").replace(";", "\n").split("\n"):
        line = line.strip()
        if not line:
            continue
        m = re.search(rf'(\d+\.?\d*)\s*({_UNIT_PAT })\s+([a-zA-Z\s]{{2,30}})', line, re.IGNORECASE)
        if m:
            qty_str, unit_raw, ing_raw = m.group(1), m.group(2).lower(), m.group(3).strip().lower()
        else:
            m = re.search(
                rf'([a-zA-Z\s]{{2,25}})[:\s]+(\d+\.?\d*)\s*({_UNIT_PAT })', line, re.IGNORECASE
            )
            if not m:
                continue
            ing_raw, qty_str, unit_raw = m.group(1).strip().lower(), m.group(2), m.group(3).lower()
        unit_info = UNIT_MAP.get(unit_raw)
        if not unit_info:
            continue
        canonical_unit, multiplier = unit_info
        qty = float(qty_str) * multiplier
        ingredient = MALAY.get(ing_raw, ing_raw).strip().title()
        if ingredient and qty > 0:
            results[ingredient] = {
                "qty": round(qty, 3),
                "unit": canonical_unit,
                "raw": line.strip(),
            }
    return results


def _fuzzy_match(ingredient: str, bom_keys: set[str]) -> Optional[str]:
    ing_lower = ingredient.lower()
    for key in bom_keys:
        if ing_lower == key.lower():
            return key
    for key in bom_keys:
        if ing_lower in key.lower() or key.lower() in ing_lower:
            return key

    def tri(s: str) -> set[str]:
        s = s.lower().strip()
        return {s[i : i + 3] for i in range(max(len(s) - 2, 0))}

    best, best_score = None, 0.0
    ing_tri = tri(ingredient)
    for key in bom_keys:
        kt = tri(key)
        if not ing_tri or not kt:
            continue
        score = len(ing_tri & kt) / (len(ing_tri | kt) + 1e-9)
        if score > best_score and score > 0.35:
            best_score = score
            best = key
    return best


def scan_inventory_from_image(image_bytes: bytes, restaurant: dict) -> dict:
    bom_keys = set(restaurant.get("bom", {}).keys())
    if not bom_keys:
        bom_keys = {m["item"] for m in restaurant.get("menu", [])}

    try:
        from services.ai_provider import call_ai_with_image, ai_available

        if ai_available():
            prompt = (
                "You are scanning inventory for a indian food stall.\n"
                "List all visible ingredients with their quantities. One line per item:\n"
                "  QUANTITY UNIT INGREDIENT_NAME\n"
                "Examples: '5 kg beras', '2 botol minyak', '10 pcs telur'\n"
                "Only include items where quantity is clearly visible. Output ingredient lines only."
            )
            raw = call_ai_with_image(prompt, image_bytes, "image/jpeg")
            if raw and raw.strip():
                detected = _parse_quantities(raw)
                if detected:
                    matched = [m for ing in detected if (m := _fuzzy_match(ing, bom_keys))]
                    unmatched = [ing for ing in detected if not _fuzzy_match(ing, bom_keys)]
                    return {
                        "detected": {
                            k: {"qty": v["qty"], "unit": v["unit"]} for k, v in detected.items()
                        },
                        "matched_bom": matched,
                        "unmatched": unmatched,
                        "confidence": round(min(1.0, 0.5 + 0.1 * len(detected)), 2),
                        "method": "gemini_vision",
                        "message": f"Detected {len (detected )} ingredient(s). {len (matched )} matched your BOM.",
                    }
    except Exception as e:
        print(f"[CV Inventory] Gemini vision failed: {e }")

    try:
        import easyocr

        reader = easyocr.Reader(["en", "ms"], gpu=False, verbose=False)
        ocr_result = reader.readtext(image_bytes)
        if ocr_result:
            raw_text = "\n".join(r[1] for r in ocr_result if r[2] > 0.45)
            detected = _parse_quantities(raw_text)
            if detected:
                matched = [m for ing in detected if (m := _fuzzy_match(ing, bom_keys))]
                unmatched = [ing for ing in detected if not _fuzzy_match(ing, bom_keys)]
                avg_conf = sum(r[2] for r in ocr_result) / max(len(ocr_result), 1)
                return {
                    "detected": {
                        k: {"qty": v["qty"], "unit": v["unit"]} for k, v in detected.items()
                    },
                    "matched_bom": matched,
                    "unmatched": unmatched,
                    "confidence": round(avg_conf, 2),
                    "method": "easyocr",
                    "message": f"EasyOCR detected {len (detected )} ingredient(s) (avg confidence {avg_conf :.0%}).",
                }
    except ImportError:
        pass
    except Exception as e:
        print(f"[CV Inventory] EasyOCR failed: {e }")

    return {
        "detected": {},
        "matched_bom": [],
        "unmatched": [],
        "confidence": 0.0,
        "method": "unavailable",
        "message": (
            "Could not auto-detect inventory from this image.\n\n"
            "Tips for better results:\n"
            "• Make sure labels are clearly visible and well-lit\n"
            "• Write quantities on containers before photographing\n"
            "• You can also type your stock counts manually in the chat"
        ),
    }
