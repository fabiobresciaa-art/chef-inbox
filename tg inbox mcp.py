"""Connector server for the ship (a claude.ai artifact). Stateless: nothing is stored.

Env:  TELEGRAM_BOT_TOKEN   from @BotFather
      TELEGRAM_ALLOWED_IDS your Telegram user id(s), comma separated
      INBOX_SECRET         random string, 16+ characters; it is part of the connector address
Run:  python tg_inbox_mcp.py     (listens on $PORT, default 8765)
Connector address:  https://YOUR-HOST/<INBOX_SECRET>/mcp   name it exactly: Starship Inbox
"""
import os

from fastmcp import FastMCP

import tg_inbox

TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
SECRET = os.environ.get("INBOX_SECRET", "")
ALLOWED = {int(x) for x in os.environ.get("TELEGRAM_ALLOWED_IDS", "").replace(" ", "").split(",") if x}
if not TOKEN or len(SECRET) < 16:
    raise SystemExit("Set TELEGRAM_BOT_TOKEN and INBOX_SECRET (16+ random characters).")

tg = tg_inbox.Telegram(TOKEN)
mcp = FastMCP("Starship Inbox")


@mcp.tool
def list_inbox(after: int = 0) -> dict:
    """New photos and typed meals sent to the Telegram bot, with update_id greater than `after`."""
    return tg_inbox.pending(tg, ALLOWED, after)


@mcp.tool
def get_photo(file_id: str) -> dict:
    """A Telegram photo as base64 JPEG (fetched live, never saved), plus barcode product info if any."""
    return tg_inbox.photo_payload(tg, file_id)


@mcp.tool
def ack(update_id: int, chat: int, text: str) -> dict:
    """Confirm handled messages up to update_id and send `text` back to the Telegram chat."""
    return tg_inbox.acknowledge(tg, ALLOWED, update_id, chat, text)


if __name__ == "__main__":
    kw = dict(transport="http", host=os.environ.get("INBOX_HOST", "0.0.0.0"),
              port=int(os.environ.get("PORT") or "8765"), path=f"/{SECRET}/mcp")
    try:
        mcp.run(stateless_http=True, **kw)  # a sleeping host forgets sessions, so do not rely on them
    except TypeError:
        mcp.run(**kw)
