"""
Telegram Store Bot
  Part 1: Welcome interface & Rules
  Part 2: Wallet with crypto top-ups (USDT / BTC) via NOWPayments
  Part 3: Store navigation with categories, sub-lists, paginated line items
  Part 4: Full admin panel
"""

import logging
from decimal import Decimal

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardRemove,
    BotCommand,
)
from telegram.error import BadRequest
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

import admin

class _NullLog:
    """Silent fallback used ONLY when channel_log.py cannot be imported at all."""
    def __getattr__(self, _):
        async def _noop(*a, **kw): pass
        return _noop
    def init(self, bot): pass

try:
    import channel_log
    # Ensure the module has an init function (older versions may not)
    if not hasattr(channel_log, 'init'):
        channel_log.init = lambda bot: None
except ImportError:
    channel_log = _NullLog()

import config
import db
import payments

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


# ============================================================
#  Text + keyboards
# ============================================================
_DEFAULT_WELCOME = (
    "🛍️ <b>Welcome to the Store!</b>\n\n"
    "Use the menu below to get started. 👇"
)


def welcome_text() -> str:
    """Reads from the labels DB — change it from /admin → Labels → Welcome Message."""
    return db.get_label("welcome_text", _DEFAULT_WELCOME)


def _txt(key: str, default: str) -> str:
    """Read a configurable UI text from the labels DB."""
    return db.get_label(key, default)


def _tg_url(https_url: str) -> str:
    """Convert https://t.me/username → tg://resolve?domain=username (one-tap open)."""
    import re
    url = (https_url or "").strip()
    if not url:
        return ""
    match = re.match(r"https?://t\.me/([A-Za-z0-9_]+)", url)
    if match:
        return f"tg://resolve?domain={match.group(1)}"
    if url.startswith("@"):
        return f"tg://resolve?domain={url.lstrip('@')}"
    return url


def main_menu() -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(db.get_label("menu:store",  "🛒 Store"),  callback_data="store"),
            InlineKeyboardButton(db.get_label("menu:wallet", "💵 Wallet"), callback_data="wallet"),
        ],
        [InlineKeyboardButton(db.get_label("menu:rules", "🛡️ Rules"), callback_data="rules")],
    ]
    # Only add URL buttons if the URLs are actually configured
    url_row = []
    support_url = _tg_url(config.SUPPORT_URL)
    channel_url = _tg_url(config.CHANNEL_URL)
    if support_url:
        url_row.append(InlineKeyboardButton(
            db.get_label("menu:support", "☎️ Support ↗"), url=support_url))
    if channel_url:
        url_row.append(InlineKeyboardButton(
            db.get_label("menu:channel", "📄 Channel ↗"), url=channel_url))
    if url_row:
        rows.append(url_row)
    return InlineKeyboardMarkup(rows)


def back_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("\u2B05\uFE0F Back to Menu", callback_data="menu")]]
    )


def store_menu() -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(
                db.get_label(f"cat:{c['id']}", c["label"]),
                callback_data=f"cat:{c['id']}")]
            for c in config.CATEGORIES]
    rows.append([InlineKeyboardButton("\U0001F30F Main Menu", callback_data="menu")])
    return InlineKeyboardMarkup(rows)


def find_category(cat_id):
    return next((c for c in config.CATEGORIES if c["id"] == cat_id), None)


def find_sublist(cat, subl_id: str) -> dict | None:
    """Find sublist from DB cache (works even if cat is None)."""
    if cat is None:
        return db.find_sublist_by_id(subl_id)
    for s in db.get_sublists(cat["id"]):
        if s["id"] == subl_id:
            return s
    return None


def sublist_menu(cat) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(
                db.get_label(f"subl:{s['id']}", s["label"]),
                callback_data=f"subl:{cat['id']}:{s['id']}")]
            for s in db.get_sublists(cat["id"])]
    rows.append([InlineKeyboardButton("\U0001F50D Search for BIN",
                                      callback_data=f"binsearch:{cat['id']}")])
    rows.append([InlineKeyboardButton("\u2B05\uFE0F Back to Store", callback_data="store")])
    rows.append([InlineKeyboardButton("\U0001F30F Main Menu",        callback_data="menu")])
    return InlineKeyboardMarkup(rows)


def sublist_back_menu(cat_id) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("\u2B05\uFE0F Back",    callback_data=f"cat:{cat_id}")],
        [InlineKeyboardButton("\U0001F30F Main Menu", callback_data="menu")],
    ])


def format_line(item) -> str:
    price_str = f"{float(item['price']):g}"
    return (f"{item['bin']} - {item['year']} - {item['code']} - "
            f"{config.CURRENCY_SYMBOL}{price_str}")


