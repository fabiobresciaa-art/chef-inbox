"""tg_inbox - stateless Telegram inbox logic (no database, no files, no always-on bot).

Telegram itself is the queue: it keeps messages sent to your bot for up to 24 hours until they are
confirmed. The ship asks for new ones when it is open, handles them, then confirms (ack).
"""
from __future__ import annotations

import base64
import io
import json
import os
import re
import urllib.request

import food_ai

DATE = re.compile(r"\d{4}-\d{2}-\d{2}")


class Telegram:
    def __init__(self, token: str):
        self.token = token

    def call(self, method: str, **params):
        req = urllib.request.Request(f"https://api.telegram.org/bot{self.token}/{method}",
                                     data=json.dumps(params).encode(), headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=20) as r:
            res = json.load(r)
        if not res.get("ok"):
            raise RuntimeError(res.get("description", "telegram error"))
        return res["result"]

    def download(self, file_path: str) -> bytes:
        with urllib.request.urlopen(f"https://api.telegram.org/file/bot{self.token}/{file_path}", timeout=30) as r:
            return r.read()


def pending(tg, allowed: set[int], after: int = 0, limit: int = 10) -> dict:
    """New photos, typed meals (name + kcal) and meal descriptions from allowed people, oldest first."""
    if not allowed:
        return {"items": [], "error": "Set TELEGRAM_ALLOWED_IDS on the server."}
    try:
        updates = tg.call("getUpdates", limit=100, timeout=0, allowed_updates=["message"])
    except Exception:
        return {"items": [], "error": "Could not reach Telegram."}
    items = []
    for u in updates:
        uid, m = u["update_id"], u.get("message") or {}
        if uid <= after or (m.get("from") or {}).get("id") not in allowed:
            continue
        text = m.get("caption") or m.get("text") or ""
        d = DATE.search(text)
        base = {"update_id": uid, "chat": m["chat"]["id"], "ts": m.get("date"), "day": d.group(0) if d else None}
        if m.get("photo"):
            items.append({**base, "kind": "photo", "file_id": m["photo"][-1]["file_id"], "caption": m.get("caption") or ""})
        elif m.get("text"):
            txt = DATE.sub("", m["text"]).strip()
            q = food_ai.parse_quick_meal(txt)
            if q:  # "pasta 600": the person gave the kcal
                items.append({**base, "kind": "meal", "name": q[0], "kcal": q[1]})
            elif txt and not txt.startswith("/"):  # a description: Chef works out the kcal
                items.append({**base, "kind": "describe", "text": txt[:300]})
    return {"items": sorted(items, key=lambda i: i["update_id"])[:limit]}


def shrink(data: bytes, side: int = 1024) -> bytes:
    """Smaller JPEG for the trip to the ship (and cheaper to analyse). Falls back to the original."""
    try:
        from PIL import Image
        im = Image.open(io.BytesIO(data))
        im.thumbnail((side, side))
        buf = io.BytesIO()
        im.convert("RGB").save(buf, "JPEG", quality=80)
        return buf.getvalue()
    except Exception:
        return data


def photo_payload(tg, file_id: str) -> dict:
    """The photo (base64, fetched into memory only) and, if it shows a known barcode, the product.

    If ANTHROPIC_API_KEY is set on the server, dish photos are also analysed here ("analysis"), so the
    ship works even in views that cannot send photos to Claude themselves. That uses your API credits.
    """
    try:
        data = tg.download(tg.call("getFile", file_id=file_id)["file_path"])
    except Exception:
        return {"ok": False, "error": "could not fetch the photo from Telegram"}
    small = shrink(data)
    out = {"ok": True, "mime": "image/jpeg", "b64": base64.b64encode(small).decode()}
    code = food_ai.decode_barcode(data)  # read the barcode on the full-size original
    if code:
        try:
            prod = food_ai.lookup_barcode(code)
        except Exception:
            prod = None
        if prod:
            out["barcode"] = {"code": code, **prod}
            return out
    if os.environ.get("ANTHROPIC_API_KEY"):
        try:
            out["analysis"] = food_ai.estimate_dish(small, "image/jpeg")
        except Exception:
            pass
    return out


def acknowledge(tg, allowed: set[int], update_id: int, chat: int, text: str) -> dict:
    """Confirm everything up to update_id (Telegram forgets it) and message the person back."""
    if chat not in allowed:
        return {"ok": False}
    try:
        tg.call("getUpdates", offset=update_id + 1, limit=1, timeout=0)
        tg.call("sendMessage", chat_id=chat, text=str(text)[:500])
    except Exception:
        return {"ok": False}
    return {"ok": True}
