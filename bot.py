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
try:
    import channel_log
except ImportError:
    channel_log = None  # log channel not set up yet — safe to continue
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
def Welcome_text() -> str:
    return (
        "Welcome to EXCEL Store 👋\n"
        "Use the menu below to interact with the bot 🤖\n"
        "===============\n"
        "Managed by @EXCELV1\n"
        "Coded by @EXCELV1 on session 05a5c62989edb4dadf7cb1274e35e37d498b5af459b04e08fe08ab037a206ec841"
    )


def _tg_url(https_url: str) -> str:
    """
    Convert https://t.me/username  →  tg://resolve?domain=username
    This opens the profile directly with one tap, no preview dialog.
    Falls back to the original URL if it doesn't match the expected format.
    """
    import re
    match = re.match(r"https?://t\.me/([A-Za-z0-9_]+)", https_url.strip())
    if match:
        return f"tg://resolve?domain={match.group(1)}"
    return https_url


def main_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(db.get_label("menu:store",   "\U0001F6D2 Store"),
                                 callback_data="store"),
            InlineKeyboardButton(db.get_label("menu:wallet",  "\U0001F4B5 Wallet"),
                                 callback_data="wallet"),
        ],
        [InlineKeyboardButton(db.get_label("menu:rules",   "\U0001F6E1\uFE0F Rules"),
                              callback_data="rules")],
        [
            InlineKeyboardButton(db.get_label("menu:support", "\u260E\uFE0F Support \u2197"),
                                 url=_tg_url(config.SUPPORT_URL)),
            InlineKeyboardButton(db.get_label("menu:channel", "\U0001F4C4 Channel \u2197"),
                                 url=_tg_url(config.CHANNEL_URL)),
        ],
    ])


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


def find_sublist(cat, subl_id):
    if not cat:
        return None
    return next((s for s in cat.get("sublists", []) if s["id"] == subl_id), None)


def sublist_menu(cat) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(
                db.get_label(f"subl:{s['id']}", s["label"]),
                callback_data=f"subl:{cat['id']}:{s['id']}")]
            for s in cat.get("sublists", [])]
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