def lines_menu(cat_id, subl_id, items, page=0, cart=None) -> InlineKeyboardMarkup:
    """
    Cart-aware item listing.
    - Items show ✅ prefix when selected.
    - Tapping an item toggles selection (sel: callback).
    - Checkout button appears when cart is not empty.
    - Refresh resets to page 0 via refresh: callback.
    """
    if cart is None:
        cart = {}

    per         = config.ITEMS_PER_PAGE
    total_pages = max(1, (len(items) + per - 1) // per)
    page        = max(0, min(page, total_pages - 1))
    page_items  = items[page * per : page * per + per]

    rows = []
    for it in page_items:
        selected = it["id"] in cart
        label    = ("✅ " if selected else "") + format_line(it)
        rows.append([InlineKeyboardButton(
            label, callback_data=f"sel:{subl_id}:{it['id']}:{page}"
        )])

    # Checkout button — only when something is in the cart
    if cart:
        selected_items = [it for it in items if it["id"] in cart]
        total = sum(float(it["price"]) for it in selected_items)
        count = len(cart)
        s     = "s" if count != 1 else ""
        rows.append([InlineKeyboardButton(
            f"🛒 Proceed to Buy ({count} Item{s} Selected)  "
            f"{config.CURRENCY_SYMBOL}{total:g}",
            callback_data=f"cart_checkout:{cat_id}:{subl_id}:{page}",
        )])

    # Navigation — all full-width, Refresh uses separate callback
    rows.append([InlineKeyboardButton(
        "🔄 Refresh",
        callback_data=f"refresh:{cat_id}:{subl_id}",
    )])
    if page < total_pages - 1:
        rows.append([InlineKeyboardButton(
            "Next \u27A1\uFE0F",
            callback_data=f"page:{cat_id}:{subl_id}:{page + 1}",
        )])
    if page > 0:
        rows.append([InlineKeyboardButton(
            "\u2B05\uFE0F Previous Page",
            callback_data=f"page:{cat_id}:{subl_id}:{page - 1}",
        )])

    subl_default = next(
        (s["label"] for cat in config.CATEGORIES
         for s in cat.get("sublists", []) if s["id"] == subl_id),
        subl_id,
    )
    rows.append([InlineKeyboardButton(
        f"\U0001F519 {db.get_label(f'subl:{subl_id}', subl_default)}",
        callback_data=f"cat:{cat_id}",
    )])
    rows.append([InlineKeyboardButton("🌏 Main Menu", callback_data="menu")])
    return InlineKeyboardMarkup(rows)

    subl_default = next(
        (s["label"] for cat in config.CATEGORIES
         for s in cat.get("sublists", []) if s["id"] == subl_id),
        subl_id,
    )
    rows.append([InlineKeyboardButton(
        f"\U0001F519 {db.get_label(f'subl:{subl_id}', subl_default)}",
        callback_data=f"cat:{cat_id}",
    )])
    rows.append([InlineKeyboardButton(
        "\U0001F30F Main Menu", callback_data="menu"
    )])
    return InlineKeyboardMarkup(rows)


def wallet_menu(balance_str: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("\u2795 Top Up", callback_data="topup")],
        [InlineKeyboardButton("\u2B05\uFE0F Back to Menu", callback_data="menu")],
    ])


def amount_menu() -> InlineKeyboardMarkup:
    presets = config.TOPUP_PRESETS
    rows = []
    for i in range(0, len(presets), 2):
        pair = presets[i:i + 2]
        rows.append([
            InlineKeyboardButton(
                f"\U0001F538 {config.CURRENCY_SYMBOL}{a}",
                callback_data=f"topup_amt:{a}",
            )
            for a in pair
        ])
    rows.append([InlineKeyboardButton("\U0001F4B0 Custom Amount", callback_data="topup_custom")])
    rows.append([InlineKeyboardButton("\U0001F30F Main Menu",      callback_data="menu")])
    return InlineKeyboardMarkup(rows)


def coin_menu() -> InlineKeyboardMarkup:
    """Show only coins that have a wallet address configured."""
    coins = payments.active_coins()
    rows  = [[InlineKeyboardButton(name, callback_data=f"topup_coin:{name}")]
             for name in coins]
    rows.append([InlineKeyboardButton("\u2B05\uFE0F Back", callback_data="topup")])
    return InlineKeyboardMarkup(rows)


def sent_menu(payment_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(
            "\u2705 I've Sent the Payment",
            callback_data=f"pay_sent:{payment_id}",
        )],
        [InlineKeyboardButton("\u274C Cancel", callback_data="wallet")],
    ])


async def safe_edit(query, text, reply_markup) -> None:
    try:
        await query.edit_message_text(text, reply_markup=reply_markup, parse_mode="HTML")
    except BadRequest as e:
        if "not modified" not in str(e).lower():
            logger.warning("safe_edit BadRequest: %s", e)
    except Exception as e:
        logger.warning("safe_edit error: %s", e)


# ============================================================
#  /start and user commands
# ============================================================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    uid      = update.effective_user.id
    username = update.effective_user.username or ""

    # DB setup — never crash if DB has issues
    try:
        existing = await db.get_user_info(uid)
        is_new   = existing is None
        await db.ensure_user(uid, username)
        await channel_log.user_start(uid, username, is_new)
        banned = await db.is_banned(uid)
    except Exception as e:
        logger.error("start: DB error: %s", e)
        banned = False

    if banned:
        await update.message.reply_text(
            "\U0001F6AB You have been banned from this store.\n"
            f"Contact {config.SUPPORT_HANDLE} if you think this is a mistake.",
            reply_markup=ReplyKeyboardRemove(),
        )
        return

    # 1 — Refund policy (wrapped so bad HTML never kills /start)
    try:
        refund = db.get_label("refund_text", config.REFUND_RULES_TEXT)
        await update.message.reply_text(
            refund,
            reply_markup=ReplyKeyboardRemove(),
            parse_mode="HTML",
        )
    except Exception as e:
        logger.error("start: refund rules send error: %s", e)
        await update.message.reply_text(
            "📋 Please read our refund policy and rules before purchasing.",
            reply_markup=ReplyKeyboardRemove(),
        )

    # 2 — Welcome banner
    try:
        await update.message.reply_text(welcome_text(), parse_mode="HTML")
    except Exception as e:
        logger.error("start: welcome text error: %s", e)
        await update.message.reply_text("Welcome to the Store 👋")

    # 3 — Menu buttons (this must always succeed)
    await update.message.reply_text("Use the buttons below 👇", reply_markup=main_menu())


