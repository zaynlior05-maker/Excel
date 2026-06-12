"""
Telegram Store Bot
  Part 1: Welcome interface & Rules
  Part 2: Wallet with crypto top-ups (USDT / BTC) via NOWPayments
  Part 3: Store navigation with categories, sub-lists, paginated line items
  Part 4: Full admin panel
"""

import logging
from decimal import Decimal

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
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
def welcome_text() -> str:
    return (
        f"\U0001F539 Support account is available 24/7 {config.SUPPORT_HANDLE}\n"
        "\U0001F539\n"
        "\U0001F539 <b>BY PURCHASING YOU AGREE TO THESE RULES. "
        "FAILURE TO READ THEM WILL FORFEIT YOUR REFUND / REPLACEMENT. "
        "WE SHALL GIVE NO WARNINGS</b>\n\n"
        "Welcome to the Store \U0001F44B\n"
        "Use the menu below to interact with the bot \U0001F916"
    )


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
                                 url=config.SUPPORT_URL),
            InlineKeyboardButton(db.get_label("menu:channel", "\U0001F4C4 Channel \u2197"),
                                 url=config.CHANNEL_URL),
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
    """
    One button per line on this page, then nav block:
       🔄 Refresh | Next ➡️
       ⬅️ Previous Menu   (full-width)
       🌏 Main Menu        (full-width)
    """
    per         = config.ITEMS_PER_PAGE
    total_pages = max(1, (len(items) + per - 1) // per)
    page        = max(0, min(page, total_pages - 1))
    page_items  = items[page * per : page * per + per]

    rows = [[InlineKeyboardButton(
        format_line(it), callback_data=f"line:{subl_id}:{it['id']}")]
        for it in page_items]

    nav = [InlineKeyboardButton(
        "\U0001F504 Refresh",
        callback_data=f"page:{cat_id}:{subl_id}:{page}",
    )]
    if page < total_pages - 1:
        nav.append(InlineKeyboardButton(
            "Next \u27A1\uFE0F",
            callback_data=f"page:{cat_id}:{subl_id}:{page + 1}",
        ))
    rows.append(nav)
    # "Previous Menu" back button — show the live sublist name
    subl_default = next(
        (s["label"] for cat in config.CATEGORIES
         for s in cat.get("sublists", []) if s["id"] == subl_id),
        subl_id,
    )
    rows.append([InlineKeyboardButton(
        f"\u2B05\uFE0F {db.get_label(f'subl:{subl_id}', subl_default)}",
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
    rows = [[InlineKeyboardButton(
        f"{config.CURRENCY_SYMBOL}{a}", callback_data=f"topup_amt:{a}")]
        for a in config.TOPUP_PRESETS]
    rows.append([InlineKeyboardButton("\u270F\uFE0F Custom amount", callback_data="topup_custom")])
    rows.append([InlineKeyboardButton("\u2B05\uFE0F Back", callback_data="wallet")])
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
#  /start
# ============================================================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    uid = update.effective_user.id
    await db.ensure_user(uid)
    if await db.is_banned(uid):
        await update.message.reply_text(
            "\U0001F6AB You have been banned from this store.\n"
            f"Contact {config.SUPPORT_HANDLE} if you think this is a mistake."
        )
        return
    await update.message.reply_text(
        welcome_text(), reply_markup=main_menu(), parse_mode="HTML"
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
    elif awaiting == "proof":
        await handle_proof_text(update, context)
    elif awaiting == "bin_search":
        await handle_bin_search(update, context)


async def on_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Screenshot submitted as payment proof."""
    if context.user_data.get("awaiting") == "proof":
        await handle_proof_photo(update, context)


async def handle_topup_amount(update, context) -> None:
    raw = update.message.text.strip().replace(config.CURRENCY_SYMBOL, "")
    try:
        amount = float(raw)
    except ValueError:
        await update.message.reply_text("Please send just a number, e.g. 15")
        return
    if amount < 1 or amount > 100000:
        await update.message.reply_text("Enter an amount between 1 and 100000.")
        return
    context.user_data["awaiting"]     = None
    context.user_data["topup_amount"] = amount
    await update.message.reply_text(
        f"Amount: {config.CURRENCY_SYMBOL}{amount:.2f}\nChoose which coin to pay with:",
        reply_markup=coin_menu(),
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

    if not matches:
        await update.message.reply_text(
            f"\U0001F50D No stock matching BIN <code>{bin_digits}</code> right now.",
            reply_markup=sublist_back_menu(cat_id),
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
        await safe_edit(query, welcome_text(), main_menu())

    elif data == "rules":
        await safe_edit(query, config.RULES_TEXT, back_menu())

    elif data == "store":
        await safe_edit(query, "\U0001F6D2 <b>Store</b>\n\nChoose a category:", store_menu())

    elif data.startswith("cat:"):
        cat_id = data.split(":", 1)[1]
        cat    = find_category(cat_id)
        if not cat:
            await safe_edit(query, "Category not found.", store_menu())
            return
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
        if not item or item.get("sold"):
            await safe_edit(query, "This item is no longer available.", store_menu())
            return
        parent_cat = next(
            (c for c in config.CATEGORIES
             if any(s["id"] == subl_id for s in c.get("sublists", []))),
            None,
        )
        cat_id    = parent_cat["id"] if parent_cat else "ff"
        price_str = f"{float(item['price']):g}"
        await safe_edit(
            query,
            f"<b>{format_line(item)}</b>\n\n"
            f"BIN: <code>{item['bin']}</code>\n"
            f"Year: {item['year']}\n"
            f"Code/Region: {item['code']}\n"
            f"Price: {config.CURRENCY_SYMBOL}{price_str}\n\n"
            "Purchasing isn\u2019t wired up yet \u2014 that comes next.",
            InlineKeyboardMarkup([
                [InlineKeyboardButton(
                    "\u2B05\uFE0F Back", callback_data=f"subl:{cat_id}:{subl_id}")],
                [InlineKeyboardButton("\U0001F30F Main Menu", callback_data="menu")],
            ]),
        )

    elif data.startswith("binsearch:"):
        cat_id = data.split(":", 1)[1]
        context.user_data["awaiting"] = "bin_search"
        context.user_data["bin_cat"]  = cat_id
        await safe_edit(
            query,
            "\U0001F50D <b>Search for BIN</b>\n\n"
            "Send the first 6 digits of a card (the BIN), e.g. <code>414720</code>.",
            sublist_back_menu(cat_id),
        )

    elif data == "wallet":
        await db.ensure_user(uid)
        bal = await db.get_balance(uid)
        bal_str = f"{config.CURRENCY_SYMBOL}{bal:.2f}"
        await safe_edit(
            query,
            f"\U0001F4B5 <b>Wallet</b>\n\nYour balance: <b>{bal_str}</b>",
            wallet_menu(bal_str),
        )

    elif data == "topup":
        context.user_data["awaiting"] = None
        if not payments.active_coins():
            await safe_edit(query,
                "⚠️ No wallet addresses configured yet.\n"
                f"Contact {config.SUPPORT_HANDLE} to top up.", back_menu())
            return
        await safe_edit(query, "How much would you like to add?", amount_menu())

    elif data == "topup_custom":
        context.user_data["awaiting"] = "topup_amount"
        await safe_edit(
            query,
            "\u270F\uFE0F Type the amount you want to add (e.g. 15), then send it.",
            InlineKeyboardMarkup([[
                InlineKeyboardButton("\u2B05\uFE0F Back", callback_data="topup")
            ]]),
        )

    elif data.startswith("topup_amt:"):
        context.user_data["topup_amount"] = float(data.split(":", 1)[1])
        await safe_edit(query, "Choose which coin you will pay with:", coin_menu())

    elif data.startswith("topup_coin:"):
        coin   = data.split(":", 1)[1]
        amount = context.user_data.get("topup_amount")
        if not amount:
            await safe_edit(query, "Please pick an amount first.", amount_menu())
            return
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
    await db.ensure_user(user_id)
    await db.record_payment(payment_id, user_id, Decimal(str(amount)), coin)
    context.user_data["topup_amount"]  = None  # clear so it can't be reused

    await safe_edit(
        query,
        f"\U0001F4B3 <b>Top-Up Instructions</b>\n\n"
        f"Amount: <b>{config.CURRENCY_SYMBOL}{amount:g}</b>\n"
        f"Coin:   <b>{coin}</b>\n\n"
        f"Send the equivalent amount in <b>{coin}</b> to:\n"
        f"<code>{address}</code>\n\n"
        f"{config.PAYMENT_NOTE}\n\n"
        "Once you have sent, tap the button below.",
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

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(on_button))
    app.add_handler(MessageHandler(filters.PHOTO & filters.UpdateType.MESSAGE, on_photo))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))

    logger.info("Bot is starting...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