def lines_menu(cat_id, subl_id, items, page=0) -> InlineKeyboardMarkup:
    """All buttons are full-width single rows — no side-by-side buttons."""
    per         = config.ITEMS_PER_PAGE
    total_pages = max(1, (len(items) + per - 1) // per)
    page        = max(0, min(page, total_pages - 1))
    page_items  = items[page * per : page * per + per]

    rows = [[InlineKeyboardButton(
        format_line(it), callback_data=f"line:{subl_id}:{it['id']}")]
        for it in page_items]

    # Navigation — every button is its own full-width row
    rows.append([InlineKeyboardButton(
        "\U0001F504 Refresh",
        callback_data=f"page:{cat_id}:{subl_id}:{page}",
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
            raise


# ============================================================
#  /start and user commands
# ============================================================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    uid      = update.effective_user.id
    username = update.effective_user.username or ""
    existing = await db.get_user_info(uid)
    is_new   = existing is None
    await db.ensure_user(uid, username)
    channel_log and await channel_log.user_start(uid, username, is_new)
    if await db.is_banned(uid):
        await update.message.reply_text(
            "\U0001F6AB You have been banned from this store.\n"
            f"Contact {config.SUPPORT_HANDLE} if you think this is a mistake.",
            reply_markup=ReplyKeyboardRemove(),
        )
        return
    # 1 — Refund policy first (clears old keyboard too)
    await update.message.reply_text(
        config.REFUND_RULES_TEXT,
        reply_markup=ReplyKeyboardRemove(),
        parse_mode="HTML",
    )
    # 2 — Welcome banner
    await update.message.reply_text(
        welcome_text(),
        parse_mode="HTML",
    )
    # 3 — Menu buttons
    await update.message.reply_text(
        "Use the buttons below 👇",
        reply_markup=main_menu(),
    )


async def cmd_store(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    u = update.effective_user
    await db.ensure_user(u.id, u.username or "")
    await update.message.reply_text(
        "\U0001F6D2 <b>Store</b>\n\nChoose a category:",
        reply_markup=store_menu(),
        parse_mode="HTML",
    )


async def cmd_wallet(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    u   = update.effective_user
    uid = u.id
    await db.ensure_user(uid, u.username or "")
    bal = await db.get_balance(uid)
    bal_str = f"{config.CURRENCY_SYMBOL}{bal:.2f}"
    await update.message.reply_text(
        f"\U0001F4B5 <b>Wallet</b>\n\nYour balance: <b>{bal_str}</b>",
        reply_markup=wallet_menu(bal_str),
        parse_mode="HTML",
    )


async def cmd_rules(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        config.RULES_TEXT,
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

    # Handle reply-keyboard shortcuts — strip emojis and match loosely
    import re
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

    channel_log and await channel_log.bin_search(update.effective_user.id, bin_digits, len(matches))

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
        format_line(it), callback_data=f"line:{sid}:{it['id']}")]
        for sid, it in matches]
    rows.append([InlineKeyboardButton("\u2B05\uFE0F Back",    callback_data=f"cat:{cat_id}")])
    rows.append([InlineKeyboardButton("\U0001F30F Main Menu", callback_data="menu")])
    await update.message.reply_text(
        f"\U0001F50D Found {len(matches)} match(es) for BIN <code>{bin_digits}</code>:",
        reply_markup=InlineKeyboardMarkup(rows),
        parse_mode="HTML",
    )


# ============================================================
#  Button router
# ============================================================
async def on_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    data  = query.data
    uid   = query.from_user.id

    if data == "menu":
        channel_log and await channel_log.nav_event(uid, "Main Menu")
        await safe_edit(query, welcome_text(), main_menu())

    elif data == "rules":
        channel_log and await channel_log.nav_event(uid, "Rules")
        await safe_edit(query, config.RULES_TEXT, back_menu())

    elif data == "store":
        channel_log and await channel_log.nav_event(uid, "Store")
        await safe_edit(query, "\U0001F6D2 <b>Store</b>\n\nChoose a category:", store_menu())

    elif data.startswith("cat:"):
        cat_id = data.split(":", 1)[1]
        cat    = find_category(cat_id)
        if not cat:
            await safe_edit(query, "Category not found.", store_menu())
            return
        channel_log and await channel_log.nav_event(uid, "Category", db.get_label(f"cat:{cat_id}", cat["label"] if cat else cat_id))
        if not cat.get("sublists"):
            await safe_edit(query, f"{cat['label']}\n\nNo lists yet.",
                            sublist_back_menu(cat_id))
            return
        await safe_edit(query, f"{cat['label']}\n\nSelect a list:", sublist_menu(cat))

    elif data.startswith("subl:"):
        _, cat_id, subl_id = data.split(":", 2)
        cat  = find_category(cat_id)
        subl = find_sublist(cat, subl_id)
        if not subl:
            await safe_edit(query, "List not found.", store_menu())
            return
        subl_label = db.get_label(f"subl:{subl_id}", subl["label"])
        channel_log and await channel_log.nav_event(uid, "Sublist", subl_label)
        items = await db.get_stock(subl_id)
        if not items:
            await safe_edit(query, f"{subl['label']}\n\nNo lines in stock right now.",
                            sublist_back_menu(cat_id))
            return
        await safe_edit(query, f"{subl['label']}\n\nTap a line to view it:",
                        lines_menu(cat_id, subl_id, items, page=0))

    elif data.startswith("page:"):
        _, cat_id, subl_id, page_s = data.split(":", 3)
        page  = int(page_s) if page_s.isdigit() else 0
        cat   = find_category(cat_id)
        subl  = find_sublist(cat, subl_id)
        if not subl:
            await safe_edit(query, "List not found.", store_menu())
            return
        items = await db.get_stock(subl_id)
        await safe_edit(query, f"{subl['label']}\n\nTap a line to view it:",
                        lines_menu(cat_id, subl_id, items, page=page))

    elif data.startswith("line:"):
        _, subl_id, line_id = data.split(":", 2)
        item = await db.get_stock_item(line_id)
        parent_cat = next(
            (c for c in config.CATEGORIES
             if any(s["id"] == subl_id for s in c.get("sublists", []))),
            None,
        )
        cat_id = parent_cat["id"] if parent_cat else "ff"

        if not item or item.get("sold"):
            await safe_edit(
                query,
                "❌ <b>No Longer Available</b>\n\n"
                "This item has already been sold.\n"
                "Tap below to go back and browse other listings.",
                InlineKeyboardMarkup([
                    [InlineKeyboardButton("\u2B05\uFE0F Back to List",
                                         callback_data=f"subl:{cat_id}:{subl_id}")],
                    [InlineKeyboardButton("\U0001F30F Main Menu", callback_data="menu")],
                ]),
            )
            return

        bal       = await db.get_balance(uid)
        price_str = f"{float(item['price']):g}"
        can_buy   = bal >= item["price"]
        shortfall = item["price"] - bal

        buy_row = (
            [InlineKeyboardButton(
                f"\U0001F6D2 Buy — {config.CURRENCY_SYMBOL}{price_str}",
                callback_data=f"buy:{subl_id}:{line_id}",
            )]
            if can_buy else
            [InlineKeyboardButton(
                f"\U0001F4B3 Top Up {config.CURRENCY_SYMBOL}{float(shortfall):g} to Buy",
                callback_data="topup",
            )]
        )
        channel_log and await channel_log.item_viewed(
            uid, item["bin"], item["year"], item["code"],
            float(item["price"]), subl_id,
        )
        await safe_edit(
            query,
            f"<b>{format_line(item)}</b>\n\n"
            f"BIN:          <code>{item['bin']}</code>\n"
            f"Year:         {item['year']}\n"
            f"Code/Region:  {item['code']}\n"
            f"Price:        <b>{config.CURRENCY_SYMBOL}{price_str}</b>\n"
            f"Your balance: {config.CURRENCY_SYMBOL}{bal:.2f}\n\n"
            + ("✅ You have enough balance to buy." if can_buy else
               f"⚠️ You need <b>{config.CURRENCY_SYMBOL}{float(shortfall):g}</b> more to buy this."),
            InlineKeyboardMarkup([
                buy_row,
                [InlineKeyboardButton("\u2B05\uFE0F Back to List",
                                      callback_data=f"subl:{cat_id}:{subl_id}")],
                [InlineKeyboardButton("\U0001F30F Main Menu", callback_data="menu")],
            ]),
        )

    elif data.startswith("buy:"):
        _, subl_id, line_id = data.split(":", 2)
        result = await db.purchase_item(uid, line_id)

        if result["status"] == "not_available":
            parent_cat = next(
                (c for c in config.CATEGORIES
                 if any(s["id"] == subl_id for s in c.get("sublists", []))),
                None,
            )
            cat_id = parent_cat["id"] if parent_cat else "ff"
            await safe_edit(
                query,
                "❌ <b>No Longer Available</b>\n\n"
                "This item was just purchased by someone else.\n"
                "Tap below to browse other listings.",
                InlineKeyboardMarkup([
                    [InlineKeyboardButton("\u2B05\uFE0F Back to List",
                                         callback_data=f"subl:ff:{subl_id}")],
                    [InlineKeyboardButton("\U0001F30F Main Menu", callback_data="menu")],
                ]),
            )

        elif result["status"] == "insufficient":
            shortfall = result["shortfall"]
            await safe_edit(
                query,
                f"⚠️ <b>Insufficient Balance</b>\n\n"
                f"You need <b>{config.CURRENCY_SYMBOL}{float(shortfall):g}</b> more.\n\n"
                "Top up your wallet and come back.",
                InlineKeyboardMarkup([
                    [InlineKeyboardButton("\u2795 Top Up Now", callback_data="topup")],
                    [InlineKeyboardButton("\U0001F30F Main Menu", callback_data="menu")],
                ]),
            )

        else:  # success
            new_bal = result["new_balance"]
            content = result["content"]
            channel_log and await channel_log.purchase_made(
                uid, item["bin"] if item else "?",
                item["year"] if item else "?",
                item["code"] if item else "?",
                float(result["price"]), float(new_bal), subl_id,
            )
            await safe_edit(
                query,
                f"✅ <b>Purchase Successful!</b>\n\n"
                f"Paid: <b>{config.CURRENCY_SYMBOL}{float(result['price']):g}</b>\n"
                f"New balance: {config.CURRENCY_SYMBOL}{new_bal:.2f}\n\n"
                "─────────────────\n"
                f"<code>{content}</code>\n"
                "─────────────────\n\n"
                "Screenshot this or copy it now.",
                InlineKeyboardMarkup([
                    [InlineKeyboardButton("\U0001F6D2 Back to Store", callback_data="store")],
                    [InlineKeyboardButton("\U0001F30F Main Menu",     callback_data="menu")],
                ]),
            )

    elif data.startswith("binsearch:"):
        cat_id = data.split(":", 1)[1]
        context.user_data["awaiting"] = "bin_search"
        context.user_data["bin_cat"]  = cat_id
        channel_log and await channel_log.nav_event(uid, "BIN Search Started")
        await safe_edit(
            query,
            "\U0001F50D <b>Search for BIN</b>\n\n"
            "Send the first 6 digits of a card (the BIN), e.g. <code>414720</code>.",
            sublist_back_menu(cat_id),
        )

    elif data == "wallet":
        await db.ensure_user(uid, query.from_user.username or "")
        bal = await db.get_balance(uid)
        bal_str = f"{config.CURRENCY_SYMBOL}{bal:.2f}"
        channel_log and await channel_log.nav_event(uid, "Wallet", f"balance {bal_str}")
        await safe_edit(
            query,
            f"\U0001F4B5 <b>Wallet</b>\n\nYour balance: <b>{bal_str}</b>",
            wallet_menu(bal_str),
        )

    elif data == "topup":
        context.user_data["awaiting"] = None
        channel_log and await channel_log.nav_event(uid, "Top-Up Menu")
        if not payments.active_coins():
            await safe_edit(query,
                "⚠️ No wallet addresses configured yet.\n"
                f"Contact {config.SUPPORT_HANDLE} to top up.", back_menu())
            return
        await safe_edit(query, "How much would you like to add?", amount_menu())

    elif data == "topup_custom":
        context.user_data["awaiting"] = "topup_amount"
        channel_log and await channel_log.nav_event(uid, "Custom Amount")
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
        channel_log and await channel_log.nav_event(uid, "Amount Selected", f"{config.CURRENCY_SYMBOL}{amt_val}")
        await safe_edit(query, "Choose which coin you will pay with:", coin_menu())

    elif data.startswith("topup_coin:"):
        coin   = data.split(":", 1)[1]
        amount = context.user_data.get("topup_amount")
        if not amount:
            await safe_edit(query, "Please pick an amount first.", amount_menu())
            return
        channel_log and await channel_log.nav_event(uid, "Coin Selected", f"{coin} for {config.CURRENCY_SYMBOL}{amount:g}")
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
    channel_log and await channel_log.topup_started(user_id, amount, coin)

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
        channel_log and await channel_log.proof_submitted(
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
        channel_log and await channel_log.proof_submitted(
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

    app.add_handler(CommandHandler("start",   start))
    app.add_handler(CommandHandler("store",   cmd_store))
    app.add_handler(CommandHandler("wallet",  cmd_wallet))
    app.add_handler(CommandHandler("rules",   cmd_rules))
    app.add_handler(CommandHandler("support", cmd_support))
    app.add_handler(CallbackQueryHandler(on_button))
    app.add_handler(MessageHandler(filters.PHOTO & filters.UpdateType.MESSAGE, on_photo))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))

    logger.info("Bot is starting...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
