"""food_ai - turn a photo of a dish or a barcode into kcal.

- estimate_dish(): Claude looks at the photo and estimates items, grams and kcal (a rough estimate).
- decode_barcode() + lookup_barcode(): read the barcode and fetch kcal/100 g from Open Food Facts.
"""
from __future__ import annotations

import base64
import io
import json
import os
import re
import urllib.request

MODEL = os.environ.get("FOOD_MODEL", "claude-sonnet-5-5")

DISH_PROMPT = (
    "You are a careful nutrition assistant. Look at this photo of a meal. Identify each food item, "
    "estimate its portion in grams from visual cues (plate size, cutlery, packaging), and estimate its calories. "
    "Reply with JSON only, no other text: "
    '{"dish": "short name", "items": [{"name": "...", "grams": 0, "kcal": 0}], '
    '"total_kcal": 0, "confidence": "low|medium|high", "note": "one short caveat"}. '
    "If the photo is not food, set total_kcal to 0 and explain in note."
)


def json_from(text: str) -> dict:
    """Pull the first JSON object out of a model reply (it may add words or code fences)."""
    a, b = text.find("{"), text.rfind("}")
    if a < 0 or b < a:
        raise ValueError("no JSON in reply")
    return json.loads(text[a : b + 1])


def estimate_dish(image: bytes, media_type: str = "image/jpeg", caption: str = "") -> dict:
    import anthropic

    prompt = DISH_PROMPT + (f"\nThe user says: {caption}" if caption else "")
    r = anthropic.Anthropic().messages.create(
        model=MODEL,
        max_tokens=600,
        messages=[{"role": "user", "content": [
            {"type": "image", "source": {"type": "base64", "media_type": media_type,
                                         "data": base64.b64encode(image).decode()}},
            {"type": "text", "text": prompt},
        ]}],
    )
    d = json_from("".join(b.text for b in r.content if b.type == "text"))
    d["total_kcal"] = int(d.get("total_kcal") or 0)
    return d


def decode_barcode(image: bytes) -> str | None:
    """Return the first barcode number found in the photo, or None."""
    try:
        import zxingcpp
        from PIL import Image
    except ImportError:
        return None
    found = zxingcpp.read_barcodes(Image.open(io.BytesIO(image)))
    return found[0].text if found else None


def lookup_barcode(code: str) -> dict | None:
    """Open Food Facts lookup. Returns {name, kcal100, serving_g, p100, c100, f100} or None if unknown."""
    if not re.fullmatch(r"\d{6,14}", code):
        return None
    url = (f"https://world.openfoodfacts.org/api/v2/product/{code}.json"
           "?fields=product_name,brands,nutriments,serving_quantity")
    req = urllib.request.Request(url, headers={"User-Agent": "StarshipHome/1.0 (personal project)"})
    with urllib.request.urlopen(req, timeout=10) as resp:
        data = json.load(resp)
    if data.get("status") != 1:
        return None
    p = data.get("product") or {}
    n = p.get("nutriments") or {}
    kcal100 = n.get("energy-kcal_100g")
    if kcal100 is None and n.get("energy_100g") is not None:  # kJ only
        kcal100 = float(n["energy_100g"]) / 4.184
    if kcal100 is None:
        return None
    sq = p.get("serving_quantity")
    name = " ".join(x for x in (p.get("brands", "").split(",")[0].strip(), p.get("product_name", "")) if x) or code
    def per100(key):
        v = n.get(key)
        return float(v) if isinstance(v, (int, float)) else None

    return {"name": name, "kcal100": float(kcal100), "serving_g": float(sq) if sq else None,
            "p100": per100("proteins_100g"), "c100": per100("carbohydrates_100g"), "f100": per100("fat_100g")}


def kcal_for_grams(kcal100: float, grams: float) -> int:
    return round(kcal100 * grams / 100)


def parse_quick_meal(text: str) -> tuple[str, int] | None:
    """'pasta 600' or 'Pasta al pomodoro 600 kcal' -> ('Pasta al pomodoro', 600)."""
    m = re.fullmatch(r"\s*(.+?)\s+(\d{2,4})\s*(?:kcal)?\s*", text or "", flags=re.I)
    return (m.group(1), int(m.group(2))) if m else None
