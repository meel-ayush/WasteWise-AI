import os, sys, re

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)


def _clean_ingredient_key(raw_key: str) -> str:
    key = raw_key.strip()

    if '_' in key:
        parts = key.split('_')
        unit = None
        if parts[-1] in ('g', 'ml', 'kg', 'l', 'pcs', 'pieces', 'no', 'nos'):
            unit = parts[-1]
            name_parts = parts[:-1]
        else:
            name_parts = parts
        name = ' '.join(p.capitalize() for p in name_parts)
        return f"{name} ({unit})" if unit else name

    unit_map = [
        ('pieces', 'pieces'), ('piece', 'piece'),
        ('ml', 'ml'), ('g', 'g'), ('kg', 'kg'), ('l', 'l'),
    ]
    unit_suffix = None
    stripped = key
    for suffix, label in unit_map:
        if stripped.lower().endswith(suffix) and len(stripped) > len(suffix):
            unit_suffix = label
            stripped = stripped[: -len(suffix)]
            break

    words = re.sub(r'([a-z])([A-Z])', r'\1 \2', stripped)
    words = re.sub(r'([a-zA-Z])(\d)', r'\1 \2', words)
    words = re.sub(r'(\d)([a-zA-Z])', r'\1 \2', words)

    if ' ' not in words:
        words = words.capitalize()

    name = words.strip().title()
    if unit_suffix:
        return f"{name} ({unit_suffix})"
    return name


def generate_bom_for_item(item_name: str, region: str, restaurant_type: str) -> dict:
    from services.ai_provider import call_ai_json

    prompt = (
        f"You are a professional Indian culinary expert who knows exact ingredient amounts used in commercial food businesses.\n\n"
        f"Generate the ingredient bill-of-materials (BOM) for ONE serving of: '{item_name}'\n"
        f"Restaurant type: {restaurant_type}\n"
        f"Location: {region}, India\n\n"
        "Use amounts typical for a commercial Indian food stall — not home cooking.\n"
        "Return a JSON object where:\n"
        "  - Keys are proper readable English ingredient names (e.g. 'Samosa', 'Chickpeas boiled', 'Tamarind chutney')\n"
        "  - Append unit in brackets: 'Rice (g)', 'Coconut milk (ml)', 'Eggs (nos)'\n"
        "  - Values are numeric quantities per serving\n"
        "  - Include 'cost_inr': estimated raw material cost per serving in Indian Rupees at 2024 wholesale prices\n\n"
        "Example: {\"Samosa (pieces)\": 2, \"Chickpeas boiled (g)\": 100, \"Tamarind chutney (ml)\": 30, \"Sev (g)\": 20, \"cost_inr\": 28.50}\n\n"
        "IMPORTANT: Use proper English names with spaces. Do NOT concatenate words. Return ONLY valid JSON."
    )

    raw = call_ai_json(prompt)
    if not isinstance(raw, dict) or not raw:
        return {}

    cleaned: dict = {}
    for k, v in raw.items():
        if not isinstance(v, (int, float)) or v <= 0:
            continue
        if k in ('cost_inr', 'cost_rm', 'cost'):
            cleaned['cost_inr'] = round(float(v), 2)
        else:
            readable_key = _clean_ingredient_key(k)
            cleaned[readable_key] = round(float(v), 2) if '.' in str(v) else v

    return cleaned


def ask_bom_conversational(
    item_name: str, region: str, restaurant_type: str, owner_input: str
) -> dict:
    from services.ai_provider import call_ai_json

    dont_know = any(
        p in owner_input.lower()
        for p in ["don't know", "dont know", "not sure", "no idea", "skip", "idk",
                  "tak tahu", "pata nahi", "nahi pata", "unknown"]
    )

    if dont_know or not owner_input.strip():
        return generate_bom_for_item(item_name, region, restaurant_type)

    prompt = (
        f"An Indian restaurant owner describes ingredients for '{item_name}' ({restaurant_type}, {region}, India):\n"
        f"\"{owner_input}\"\n\n"
        "Fill in any missing details based on how this dish is made commercially in India.\n"
        "Return a precise JSON BOM where:\n"
        "  - Keys are proper readable ingredient names with units in brackets: 'Rice (g)', 'Oil (ml)'\n"
        "  - Values are numeric quantities per serving\n"
        "  - Include 'cost_inr': estimated raw material cost per serving in Indian Rupees (₹)\n"
        "IMPORTANT: Use proper English names with spaces. Do NOT concatenate words.\n"
        "Return ONLY valid JSON."
    )

    raw = call_ai_json(prompt)
    if not isinstance(raw, dict) or not raw:
        return generate_bom_for_item(item_name, region, restaurant_type)

    cleaned: dict = {}
    for k, v in raw.items():
        if not isinstance(v, (int, float)) or v <= 0:
            continue
        if k in ('cost_inr', 'cost_rm', 'cost'):
            cleaned['cost_inr'] = round(float(v), 2)
        else:
            readable_key = _clean_ingredient_key(k)
            cleaned[readable_key] = round(float(v), 2) if '.' in str(v) else v

    return cleaned if cleaned else generate_bom_for_item(item_name, region, restaurant_type)