async def cmd_myid(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Anyone can use this to see their exact Telegram user ID."""
    uid = update.effective_user.id
    is_admin_user = uid in config.ADMIN_IDS
    await update.message.reply_text(
        f"🪪 Your Telegram ID: <code>{uid}</code>\n\n"
        f"Admin access: {'✅ YES' if is_admin_user else '❌ NO — this ID is not in ADMIN_IDS'}\n\n"
        f"ADMIN_IDS set in Railway: <code>{config.ADMIN_IDS or 'empty — not set!'}</code>",
        parse_mode="HTML",
    )
    u = update.effective_user
    await db.ensure_user(u.id, u.username or "")
    await update.message.reply_text(
        f"<code>──────────────────────</code>\n{_txt('store_cat_text', 'Choose a category:')}",
        reply_markup=store_menu(),
        parse_mode="HTML",
    )


async def cmd_store(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    u = update.effective_user
    await db.ensure_user(u.id, u.username or "")
    await update.message.reply_text(
        f"<code>──────────────────────</code>\n{_txt('store_cat_text', 'Choose a category:')}",
        reply_markup=store_menu(),
        parse_mode="HTML",
    )


async def _wallet_text(uid: int) -> str:
    """Build the wallet/profile card shown in the wallet section."""
    info = await db.get_user_info(uid)
    bal  = await db.get_balance(uid)
    bal_str = f"{config.CURRENCY_SYMBOL}{bal:.2f}"
    if info and info.get("joined"):
        join_date = info["joined"].strftime("%m-%d-%Y")
    else:
        join_date = "—"
    return (
        "<code>============================</code>\n"
        f"🪪 <b>ID:</b> <code>{uid}</code>\n"
        f"💰 <b>Balance:</b> <b>{bal_str}</b>\n"
        f"📅 <b>Join Date:</b> {join_date}\n"
        "<code>============================</code>"
    )


async def cmd_wallet(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    u   = update.effective_user
    uid = u.id
    await db.ensure_user(uid, u.username or "")
    bal     = await db.get_balance(uid)
    bal_str = f"{config.CURRENCY_SYMBOL}{bal:.2f}"
    text    = await _wallet_text(uid)
    await update.message.reply_text(
        text,
        reply_markup=wallet_menu(bal_str),
        parse_mode="HTML",
    )


async def cmd_rules(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        db.get_label("rules_text", config.RULES_TEXT),
        reply_markup=back_menu(),
        parse_mode="HTML",
    )


async def cmd_support(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        f"\u260E\uFE0F <b>Support</b>\n\nContact us: {config.SUPPORT_HANDLE}",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton(
                "\u260E\uFE0F Open Support Chat",
                url=_tg_url(config.SUPPORT_URL),
            )
        ]]),
        parse_mode="HTML",
    )


# ============================================================
#  Text routing
# ============================================================
async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    # Admin password entry — must be first, before all other routing
    if context.user_data.get("adm_awaiting_pw"):
        await admin.handle_admin_password(update, context)
        return
    if context.user_data.get("adm_awaiting"):
        await admin.adm_text(update, context)
        return
    awaiting = context.user_data.get("awaiting")
    if awaiting == "topup_amount":
        await handle_topup_amount(update, context)
        return
    elif awaiting == "proof":
        await handle_proof_text(update, context)
        return
    elif awaiting == "bin_search":
        await handle_bin_search(update, context)
        return

    # ── Admin ghost sender ──────────────────────────────────────────
    # Admin sent plain text not caught by any flow → relay it and stop
    uid = update.effective_user.id if update.effective_user else 0
    if admin.is_admin(uid, context.user_data):
        await admin_relay(update, context)
        return

    # ── Reply-keyboard shortcuts (regular users) ────────────────────
    txt   = update.message.text.strip().lower()
    clean = re.sub(r'[^\w\s]', '', txt).strip().lower()
    if "store" in clean:
        await cmd_store(update, context)
    elif "wallet" in clean:
        await cmd_wallet(update, context)
    elif "rules" in clean:
        await cmd_rules(update, context)
    elif "support" in clean:
        await cmd_support(update, context)
    elif "channel" in clean or "update" in clean:
        await context.bot.send_message(
            update.effective_user.id,
            f"\U0001F4E2 <b>Updates Channel</b>\n\nJoin us here:",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton(
                    "\U0001F4E2 Open Channel",
                    url=_tg_url(config.CHANNEL_URL),
                )
            ]]),
            parse_mode="HTML",
        )


async def on_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Screenshot submitted as payment proof."""
    if context.user_data.get("awaiting") == "proof":
        await handle_proof_photo(update, context)


async def handle_topup_amount(update, context) -> None:
    raw = update.message.text.strip().replace(config.CURRENCY_SYMBOL, "")
    try:
        amount = float(raw)
    except ValueError:
        await update.message.reply_text("Please send just a number, e.g. 50")
        return
    if amount < config.TOPUP_MIN:
        await update.message.reply_text(
            f"⚠️ Minimum top-up is {config.CURRENCY_SYMBOL}{config.TOPUP_MIN}. "
            "Please enter a higher amount."
        )
        return
    if amount > 100000:
        await update.message.reply_text("Please enter an amount under 100,000.")
        return
    context.user_data["awaiting"]     = None
    context.user_data["topup_amount"] = amount
    await update.message.reply_text(
        f"Amount: <b>{config.CURRENCY_SYMBOL}{amount:.2f}</b>\n"
        "Choose which coin you will pay with:",
        reply_markup=coin_menu(),
        parse_mode="HTML",
    )


async def handle_bin_search(update, context) -> None:
    cat_id     = context.user_data.get("bin_cat", "ff")
    bin_digits = "".join(ch for ch in update.message.text if ch.isdigit())
    if len(bin_digits) < 6:
        await update.message.reply_text("Please send at least 6 digits, e.g. 414720")
        return
    bin_digits = bin_digits[:6]
    context.user_data["awaiting"] = None

    cat = find_category(cat_id)
    matches = []
    for subl in (cat.get("sublists", []) if cat else []):
        for it in await db.get_stock(subl["id"]):
            if it["bin"] == bin_digits:
                matches.append((subl["id"], it))

    await channel_log.bin_search(update.effective_user.id, bin_digits, len(matches))

    if not matches:
        await update.message.reply_text(
            f"❌ <b>No Stock Found</b>\n\n"
            f"BIN <code>{bin_digits}</code> is not available in any list right now.\n\n"
            "Try a different BIN or check back later.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("\U0001F504 Search Another BIN",
                                      callback_data=f"binsearch:{cat_id}")],
                [InlineKeyboardButton("\u2B05\uFE0F Back",
                                      callback_data=f"cat:{cat_id}")],
                [InlineKeyboardButton("\U0001F30F Main Menu", callback_data="menu")],
            ]),
            parse_mode="HTML",
        )
        return

    rows = [[InlineKeyboardButton(
        format_line(it), callback_data=f"sel:{sid}:{it['id']}:0")]
        for sid, it in matches]
    rows.append([InlineKeyboardButton("\u2B05\uFE0F Back",    callback_data=f"cat:{cat_id}")])
    rows.append([InlineKeyboardButton("\U0001F30F Main Menu", callback_data="menu")])
    await update.message.reply_text(
        f"\U0001F50D Found {len(matches)} match(es) for BIN <code>{bin_digits}</code>:",
        reply_markup=InlineKeyboardMarkup(rows),
        parse_mode="HTML",
    )


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.error("Unhandled exception:", exc_info=context.error)
    try:
        if update and hasattr(update, "callback_query") and update.callback_query:
            await update.callback_query.answer("⚠️ Something went wrong. Please try again.")
        elif update and hasattr(update, "message") and update.message:
            await update.message.reply_text("⚠️ Something went wrong. Please try again.")
    except Exception:
        pass


