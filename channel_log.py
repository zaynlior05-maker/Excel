"""
Log channel — pushes every bot event to your private Telegram channel.
All events now show both user ID and @username.
"""

import logging
from datetime import datetime, timezone

from telegram import Bot

import config

_bot: Bot | None = None
logger = logging.getLogger(__name__)


def init(bot: Bot) -> None:
    global _bot
    _bot = bot
    if config.LOG_CHANNEL_ID:
        logger.info("Log channel initialised → %s", config.LOG_CHANNEL_ID)
    else:
        logger.warning("LOG_CHANNEL_ID not set — channel logging disabled.")


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%d/%m %H:%M UTC")


async def _tag(user_id: int) -> str:
    """Return 'ID (@username)' or just 'ID' if no username stored."""
    try:
        import db as _db
        uname = await _db.get_username(user_id)
        if uname:
            return f"<code>{user_id}</code> (@{uname})"
    except Exception:
        pass
    return f"<code>{user_id}</code>"


async def log(text: str) -> None:
    if not _bot:
        logger.warning("channel_log: bot not initialised")
        return
    if not config.LOG_CHANNEL_ID:
        return
    try:
        await _bot.send_message(config.LOG_CHANNEL_ID, text, parse_mode="HTML")
    except Exception as e:
        logger.error("channel_log send error → %s: %s", config.LOG_CHANNEL_ID, e)


# ── Events ─────────────────────────────────────────────────────────────────

async def user_start(user_id: int, username: str, is_new: bool) -> None:
    icon = "🆕" if is_new else "👋"
    tag  = f"@{username}" if username else "no username"
    await log(
        f"{icon} <b>{'New User' if is_new else 'User Started'}</b>\n"
        f"ID: <code>{user_id}</code>  ({tag})\n"
        f"🕐 {_now()}"
    )


async def nav_event(user_id: int, page: str, detail: str = "") -> None:
    line = f"📍 <b>{page}</b>"
    if detail:
        line += f" — {detail}"
    tag = await _tag(user_id)
    await log(f"{line}\nUser: {tag}\n🕐 {_now()}")


async def bin_search(user_id: int, bin_digits: str, found: int, subl_id: str = "") -> None:
    icon   = "✅" if found else "❌"
    result = f"<b>{found} match(es)</b>" if found else "No stock found"
    tag    = await _tag(user_id)
    await log(
        f"🔍 <b>BIN Search</b>\n"
        f"User:   {tag}\n"
        f"BIN:    <code>{bin_digits}</code>\n"
        f"Result: {icon} {result}\n"
        f"🕐 {_now()}"
    )


async def item_viewed(user_id: int, bin_: str, year: str,
                      code: str, price: float, subl_id: str) -> None:
    tag = await _tag(user_id)
    await log(
        f"👀 <b>Item Viewed</b>\n"
        f"User:  {tag}\n"
        f"Item:  {bin_} — {year} — {code} — "
        f"{config.CURRENCY_SYMBOL}{price:g}\n"
        f"List:  {subl_id}\n"
        f"🕐 {_now()}"
    )


async def topup_started(user_id: int, amount: float, coin: str) -> None:
    tag = await _tag(user_id)
    await log(
        f"💳 <b>Top-Up Started</b>\n"
        f"User:   {tag}\n"
        f"Amount: <b>{config.CURRENCY_SYMBOL}{amount:g}</b>\n"
        f"Coin:   {coin}\n"
        f"🕐 {_now()}"
    )


async def proof_submitted(user_id: int, amount: float,
                          coin: str, tx_ref: str, payment_id: str) -> None:
    tag = await _tag(user_id)
    ref = tx_ref.replace("txid:", "").replace("photo:", "📷 screenshot")
    await log(
        f"📤 <b>Payment Proof Submitted</b>\n"
        f"User:   {tag}\n"
        f"Amount: <b>{config.CURRENCY_SYMBOL}{amount:.2f}</b>\n"
        f"Coin:   {coin}\n"
        f"Ref:    {ref}\n"
        f"Pay ID: <code>{payment_id}</code>\n"
        f"🕐 {_now()}"
    )


async def payment_approved(user_id: int, amount: float,
                           new_balance: float, admin_id: int) -> None:
    tag = await _tag(user_id)
    await log(
        f"✅ <b>Payment Approved</b>\n"
        f"User:        {tag}\n"
        f"Credited:    <b>{config.CURRENCY_SYMBOL}{amount:.2f}</b>\n"
        f"New balance: {config.CURRENCY_SYMBOL}{new_balance:.2f}\n"
        f"Approved by: <code>{admin_id}</code>\n"
        f"🕐 {_now()}"
    )


async def payment_rejected(user_id: int, amount: float, admin_id: int) -> None:
    tag = await _tag(user_id)
    await log(
        f"❌ <b>Payment Rejected</b>\n"
        f"User:      {tag}\n"
        f"Amount:    {config.CURRENCY_SYMBOL}{amount:.2f}\n"
        f"Rejected by: <code>{admin_id}</code>\n"
        f"🕐 {_now()}"
    )


async def purchase_made(user_id: int, bin_: str, year: str, code: str,
                        price: float, new_balance: float, subl_id: str) -> None:
    tag = await _tag(user_id)
    await log(
        f"🛒 <b>Purchase</b>\n"
        f"User:        {tag}\n"
        f"Item:        {bin_} — {year} — {code} — "
        f"{config.CURRENCY_SYMBOL}{price:g}\n"
        f"List:        {subl_id}\n"
        f"New balance: {config.CURRENCY_SYMBOL}{new_balance:.2f}\n"
        f"🕐 {_now()}"
    )


async def user_banned(target_id: int, admin_id: int, banned: bool) -> None:
    tag    = await _tag(target_id)
    icon   = "🚫" if banned else "✅"
    action = "Banned" if banned else "Unbanned"
    await log(
        f"{icon} <b>User {action}</b>\n"
        f"User:  {tag}\n"
        f"By:    <code>{admin_id}</code>\n"
        f"🕐 {_now()}"
    )


async def balance_adjusted(target_id: int, admin_id: int,
                           delta: float, new_balance: float) -> None:
    tag  = await _tag(target_id)
    icon = "➕" if delta >= 0 else "➖"
    await log(
        f"{icon} <b>Balance Adjusted</b>\n"
        f"User:        {tag}\n"
        f"Change:      {config.CURRENCY_SYMBOL}{abs(delta):g}\n"
        f"New balance: {config.CURRENCY_SYMBOL}{new_balance:.2f}\n"
        f"By admin:    <code>{admin_id}</code>\n"
        f"🕐 {_now()}"
    )


async def broadcast_sent(admin_id: int, sent: int, failed: int) -> None:
    await log(
        f"📢 <b>Broadcast Sent</b>\n"
        f"By:     <code>{admin_id}</code>\n"
        f"Sent:   {sent}\n"
        f"Failed: {failed}\n"
        f"🕐 {_now()}"
    )