async def admin_relay(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Ghost sender — admin sends any message/file to the bot,
    bot copies it back from itself. Admin then forwards the bot's
    copy to their channel, showing 'Forwarded from [Bot Name]'.
    Only fires when admin is NOT in the middle of a command flow.
    """
    if not update.message:
        return
    uid = update.effective_user.id
    if not admin.is_admin(uid, context.user_data):
        return
    # Don't intercept if admin is mid-flow (uploading, typing, etc.)
    if context.user_data.get("adm_awaiting") or context.user_data.get("adm_awaiting_pw"):
        return
    try:
        await context.bot.copy_message(
            chat_id=uid,
            from_chat_id=uid,
            message_id=update.message.message_id,
        )
    except Exception as e:
        logger.warning("Relay failed: %s", e)
    """Log all unhandled exceptions so nothing fails silently."""
    logger.error("Unhandled exception:", exc_info=context.error)
    # Try to notify the user something went wrong
    try:
        if update and hasattr(update, "callback_query") and update.callback_query:
            await update.callback_query.answer("⚠️ Something went wrong. Please try again.")
        elif update and hasattr(update, "message") and update.message:
            await update.message.reply_text("⚠️ Something went wrong. Please try again.")
    except Exception:
        pass


# ============================================================
#  Button router
# ============================================================
async def on_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    data  = query.data
    uid   = query.from_user.id

    try:
        await _route_button(query, data, uid, context)
    except Exception as e:
        logger.error("on_button error for data=%s: %s", data, e, exc_info=True)
        try:
            await query.answer("⚠️ Something went wrong. Please try again.", show_alert=True)
        except Exception:
            pass


async def _route_button(query, data: str, uid: int,
                        context: ContextTypes.DEFAULT_TYPE) -> None:

    if data == "menu":
        await channel_log.nav_event(uid, "Main Menu")
        try:
            await query.edit_message_text(
                welcome_text(), reply_markup=main_menu(), parse_mode="HTML"
            )
        except Exception:
            # Edit failed (e.g. message too old or invalid keyboard) — send fresh
            await query.message.reply_text(
                welcome_text(), reply_markup=main_menu(), parse_mode="HTML"
            )

    elif data == "rules":
        await safe_edit(query, db.get_label("rules_text", config.RULES_TEXT), back_menu())

    elif data == "store":
        await channel_log.nav_event(uid, "Store")
        await safe_edit(
            query,
            f"<code>──────────────────────</code>\n{_txt('store_cat_text', 'Choose a category:')}",
            store_menu(),
        )

    elif data.startswith("cat:"):
        cat_id = data.split(":", 1)[1]
        cat    = find_category(cat_id)
        if not cat:
            await safe_edit(query, "Category not found.", store_menu())
            return
        cat_label = db.get_label(f"cat:{cat_id}", cat["label"])
        await channel_log.nav_event(uid, "Category", cat_label)
        if not cat.get("sublists"):
            await safe_edit(query, f"No lists yet.",
                            sublist_back_menu(cat_id))
            return
        await safe_edit(
            query,
            f"<code>──────────────────────</code>\n{_txt('store_subl_text', 'Select a list:')}",
            sublist_menu(cat),
        )

    elif data.startswith("subl:"):
        _, cat_id, subl_id = data.split(":", 2)
        cat  = find_category(cat_id)
        subl = find_sublist(cat, subl_id)
        if not subl:
            await safe_edit(query, "List not found.", store_menu())
            return
        # Locked base — show locked message to user
        if db.is_sublist_locked(subl_id):
            subl_label = db.get_label(f"subl:{subl_id}", subl["label"])
            await safe_edit(
                query,
                f"Database Locked 🔒",
                InlineKeyboardMarkup([
                    [InlineKeyboardButton("◄ Previous Menu", callback_data=f"cat:{cat_id}")],
                    [InlineKeyboardButton("🌏 Main Menu",    callback_data="menu")],
                ]),
            )
            return
        subl_label = db.get_label(f"subl:{subl_id}", subl["label"])
        await channel_log.nav_event(uid, "Sublist", subl_label)
        items = await db.get_stock(subl_id)
        cart  = context.user_data.get("cart", {})
        context.user_data["nav"] = {"cat_id": cat_id, "subl_id": subl_id, "page": 0}
        if not items:
            await safe_edit(query,
                f"<code>──────────────────────</code>\nNo lines in stock right now.",
                sublist_back_menu(cat_id))
            return
        await safe_edit(query,
            f"<code>──────────────────────</code>\n{_txt("store_items_text", "Tap items to add to cart:")}",
            lines_menu(cat_id, subl_id, items, page=0, cart=cart))

    elif data.startswith("page:"):
        _, cat_id, subl_id, page_s = data.split(":", 3)
        page  = int(page_s) if page_s.isdigit() else 0
        cat   = find_category(cat_id)
        subl  = find_sublist(cat, subl_id)
        if not subl:
            await safe_edit(query, "List not found.", store_menu())
            return
        subl_label = db.get_label(f"subl:{subl_id}", subl["label"])
        items = await db.get_stock(subl_id)
        cart  = context.user_data.get("cart", {})
        context.user_data["nav"] = {"cat_id": cat_id, "subl_id": subl_id, "page": page}
        await safe_edit(query,
            f"<code>──────────────────────</code>\n{_txt("store_items_text", "Tap items to add to cart:")}",
            lines_menu(cat_id, subl_id, items, page=page, cart=cart))

    elif data.startswith("refresh:"):
        _, cat_id, subl_id = data.split(":", 2)
        context.user_data["nav"] = {"cat_id": cat_id, "subl_id": subl_id, "page": 0}
        cart  = context.user_data.get("cart", {})   # cart is KEPT
        cat   = find_category(cat_id)
        subl  = find_sublist(cat, subl_id)
        subl_label = db.get_label(f"subl:{subl_id}", subl["label"] if subl else subl_id)
        items = await db.get_stock(subl_id)
        banner = "✅ <b>LIST UPDATED</b> ✅\n\n"   # always shown
        count  = len(cart)
        note   = f" · 🛒 <b>{count}</b> in cart" if count else ""
        if not items:
            await safe_edit(query,
                f"{banner}<code>──────────────────────</code>\nNo lines in stock right now.",
                sublist_back_menu(cat_id))
            return
        await safe_edit(query,
            f"{banner}<code>──────────────────────</code>\n{_txt("store_select_text", "Tap to select/deselect")}{note}:",
            lines_menu(cat_id, subl_id, items, page=0, cart=cart))

    elif data.startswith("sel:"):
        parts   = data.split(":", 3)
        subl_id = parts[1]
        item_id = parts[2]
        page    = int(parts[3]) if len(parts) > 3 and parts[3].isdigit() else 0
        cart    = context.user_data.setdefault("cart", {})
        parent_cat = next((c for c in config.CATEGORIES
            if any(s["id"] == subl_id for s in c.get("sublists", []))), None)
        cat_id = parent_cat["id"] if parent_cat else "ff"
        if item_id in cart:
            del cart[item_id]
        else:
            item = await db.get_stock_item(item_id)
            if not item or item.get("sold"):
                await query.answer("⚠️ This item is no longer available.", show_alert=True)
                items = await db.get_stock(subl_id)
                subl  = find_sublist(find_category(cat_id), subl_id)
                subl_label = db.get_label(f"subl:{subl_id}", subl["label"] if subl else subl_id)
                await safe_edit(query,
                    f"<code>──────────────────────</code>\n{_txt("store_items_text", "Tap items to add to cart:")}",
                    lines_menu(cat_id, subl_id, items, page=page, cart=cart))
                return
            cart[item_id] = subl_id
        context.user_data["nav"] = {"cat_id": cat_id, "subl_id": subl_id, "page": page}
        items = await db.get_stock(subl_id)
        subl  = find_sublist(find_category(cat_id), subl_id)
        subl_label = db.get_label(f"subl:{subl_id}", subl["label"] if subl else subl_id)
        count = len(cart)
        note  = f" · 🛒 <b>{count}</b> in cart" if count else ""
        await safe_edit(query,
            f"<code>──────────────────────</code>\n{_txt("store_select_text", "Tap to select/deselect")}{note}:",
            lines_menu(cat_id, subl_id, items, page=page, cart=cart))

    elif data.startswith("cart_checkout:"):
        _, cat_id, subl_id, page_s = data.split(":", 3)
        cart = context.user_data.get("cart", {})
        if not cart:
            await query.answer("No items selected!", show_alert=True)
            return
        selected, gone = [], 0
        for iid in list(cart.keys()):
            item = await db.get_stock_item(iid)
            if item and not item.get("sold"):
                selected.append(item)
            else:
                del context.user_data["cart"][iid]; gone += 1
        if not selected:
            await query.answer("All selected items are now sold out!", show_alert=True)
            items = await db.get_stock(subl_id)
            subl  = find_sublist(find_category(cat_id), subl_id)
            subl_label = db.get_label(f"subl:{subl_id}", subl["label"] if subl else subl_id)
            await safe_edit(query,
                f"❌ All items sold\n<code>──────────────────────</code>\n{_txt('store_items_text', 'Tap items to add to cart:')}",
                lines_menu(cat_id, subl_id, items, page=int(page_s), cart={}))
            return
        total = sum(Decimal(str(it["price"])) for it in selected)
        bal   = await db.get_balance(uid)
        summary = (
            "🛒 <b>Order Summary</b>\n"
            "<code>──────────────────────</code>\n"
            + "\n".join(f"• {format_line(it)}" for it in selected) + "\n"
            "<code>──────────────────────</code>\n"
            f"Items:    <b>{len(selected)}</b>\n"
            f"Total:    <b>{config.CURRENCY_SYMBOL}{float(total):g}</b>\n"
            f"Balance:  {config.CURRENCY_SYMBOL}{bal:.2f}"
            + (f"\n⚠️ {gone} item(s) sold and removed." if gone else "")
        )
        if bal < total:
            shortfall = total - bal
            await safe_edit(query, summary + f"\n\n❌ Need <b>{config.CURRENCY_SYMBOL}{float(shortfall):g}</b> more.",
                InlineKeyboardMarkup([
                    [InlineKeyboardButton("➕ Top Up Now", callback_data="topup")],
                    [InlineKeyboardButton("⬅️ Back", callback_data=f"subl:{cat_id}:{subl_id}")],
                    [InlineKeyboardButton("🌏 Main Menu", callback_data="menu")],
                ]))
            return
        await safe_edit(query, summary + "\n\n✅ Balance sufficient — confirm?",
            InlineKeyboardMarkup([
                [InlineKeyboardButton(f"✅ Confirm — {config.CURRENCY_SYMBOL}{float(total):g}",
                    callback_data=f"cart_confirm:{cat_id}:{subl_id}:{page_s}")],
                [InlineKeyboardButton("❌ Cancel", callback_data=f"subl:{cat_id}:{subl_id}")],
                [InlineKeyboardButton("🌏 Main Menu", callback_data="menu")],
            ]))

    elif data.startswith("cart_confirm:"):
        _, cat_id, subl_id, _ = data.split(":", 3)
        cart = context.user_data.get("cart", {})
        if not cart:
            await safe_edit(query, "Cart is empty.", main_menu()); return
        purchased, failed, total_spent = [], [], Decimal("0")
        for iid in list(cart.keys()):
            result = await db.purchase_item(uid, iid)
            if result["status"] == "success":
                purchased.append(result); total_spent += result["price"]
            else:
                failed.append(result["status"])
        context.user_data["cart"] = {}
        new_bal = await db.get_balance(uid)
        if not purchased:
            await safe_edit(query,
                "❌ <b>Purchase Failed</b>\n\nAll items became unavailable. Please try again.",
                InlineKeyboardMarkup([
                    [InlineKeyboardButton("🛒 Back to Store", callback_data="store")],
                    [InlineKeyboardButton("🌏 Main Menu", callback_data="menu")],
                ]))
            return
        delivery = [
            "✅ <b>Purchase Complete!</b>",
            "<code>──────────────────────</code>",
            f"Bought:      <b>{len(purchased)}</b> item(s)",
            f"Spent:       <b>{config.CURRENCY_SYMBOL}{float(total_spent):g}</b>",
            f"New balance: {config.CURRENCY_SYMBOL}{new_bal:.2f}",
        ]
        if failed:
            delivery.append(f"⚠️ {len(failed)} item(s) unavailable (skipped).")
        delivery += ["", "<b>📦 Your Items:</b>", "<code>──────────────────────</code>"]
        for r in purchased:
            delivery.append(f"<code>{r['content']}</code>")
        delivery += ["<code>──────────────────────</code>", "<i>Screenshot or copy now.</i>"]
        await safe_edit(query, "\n".join(delivery),
            InlineKeyboardMarkup([
                [InlineKeyboardButton("🛒 Buy More", callback_data=f"subl:{cat_id}:{subl_id}")],
                [InlineKeyboardButton("🌏 Main Menu", callback_data="menu")],
            ]))
        await channel_log.log(
            f"🛒 <b>Cart Purchase</b>\n"
            f"User: <code>{uid}</code>\n"
            f"Items: {len(purchased)}  ·  Total: {config.CURRENCY_SYMBOL}{float(total_spent):g}\n"
            f"Balance: {config.CURRENCY_SYMBOL}{new_bal:.2f}"
        )

    elif data.startswith("binsearch:"):
        cat_id = data.split(":", 1)[1]
        context.user_data["awaiting"] = "bin_search"
        context.user_data["bin_cat"]  = cat_id
        await channel_log.nav_event(uid, "BIN Search Started")
        await safe_edit(
            query,
            "\U0001F50D <b>Search for BIN</b>\n\n"
            "Send the first 6 digits of a card (the BIN), e.g. <code>414720</code>.",
            sublist_back_menu(cat_id),
        )

    elif data == "wallet":
        await db.ensure_user(uid, query.from_user.username or "")
        text    = await _wallet_text(uid)
        bal     = await db.get_balance(uid)
        bal_str = f"{config.CURRENCY_SYMBOL}{bal:.2f}"
        await safe_edit(query, text, wallet_menu(bal_str))

    elif data == "topup":
        context.user_data["awaiting"] = None
        await channel_log.nav_event(uid, "Top-Up Menu")
        if not payments.active_coins():
            await safe_edit(query,
                "⚠️ No wallet addresses configured yet.\n"
                f"Contact {config.SUPPORT_HANDLE} to top up.", back_menu())
            return
        await safe_edit(query, "How much would you like to add?", amount_menu())

    elif data == "topup_custom":
        context.user_data["awaiting"] = "topup_amount"
        await channel_log.nav_event(uid, "Custom Amount")
        await safe_edit(
            query,
            f"\U0001F4B0 <b>Custom Amount</b>\n\n"
            f"Minimum top-up is <b>{config.CURRENCY_SYMBOL}{config.TOPUP_MIN}</b>.\n\n"
            "Type the amount you want to add and send it:",
            InlineKeyboardMarkup([[
                InlineKeyboardButton("\u2B05\uFE0F Back", callback_data="topup")
            ]]),
        )

    elif data.startswith("topup_amt:"):
        amt_val = data.split(":", 1)[1]
        context.user_data["topup_amount"] = float(amt_val)
        await channel_log.nav_event(uid, "Amount Selected", f"{config.CURRENCY_SYMBOL}{amt_val}")
        await safe_edit(query, "Choose which coin you will pay with:", coin_menu())

    elif data.startswith("topup_coin:"):
        coin   = data.split(":", 1)[1]
        amount = context.user_data.get("topup_amount")
        if not amount:
            await safe_edit(query, "Please pick an amount first.", amount_menu())
            return
        await channel_log.nav_event(uid, "Coin Selected", f"{coin} for {config.CURRENCY_SYMBOL}{amount:g}")
        await show_payment_address(query, context, uid, amount, coin)

    elif data.startswith("pay_sent:"):
        payment_id = data.split(":", 1)[1]
        pay = await db.get_payment(payment_id)
        if not pay or pay["status"] not in ("pending",):
            await safe_edit(query,
                "This payment has already been submitted or no longer exists.",
                back_menu())
            return
        context.user_data["awaiting"]    = "proof"
        context.user_data["proof_payid"] = payment_id
        await safe_edit(
            query,
            "\U0001F4CB <b>Submit Proof</b>\n\n"
            "Please send your <b>Transaction ID</b> (text) "
            "<b>or a screenshot</b> of the payment.\n\n"
            "This goes straight to our team for review.",
            InlineKeyboardMarkup([[
                InlineKeyboardButton("\u274C Cancel", callback_data="wallet")
            ]]),
        )


# ============================================================
#  Manual payment helpers
# ============================================================
async def show_payment_address(query, context, user_id, amount, coin) -> None:
    """Show the wallet address and create a pending payment record."""
    coins = payments.active_coins()
    if coin not in coins:
        await safe_edit(query, "⚠️ That coin is no longer available.", coin_menu())
        return

    address    = coins[coin]
    payment_id = payments.new_payment_id()
    await db.ensure_user(user_id, getattr(getattr(query, 'from_user', None), 'username', '') or "")
    await db.record_payment(payment_id, user_id, Decimal(str(amount)), coin)
    context.user_data["topup_amount"] = None
    await channel_log.topup_started(user_id, amount, coin)

    # Fetch live exchange rate
    rates    = await payments.get_rates_gbp()
    rate     = rates.get(coin, 0)
    rate_str = payments.format_crypto_amount(amount, rate, coin) if rate else ""
    crypto_line = f"\nSend approx:  <b>{rate_str}</b>" if rate_str else \
                  "\n<i>(Check the current rate before sending)</i>"

    await safe_edit(
        query,
        f"\U0001F4B3 <b>Top-Up Instructions</b>\n\n"
        f"Amount:  <b>{config.CURRENCY_SYMBOL}{amount:g}</b>{crypto_line}\n"
        f"Coin:    <b>{coin}</b>\n\n"
        f"Send to this address:\n"
        f"<code>{address}</code>\n\n"
        f"{config.PAYMENT_NOTE}\n\n"
        "Once sent, tap the button below.",
        sent_menu(payment_id),
    )


async def handle_proof_text(update, context) -> None:
    """User sends a TX ID as text proof."""
    payment_id = context.user_data.pop("proof_payid", None)
    context.user_data["awaiting"] = None
    if not payment_id:
        return

    tx_ref = update.message.text.strip()
    updated = await db.submit_proof(payment_id, f"txid:{tx_ref}")
    if not updated:
        await update.message.reply_text(
            "⚠️ This payment was already submitted or cancelled."
        )
        return

    await update.message.reply_text(
        f"\u23F3 <b>Submitted!</b>\n\n"
        f"TX ID: <code>{tx_ref}</code>\n\n"
        f"Our team will review and credit your balance {config.REVIEW_TIME}.\n"
        "You'll receive a message here when it's approved.",
        parse_mode="HTML",
    )
    pay = await db.get_payment(payment_id)
    if pay:
        await channel_log.proof_submitted(
            update.effective_user.id, float(pay["amount"]),
            pay["coin"], f"txid:{tx_ref}", payment_id,
        )
    await _notify_admins_new_payment(context.bot, pay)


async def handle_proof_photo(update, context) -> None:
    """User sends a screenshot as photo proof."""
    payment_id = context.user_data.get("proof_payid")
    if not payment_id:
        return  # not in a proof-submission flow
    context.user_data["awaiting"]    = None
    context.user_data["proof_payid"] = None

    # Store the largest photo's file_id
    photo  = update.message.photo[-1]
    tx_ref = f"photo:{photo.file_id}"
    updated = await db.submit_proof(payment_id, tx_ref)
    if not updated:
        await update.message.reply_text(
            "⚠️ This payment was already submitted or cancelled."
        )
        return

    await update.message.reply_text(
        f"\u23F3 <b>Screenshot received!</b>\n\n"
        f"Our team will review and credit your balance {config.REVIEW_TIME}.\n"
        "You'll receive a message here when it's approved.",
        parse_mode="HTML",
    )
    pay = await db.get_payment(payment_id)
    if pay:
        await channel_log.proof_submitted(
            update.effective_user.id, float(pay["amount"]),
            pay["coin"], tx_ref, payment_id,
        )
    await _notify_admins_new_payment(context.bot, pay)


async def _notify_admins_new_payment(bot, pay: dict) -> None:
    """Send a notification to every admin when a user submits payment proof."""
    if not pay:
        return
    tx_ref   = pay.get("tx_ref", "")
    is_photo = tx_ref.startswith("photo:")
    clean_ref = "📷 Screenshot (see below)" if is_photo else tx_ref.replace("txid:", "")
    ref_display = f"<code>{clean_ref}</code>"

    text = (
        f"\U0001F4B3 <b>New Payment Submitted</b>\n\n"
        f"User:    <code>{pay['user_id']}</code>\n"
        f"Amount:  <b>{config.CURRENCY_SYMBOL}{pay['amount']:.2f}</b>\n"
        f"Coin:    {pay['coin']}\n"
        f"Ref:     {ref_display}\n"
        f"ID:      <code>{pay['payment_id']}</code>"
    )
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Approve", callback_data=f"adm_pay_approve:{pay['payment_id']}"),
        InlineKeyboardButton("❌ Reject",  callback_data=f"adm_pay_reject:{pay['payment_id']}"),
    ]])

    for admin_id in config.ADMIN_IDS:
        try:
            await bot.send_message(admin_id, text, reply_markup=kb, parse_mode="HTML")
            if is_photo:
                file_id = tx_ref.replace("photo:", "")
                await bot.send_photo(admin_id, file_id,
                                     caption=f"Screenshot for payment {pay['payment_id']}")
        except Exception:
            logger.warning("Could not notify admin %s", admin_id)


# ============================================================
#  Startup + main
# ============================================================
async def on_startup(app: Application) -> None:
    await db.init()
    channel_log.init(app.bot)
    await app.bot.set_my_commands([
        BotCommand("start",   "🏠 Main menu"),
        BotCommand("store",   "🛒 Browse the store"),
        BotCommand("wallet",  "💵 View wallet & top up"),
        BotCommand("rules",   "🛡️ Read the rules"),
        BotCommand("support", "☎️ Contact support"),
    ])
    logger.info("Startup complete. Bot is running.")


def main() -> None:
    missing = [n for n in ("BOT_TOKEN", "DATABASE_URL")
               if not getattr(config, n)]
    if missing:
        raise RuntimeError("Missing Variables in Railway: " + ", ".join(missing))

    app = (
        Application.builder()
        .token(config.BOT_TOKEN)
        .post_init(on_startup)
        .build()
    )

    admin.register_admin_handlers(app)

    app.add_error_handler(error_handler)
    app.add_handler(CommandHandler("start",   start))
    app.add_handler(CommandHandler("myid",    cmd_myid))
    app.add_handler(CommandHandler("store",   cmd_store))
    app.add_handler(CommandHandler("wallet",  cmd_wallet))
    app.add_handler(CommandHandler("rules",   cmd_rules))
    app.add_handler(CommandHandler("support", cmd_support))
    app.add_handler(CallbackQueryHandler(on_button))
    app.add_handler(MessageHandler(filters.PHOTO & filters.UpdateType.MESSAGE, on_photo))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
    # Admin ghost sender — relay any media the admin sends
    app.add_handler(MessageHandler(
        (filters.PHOTO | filters.Document.ALL | filters.VIDEO |
         filters.AUDIO | filters.VOICE | filters.Sticker.ALL) & ~filters.COMMAND,
        admin_relay,
    ))

    logger.info("Bot is starting...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
