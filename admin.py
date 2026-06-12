"""
Admin panel — Telegram commands + inline menu.

All handlers are registered via register_admin_handlers(app).
Every handler silently ignores non-admin callers.

Commands:
  /admin              — open the panel
  /credit ID AMOUNT   — quick credit user balance
  /deduct ID AMOUNT   — quick deduct user balance
  /userinfo ID        — quick user lookup
  /broadcast TEXT     — quick broadcast (no confirmation)
  /upload SUBL_ID     — prime the bot then send a file on the next message

Bulk upload (easiest way):
  Send a .txt or .csv file directly to the bot.
  • With caption   → "dd-28th"  — goes straight into that list.
  • Without caption → bot shows a list picker, then processes.
  • Or: /upload dd-28th, then send the file.

Supported file formats (one item per line, blank lines / #comments skipped):
  BIN|YEAR|CODE|PRICE|CONTENT       ← pipe-separated  (preferred)
  BIN,YEAR,CODE,PRICE,CONTENT       ← comma-separated
  BIN\tYEAR\tCODE\tPRICE\tCONTENT  ← tab-separated
  Content may contain the delimiter — everything after the 4th separator
  is treated as content.

Inline panel sections:
  📊 Stats      — live dashboard
  📦 Stock      — per-list counts, add / delete items, upload file
  👥 Users      — lookup, adjust balance, ban / unban
  📋 Orders     — last 20 transactions
  📢 Broadcast  — type + confirm before sending
"""

import logging
import uuid
from decimal import Decimal, InvalidOperation

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import BadRequest, Forbidden
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

import config
import channel_log
import db

logger = logging.getLogger(__name__)


# ============================================================
#  Guard
# ============================================================
def is_admin(user_id: int) -> bool:
    return user_id in config.ADMIN_IDS


def admin_only(fn):
    """Decorator that silently drops calls from non-admins."""
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        uid = update.effective_user.id if update.effective_user else 0
        if not is_admin(uid):
            return
        return await fn(update, context)
    wrapper.__name__ = fn.__name__
    return wrapper


# ============================================================
#  Keyboards
# ============================================================
def admin_home_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📊 Stats",    callback_data="adm_stats"),
            InlineKeyboardButton("📦 Stock",    callback_data="adm_stock"),
        ],
        [
            InlineKeyboardButton("👥 Users",    callback_data="adm_users"),
            InlineKeyboardButton("📋 Orders",   callback_data="adm_orders"),
        ],
        [
            InlineKeyboardButton("💳 Payments", callback_data="adm_payments"),
            InlineKeyboardButton("🏷️ Labels",   callback_data="adm_labels"),
        ],
        [InlineKeyboardButton("📢 Broadcast",   callback_data="adm_broadcast")],
        [InlineKeyboardButton("❌ Close",        callback_data="adm_close")],
    ])


def back_to_admin() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("⬅️ Admin Menu", callback_data="adm_menu")]]
    )


def stock_overview_kb(counts: dict) -> InlineKeyboardMarkup:
    rows = []
    for cat in config.CATEGORIES:
        for subl in cat.get("sublists", []):
            sid = subl["id"]
            n = counts.get(sid, 0)
            rows.append([InlineKeyboardButton(
                f"{subl['label']}  [{n} in stock]",
                callback_data=f"adm_slist:{sid}",
            )])
    rows.append([InlineKeyboardButton("⬅️ Admin Menu", callback_data="adm_menu")])
    return InlineKeyboardMarkup(rows)


def stock_list_kb(subl_id: str, items: list) -> InlineKeyboardMarkup:
    rows = []
    for it in items[:20]:
        label = f"❌ {it['bin']} - {it['year']} - {it['code']} - {config.CURRENCY_SYMBOL}{it['price']:g}"
        rows.append([InlineKeyboardButton(label, callback_data=f"adm_sdel:{it['id']}")])
    rows.append([InlineKeyboardButton(
        "➕ Add Item", callback_data=f"adm_sadd:{subl_id}"
    )])
    rows.append([InlineKeyboardButton(
        "📤 Upload File", callback_data=f"adm_upload_prompt:{subl_id}"
    )])
    rows.append([InlineKeyboardButton("⬅️ Back", callback_data="adm_stock")])
    return InlineKeyboardMarkup(rows)


def upload_list_picker_kb() -> InlineKeyboardMarkup:
    """Shown when a file arrives with no caption — pick which list to import into."""
    rows = []
    for cat in config.CATEGORIES:
        for subl in cat.get("sublists", []):
            rows.append([InlineKeyboardButton(
                subl["label"], callback_data=f"adm_upload_to:{subl['id']}"
            )])
    rows.append([InlineKeyboardButton("❌ Cancel", callback_data="adm_stock")])
    return InlineKeyboardMarkup(rows)


def labels_kb(overrides: dict) -> InlineKeyboardMarkup:
    """
    One row per renameable label.
    Left button = current name (tap to edit).
    Right button = ↩️ Reset (only shown when overridden).
    """
    rows = []
    for key, default in config.RENAMEABLE.items():
        current     = overrides.get(key, default)
        overridden  = key in overrides
        edit_btn    = InlineKeyboardButton(
            f"{'🔄 ' if overridden else ''}{current}",
            callback_data=f"adm_label_edit:{key}",
        )
        if overridden:
            reset_btn = InlineKeyboardButton("↩️", callback_data=f"adm_label_reset:{key}")
            rows.append([edit_btn, reset_btn])
        else:
            rows.append([edit_btn])
    rows.append([InlineKeyboardButton("⬅️ Admin Menu", callback_data="adm_menu")])
    return InlineKeyboardMarkup(rows)


def user_detail_kb(user_id: int, banned: bool) -> InlineKeyboardMarkup:
    ban_label = "✅ Unban" if banned else "🚫 Ban"
    ban_cb    = f"adm_unban:{user_id}" if banned else f"adm_ban:{user_id}"
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("➕ Add Balance", callback_data=f"adm_badd:{user_id}"),
            InlineKeyboardButton("➖ Deduct Balance", callback_data=f"adm_bsub:{user_id}"),
        ],
        [InlineKeyboardButton(ban_label, callback_data=ban_cb)],
        [InlineKeyboardButton("⬅️ Admin Menu", callback_data="adm_menu")],
    ])


# ============================================================
#  /admin command
# ============================================================
@admin_only
async def cmd_admin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "🛠️ <b>Admin Panel</b>\n\nChoose a section:",
        reply_markup=admin_home_kb(),
        parse_mode="HTML",
    )


# ============================================================
#  Quick commands
# ============================================================
@admin_only
async def cmd_credit(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Usage: /credit USER_ID AMOUNT"""
    parts = (context.args or [])
    if len(parts) != 2:
        await update.message.reply_text("Usage: /credit USER_ID AMOUNT")
        return
    try:
        uid    = int(parts[0])
        amount = Decimal(parts[1])
        if amount <= 0:
            raise ValueError
    except (ValueError, InvalidOperation):
        await update.message.reply_text("Invalid ID or amount.")
        return
    await db.ensure_user(uid)
    new_bal = await db.adjust_balance(uid, amount)
    await update.message.reply_text(
        f"✅ Credited {config.CURRENCY_SYMBOL}{amount:g} to user {uid}.\n"
        f"New balance: {config.CURRENCY_SYMBOL}{new_bal:.2f}"
    )


@admin_only
async def cmd_deduct(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Usage: /deduct USER_ID AMOUNT"""
    parts = (context.args or [])
    if len(parts) != 2:
        await update.message.reply_text("Usage: /deduct USER_ID AMOUNT")
        return
    try:
        uid    = int(parts[0])
        amount = Decimal(parts[1])
        if amount <= 0:
            raise ValueError
    except (ValueError, InvalidOperation):
        await update.message.reply_text("Invalid ID or amount.")
        return
    new_bal = await db.adjust_balance(uid, -amount)
    await update.message.reply_text(
        f"✅ Deducted {config.CURRENCY_SYMBOL}{amount:g} from user {uid}.\n"
        f"New balance: {config.CURRENCY_SYMBOL}{new_bal:.2f}"
    )


@admin_only
async def cmd_userinfo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Usage: /userinfo USER_ID"""
    parts = (context.args or [])
    if not parts:
        await update.message.reply_text("Usage: /userinfo USER_ID")
        return
    try:
        uid = int(parts[0])
    except ValueError:
        await update.message.reply_text("Invalid user ID.")
        return
    info = await db.get_user_info(uid)
    if not info:
        await update.message.reply_text(f"User {uid} not found.")
        return
    await update.message.reply_text(
        _user_info_text(info),
        reply_markup=user_detail_kb(uid, info["banned"]),
        parse_mode="HTML",
    )


@admin_only
async def cmd_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Usage: /broadcast YOUR MESSAGE HERE"""
    if not context.args:
        await update.message.reply_text("Usage: /broadcast YOUR MESSAGE")
        return
    msg = " ".join(context.args)
    await _do_broadcast(update.message.reply_text, context.bot, msg)


# ============================================================
#  Inline panel router
# ============================================================
@admin_only
async def adm_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    data  = query.data
    uid   = query.from_user.id

    # ---- Home / close ----
    if data == "adm_menu":
        await _safe_edit(query, "🛠️ <b>Admin Panel</b>\n\nChoose a section:",
                         admin_home_kb())

    elif data == "adm_close":
        await query.delete_message()

    # ---- Stats ----
    elif data == "adm_stats":
        s = await db.get_stats()
        text = (
            "📊 <b>Stats</b>\n\n"
            f"👤 Total users:    <b>{s['total_users']}</b>\n"
            f"🚫 Banned users:   <b>{s['banned_users']}</b>\n"
            f"📦 Stock (live):   <b>{s['total_stock']}</b>\n"
            f"✅ Sold items:     <b>{s['sold_stock']}</b>\n"
            f"🛒 Total orders:   <b>{s['total_orders']}</b>\n"
            f"💰 Total revenue:  <b>{config.CURRENCY_SYMBOL}{s['total_revenue']:.2f}</b>\n"
            f"⏳ Pending topups: <b>{s['pending_pays']}</b>"
        )
        await _safe_edit(query, text, back_to_admin())

    # ---- Stock overview ----
    elif data == "adm_stock":
        counts = await db.get_stock_counts()
        await _safe_edit(query, "📦 <b>Stock Management</b>\n\nTap a list to manage it:",
                         stock_overview_kb(counts))

    elif data.startswith("adm_slist:"):
        subl_id = data.split(":", 1)[1]
        items = await db.get_stock(subl_id)
        label = _subl_label(subl_id)
        text = (
            f"📦 <b>{label}</b>  —  {len(items)} item(s) in stock\n\n"
            "Tap ❌ next to an item to delete it.\n"
            "Add new items with ➕ Add Item.\n\n"
            "<i>Format you'll be asked for:</i>\n"
            "<code>BIN|YEAR|CODE|PRICE|CONTENT</code>\n"
            "<i>e.g. 459667|2012|Ex3|5|4597...:2025 exp...:123</i>"
        )
        await _safe_edit(query, text, stock_list_kb(subl_id, items))

    elif data.startswith("adm_sdel:"):
        item_id = data.split(":", 1)[1]
        item = await db.get_stock_item(item_id)
        if not item:
            await query.answer("Item not found or already sold.", show_alert=True)
            return
        await db.remove_stock_item(item_id)
        await query.answer("✅ Item deleted.", show_alert=False)
        # refresh the list
        subl_id = item["subl_id"]
        items   = await db.get_stock(subl_id)
        label   = _subl_label(subl_id)
        await _safe_edit(
            query,
            f"📦 <b>{label}</b>  —  {len(items)} item(s) in stock",
            stock_list_kb(subl_id, items),
        )

    elif data.startswith("adm_sadd:"):
        subl_id = data.split(":", 1)[1]
        context.user_data["adm_awaiting"] = "add_item"
        context.user_data["adm_subl"]     = subl_id
        label = _subl_label(subl_id)
        await _safe_edit(
            query,
            f"➕ <b>Add Item to {label}</b>\n\n"
            "Send the item in this format (pipe-separated):\n"
            "<code>BIN|YEAR|CODE|PRICE|CONTENT</code>\n\n"
            "Example:\n"
            "<code>459667|2012|Ex3|5|4597xx 09/28 123 John Doe</code>\n\n"
            "You can paste multiple lines to add them in bulk.",
            InlineKeyboardMarkup([[
                InlineKeyboardButton("❌ Cancel", callback_data=f"adm_slist:{subl_id}")
            ]]),
        )

    elif data.startswith("adm_upload_prompt:"):
        subl_id = data.split(":", 1)[1]
        context.user_data["adm_awaiting"]    = "upload_file"
        context.user_data["adm_upload_subl"] = subl_id
        label = _subl_label(subl_id)
        await _safe_edit(
            query,
            f"📤 <b>Upload File → {label}</b>\n\n"
            "Send your <code>.txt</code> or <code>.csv</code> file now.\n\n"
            "<b>Format</b> (one item per line):\n"
            "<code>BIN|YEAR|CODE|PRICE|CONTENT</code>\n\n"
            "e.g. <code>459667|2012|Ex3|5|4597xx 09/28 123 John Doe</code>\n\n"
            "Comma and tab delimiters also accepted.\n"
            "Lines starting with <code>#</code> and blank lines are skipped.\n"
            "Content may contain the delimiter — everything after the 4th\n"
            "separator is treated as content.",
            InlineKeyboardMarkup([[
                InlineKeyboardButton("❌ Cancel", callback_data=f"adm_slist:{subl_id}")
            ]]),
        )

    elif data.startswith("adm_upload_to:"):
        subl_id = data.split(":", 1)[1]
        file_id = context.user_data.pop("adm_pending_file_id", None)
        if not file_id:
            await query.answer("Session expired. Please send the file again.",
                               show_alert=True)
            return
        await query.delete_message()
        # Create a fresh message to show progress and report on.
        fresh = await context.bot.send_message(
            query.from_user.id, "⏳ Processing…"
        )
        await _run_upload(fresh, subl_id, file_id, context)

    # ---- Users ----
    elif data == "adm_users":
        context.user_data["adm_awaiting"] = "lookup_user"
        await _safe_edit(
            query,
            "👥 <b>User Lookup</b>\n\nSend the Telegram user ID you want to look up:",
            InlineKeyboardMarkup([[
                InlineKeyboardButton("❌ Cancel", callback_data="adm_menu")
            ]]),
        )

    elif data.startswith("adm_ban:"):
        target = int(data.split(":", 1)[1])
        await db.set_banned(target, True)
        await channel_log.user_banned(target, query.from_user.id, True)
        await query.answer("🚫 User banned.", show_alert=True)
        await _refresh_user(query, target)

    elif data.startswith("adm_unban:"):
        target = int(data.split(":", 1)[1])
        await db.set_banned(target, False)
        await channel_log.user_banned(target, query.from_user.id, False)
        await query.answer("✅ User unbanned.", show_alert=True)
        await _refresh_user(query, target)

    elif data.startswith("adm_badd:"):
        target = int(data.split(":", 1)[1])
        context.user_data["adm_awaiting"] = "bal_delta"
        context.user_data["adm_bal_uid"]  = target
        context.user_data["adm_bal_sign"] = "+"
        await _safe_edit(
            query,
            f"➕ <b>Add Balance</b> to user {target}\n\nSend the amount to add (number only):",
            InlineKeyboardMarkup([[
                InlineKeyboardButton("❌ Cancel", callback_data="adm_menu")
            ]]),
        )

    elif data.startswith("adm_bsub:"):
        target = int(data.split(":", 1)[1])
        context.user_data["adm_awaiting"] = "bal_delta"
        context.user_data["adm_bal_uid"]  = target
        context.user_data["adm_bal_sign"] = "-"
        await _safe_edit(
            query,
            f"➖ <b>Deduct Balance</b> from user {target}\n\nSend the amount to deduct:",
            InlineKeyboardMarkup([[
                InlineKeyboardButton("❌ Cancel", callback_data="adm_menu")
            ]]),
        )

    # ---- Orders ----
    elif data == "adm_orders":
        orders = await db.get_recent_orders(20)
        if not orders:
            await _safe_edit(query, "📋 No orders yet.", back_to_admin())
            return
        lines = ["📋 <b>Last 20 Orders</b>\n"]
        for o in orders:
            bin_  = o.get("bin") or "?"
            year  = o.get("year") or "?"
            code  = o.get("code") or "?"
            lines.append(
                f"• <code>{o['user_id']}</code>  "
                f"{bin_} - {year} - {code}  "
                f"{config.CURRENCY_SYMBOL}{o['amount']:.2f}  "
                f"<i>{o['created_at'].strftime('%d/%m %H:%M')}</i>"
            )
        await _safe_edit(query, "\n".join(lines), back_to_admin())

    # ---- Payments ----
    elif data == "adm_payments":
        pending = await db.get_pending_payments()
        if not pending:
            await _safe_edit(query,
                "💳 <b>Payments</b>\n\nNo pending payments right now. ✅",
                back_to_admin())
            return
        lines = [f"💳 <b>Pending Payments</b> ({len(pending)})\n"]
        for p in pending:
            ref = p['tx_ref'].replace("txid:", "").replace("photo:", "📷 ")
            ref = ref[:30] + "…" if len(ref) > 30 else ref
            lines.append(
                f"• <code>{p['payment_id']}</code>\n"
                f"  User: <code>{p['user_id']}</code>  "
                f"{config.CURRENCY_SYMBOL}{p['amount']:.2f} {p['coin']}\n"
                f"  Ref: {ref or 'awaiting proof'}\n"
            )
        rows = []
        for p in pending[:10]:
            rows.append([
                InlineKeyboardButton(
                    f"✅ {config.CURRENCY_SYMBOL}{p['amount']:.2f} – {p['user_id']}",
                    callback_data=f"adm_pay_approve:{p['payment_id']}"),
                InlineKeyboardButton("❌",
                    callback_data=f"adm_pay_reject:{p['payment_id']}"),
            ])
        rows.append([InlineKeyboardButton("⬅️ Admin Menu", callback_data="adm_menu")])
        await _safe_edit(query, "\n".join(lines), InlineKeyboardMarkup(rows))

    elif data.startswith("adm_pay_approve:"):
        payment_id = data.split(":", 1)[1]
        result = await db.approve_payment(payment_id)
        if not result:
            await query.answer("Already handled or not found.", show_alert=True)
            return
        uid, amt = result["user_id"], result["amount"]
        bal = await db.get_balance(uid)
        await channel_log.payment_approved(uid, float(amt), float(bal), query.from_user.id)
        await query.answer(f"✅ Approved! {config.CURRENCY_SYMBOL}{amt:.2f} credited.", show_alert=True)
        # Notify the user
        try:
            await context.bot.send_message(
                uid,
                f"✅ <b>Top-up Approved!</b>\n\n"
                f"{config.CURRENCY_SYMBOL}{amt:.2f} has been added to your wallet.\n"
                f"New balance: <b>{config.CURRENCY_SYMBOL}{bal:.2f}</b>",
                parse_mode="HTML",
            )
        except Exception:
            pass
        # Refresh payments list
        pending = await db.get_pending_payments()
        if not pending:
            await _safe_edit(query, "💳 <b>Payments</b>\n\nNo pending payments. ✅",
                             back_to_admin())
        else:
            await query.answer()

    elif data.startswith("adm_pay_reject:"):
        payment_id = data.split(":", 1)[1]
        result = await db.reject_payment(payment_id)
        if not result:
            await query.answer("Already handled or not found.", show_alert=True)
            return
        uid = result["user_id"]
        await channel_log.payment_rejected(uid, float(result["amount"]), query.from_user.id)
        await query.answer("❌ Rejected.", show_alert=True)
        try:
            await context.bot.send_message(
                uid,
                f"❌ <b>Top-up Rejected</b>\n\n"
                "Your payment could not be verified.\n"
                f"Please contact {config.SUPPORT_HANDLE} if you believe this is an error.",
                parse_mode="HTML",
            )
        except Exception:
            pass
        pending = await db.get_pending_payments()
        if not pending:
            await _safe_edit(query, "💳 <b>Payments</b>\n\nNo pending payments. ✅",
                             back_to_admin())
        else:
            await query.answer()

    # ---- Labels ----
    elif data == "adm_labels":
        overrides = await db.get_all_label_overrides()
        changed   = len(overrides)
        heading   = (
            "🏷️ <b>Labels</b>\n\n"
            "Tap any label to rename it.\n"
            "🔄 = currently overridden  ↩️ = reset to default\n"
            f"<i>{changed} override(s) active</i>"
        )
        await _safe_edit(query, heading, labels_kb(overrides))

    elif data.startswith("adm_label_edit:"):
        key     = data.split(":", 1)[1]
        default = config.default_label(key)
        current = db.get_label(key, default)
        context.user_data["adm_awaiting"]   = "label_edit"
        context.user_data["adm_label_key"]  = key
        await _safe_edit(
            query,
            f"🏷️ <b>Rename Label</b>\n\n"
            f"Key: <code>{key}</code>\n"
            f"Current: <b>{current}</b>\n"
            f"Default: <i>{default}</i>\n\n"
            "Send the new display name now.\n"
            "You can use emojis — e.g. <code>🗂️ Fresh Files</code>",
            InlineKeyboardMarkup([[
                InlineKeyboardButton("❌ Cancel", callback_data="adm_labels")
            ]]),
        )

    elif data.startswith("adm_label_reset:"):
        key     = data.split(":", 1)[1]
        default = config.default_label(key)
        await db.reset_label(key)
        await query.answer(f"↩️ Reset to: {default}", show_alert=False)
        overrides = await db.get_all_label_overrides()
        await _safe_edit(
            query,
            "🏷️ <b>Labels</b>\n\n"
            "Tap any label to rename it.\n"
            "🔄 = currently overridden  ↩️ = reset to default\n"
            f"<i>{len(overrides)} override(s) active</i>",
            labels_kb(overrides),
        )

    # ---- Broadcast ----
    elif data == "adm_broadcast":
        context.user_data["adm_awaiting"] = "broadcast_compose"
        await _safe_edit(
            query,
            "📢 <b>Broadcast</b>\n\nType the message you want to send to all users.\n"
            "HTML formatting is supported (<b>bold</b>, <i>italic</i>, <code>code</code>).",
            InlineKeyboardMarkup([[
                InlineKeyboardButton("❌ Cancel", callback_data="adm_menu")
            ]]),
        )

    elif data.startswith("adm_bc_confirm:"):
        msg_key = data.split(":", 1)[1]
        msg     = context.bot_data.get(msg_key, "")
        if not msg:
            await query.answer("Message expired. Please start over.", show_alert=True)
            return
        await _safe_edit(query, "📢 Sending broadcast…", back_to_admin())
        await _do_broadcast(query.message.reply_text, context.bot, msg)

    elif data.startswith("adm_bc_cancel:"):
        msg_key = data.split(":", 1)[1]
        context.bot_data.pop(msg_key, None)
        await _safe_edit(query, "❌ Broadcast cancelled.", admin_home_kb())


# ============================================================
#  Admin text input router
# ============================================================
@admin_only
async def adm_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    awaiting = context.user_data.get("adm_awaiting")
    if not awaiting:
        return

    if awaiting == "add_item":
        await _handle_add_item(update, context)
    elif awaiting == "lookup_user":
        await _handle_lookup_user(update, context)
    elif awaiting == "bal_delta":
        await _handle_bal_delta(update, context)
    elif awaiting == "broadcast_compose":
        await _handle_broadcast_compose(update, context)
    elif awaiting == "label_edit":
        await _handle_label_edit(update, context)
    elif awaiting == "upload_file":
        # File expected — remind admin to send the actual file
        await update.message.reply_text(
            "⚠️ Please send a <code>.txt</code> or <code>.csv</code> file, "
            "not a text message.",
            parse_mode="HTML",
        )


# ============================================================
#  Text sub-handlers
# ============================================================
async def _handle_add_item(update, context) -> None:
    subl_id = context.user_data.get("adm_subl", "")
    lines   = [l.strip() for l in update.message.text.strip().splitlines() if l.strip()]
    added, failed = 0, 0
    for line in lines:
        parts = [p.strip() for p in line.split("|")]
        if len(parts) < 5:
            failed += 1
            continue
        bin_, year, code = parts[0], parts[1], parts[2]
        content = "|".join(parts[4:])   # content may contain pipes
        try:
            price = Decimal(parts[3])
            if not bin_.isdigit() or len(bin_) < 4:
                raise ValueError
        except (InvalidOperation, ValueError):
            failed += 1
            continue
        await db.add_stock_item(subl_id, bin_, year, code, price, content)
        added += 1

    context.user_data["adm_awaiting"] = None
    items = await db.get_stock(subl_id)
    label = _subl_label(subl_id)
    result = f"✅ Added {added} item(s)."
    if failed:
        result += f"  ⚠️ {failed} line(s) skipped (wrong format)."
    await update.message.reply_text(
        f"{result}\n\n📦 <b>{label}</b> now has {len(items)} item(s) in stock.",
        reply_markup=stock_list_kb(subl_id, items),
        parse_mode="HTML",
    )


async def _handle_lookup_user(update, context) -> None:
    context.user_data["adm_awaiting"] = None
    raw = update.message.text.strip()
    try:
        uid = int(raw)
    except ValueError:
        await update.message.reply_text("Please send a numeric user ID.")
        return
    info = await db.get_user_info(uid)
    if not info:
        await update.message.reply_text(f"User {uid} not found in the database.")
        return
    await update.message.reply_text(
        _user_info_text(info),
        reply_markup=user_detail_kb(uid, info["banned"]),
        parse_mode="HTML",
    )


async def _handle_bal_delta(update, context) -> None:
    uid  = context.user_data.get("adm_bal_uid")
    sign = context.user_data.get("adm_bal_sign", "+")
    context.user_data["adm_awaiting"] = None
    try:
        amount = Decimal(update.message.text.strip())
        if amount <= 0:
            raise ValueError
    except (InvalidOperation, ValueError):
        await update.message.reply_text("Please send a positive number.")
        return
    delta   = amount if sign == "+" else -amount
    new_bal = await db.adjust_balance(uid, delta)
    await channel_log.balance_adjusted(uid, update.effective_user.id, float(delta), float(new_bal))
    verb    = "added to" if sign == "+" else "deducted from"
    await update.message.reply_text(
        f"✅ {config.CURRENCY_SYMBOL}{amount:g} {verb} user {uid}.\n"
        f"New balance: <b>{config.CURRENCY_SYMBOL}{new_bal:.2f}</b>",
        parse_mode="HTML",
    )


async def _handle_broadcast_compose(update, context) -> None:
    context.user_data["adm_awaiting"] = None
    msg     = update.message.text.strip()
    msg_key = f"bc_{update.message.message_id}"
    context.bot_data[msg_key] = msg

    preview = msg[:300] + ("…" if len(msg) > 300 else "")
    user_count = len(await db.get_all_user_ids())

    await update.message.reply_text(
        f"📢 <b>Broadcast Preview</b>\n\n"
        f"{preview}\n\n"
        f"This will be sent to <b>{user_count}</b> user(s). Confirm?",
        reply_markup=InlineKeyboardMarkup([
            [
                InlineKeyboardButton("✅ Send", callback_data=f"adm_bc_confirm:{msg_key}"),
                InlineKeyboardButton("❌ Cancel", callback_data=f"adm_bc_cancel:{msg_key}"),
            ]
        ]),
        parse_mode="HTML",
    )


async def _handle_label_edit(update, context) -> None:
    key  = context.user_data.pop("adm_label_key", None)
    context.user_data["adm_awaiting"] = None
    if not key:
        return
    new_value = update.message.text.strip()
    if not new_value:
        await update.message.reply_text("Name cannot be empty. No changes made.")
        return
    if len(new_value) > 64:
        await update.message.reply_text("Name too long (max 64 characters). Try again.")
        context.user_data["adm_awaiting"]  = "label_edit"
        context.user_data["adm_label_key"] = key
        return
    await db.set_label(key, new_value)
    overrides = await db.get_all_label_overrides()
    await update.message.reply_text(
        f"✅ <b>{key}</b> renamed to: <b>{new_value}</b>\n\n"
        "The change is live instantly — users will see it on their next tap.",
        reply_markup=labels_kb(overrides),
        parse_mode="HTML",
    )


# ============================================================
#  /rename quick command
# ============================================================
@admin_only
async def cmd_rename(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Usage:  /rename KEY New display name
    Examples:
      /rename subl:dd-28th 🔸 28th Base
      /rename cat:ff 🗓️ Fresh Files
      /rename menu:store 🏪 Shop
    Run /rename with no arguments to see all valid keys.
    """
    args = context.args or []

    # No args → show all valid keys + current values
    if not args:
        lines = ["🏷️ <b>Renameable Labels</b>\n",
                 "Usage: <code>/rename KEY New Name</code>\n"]
        for key, default in config.RENAMEABLE.items():
            current = db.get_label(key, default)
            changed = " 🔄" if current != default else ""
            lines.append(f"<code>{key}</code>{changed}\n  → {current}")
        await update.message.reply_text(
            "\n".join(lines), parse_mode="HTML"
        )
        return

    key       = args[0].lower()
    new_value = " ".join(args[1:]).strip()

    if key not in config.RENAMEABLE:
        valid = "\n".join(f"  <code>{k}</code>" for k in config.RENAMEABLE)
        await update.message.reply_text(
            f"❌ Unknown key: <code>{key}</code>\n\n"
            f"Valid keys:\n{valid}",
            parse_mode="HTML",
        )
        return

    if not new_value:
        await update.message.reply_text(
            "Please provide the new name after the key.\n"
            f"Example: <code>/rename {key} 🔸 New Name</code>",
            parse_mode="HTML",
        )
        return

    if len(new_value) > 64:
        await update.message.reply_text("Name too long (max 64 characters).")
        return

    old_value = db.get_label(key, config.default_label(key))
    await db.set_label(key, new_value)
    await update.message.reply_text(
        f"✅ Renamed <code>{key}</code>\n"
        f"  Before: <i>{old_value}</i>\n"
        f"  After:  <b>{new_value}</b>\n\n"
        "Live immediately — no restart needed.",
        parse_mode="HTML",
    )


# ============================================================
#  Helpers
# ============================================================
def _subl_label(subl_id: str) -> str:
    for cat in config.CATEGORIES:
        for s in cat.get("sublists", []):
            if s["id"] == subl_id:
                return s["label"]
    return subl_id


def _find_subl_by_name(text: str) -> str | None:
    """
    Match free text to a sublist ID.
    Tries exact ID match first, then partial label match.
    e.g. "dd-28th" → "dd-28th"  |  "DD28" → "dd-28th"  |  "28th" → "dd-28th"
    """
    if not text:
        return None
    lower = text.lower().strip()
    all_subls = [s for cat in config.CATEGORIES for s in cat.get("sublists", [])]
    # Exact ID
    for s in all_subls:
        if s["id"] == lower:
            return s["id"]
    # Partial ID
    for s in all_subls:
        if lower in s["id"] or s["id"] in lower:
            return s["id"]
    # Partial label (strip emoji)
    for s in all_subls:
        clean = s["label"].encode("ascii", "ignore").decode().lower().strip()
        if lower in clean or clean in lower:
            return s["id"]
    return None


def _user_info_text(info: dict) -> str:
    joined = info["joined"].strftime("%d %b %Y") if info.get("joined") else "?"
    status = "🚫 BANNED" if info["banned"] else "✅ Active"
    return (
        f"👤 <b>User {info['user_id']}</b>\n\n"
        f"Status:   {status}\n"
        f"Balance:  <b>{config.CURRENCY_SYMBOL}{info['balance']:.2f}</b>\n"
        f"Orders:   {info['orders']}\n"
        f"Spent:    {config.CURRENCY_SYMBOL}{info['spent']:.2f}\n"
        f"Joined:   {joined}"
    )


async def _refresh_user(query, user_id: int) -> None:
    info = await db.get_user_info(user_id)
    if info:
        await _safe_edit(query, _user_info_text(info),
                         user_detail_kb(user_id, info["banned"]))


async def _safe_edit(query, text, reply_markup) -> None:
    try:
        await query.edit_message_text(text, reply_markup=reply_markup, parse_mode="HTML")
    except BadRequest as e:
        if "not modified" not in str(e).lower():
            raise



# ============================================================
#  File parser
# ============================================================
def _detect_delimiter(line: str) -> str:
    """Pick the delimiter that gives ≥5 fields on the first data line."""
    for sep in ("|", ",", "\t"):
        if len(line.split(sep)) >= 5:
            return sep
    return "|"  # fallback — let validation catch bad lines


def _parse_stock_file(raw: str, subl_id: str) -> tuple[list[tuple], int, list[str]]:
    """
    Parse file text into DB-ready tuples.
    Returns (rows, skipped_count, sample_errors).
    Each row = (id, subl_id, bin, year, code, price, content).
    """
    lines = [l.rstrip() for l in raw.splitlines()]
    # Find first non-blank, non-comment line to detect delimiter.
    data_lines = [l for l in lines if l and not l.startswith("#")]
    if not data_lines:
        return [], 0, []

    sep = _detect_delimiter(data_lines[0])
    rows: list[tuple] = []
    skipped  = 0
    errors: list[str] = []

    for raw_line in lines:
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue                              # blank / comment

        parts = line.split(sep)
        if len(parts) < 5:
            skipped += 1
            if len(errors) < 3:
                errors.append(f"<code>{line[:60]}</code>")
            continue

        bin_  = parts[0].strip()
        year  = parts[1].strip()
        code  = parts[2].strip()
        raw_price = parts[3].strip().lstrip("£$€")
        # Content = everything after the 4th field, rejoined with the delimiter.
        content = sep.join(parts[4:]).strip()

        # Validate
        if not bin_.isdigit() or len(bin_) < 4:
            skipped += 1
            if len(errors) < 3:
                errors.append(f"Bad BIN: <code>{line[:60]}</code>")
            continue
        try:
            price = Decimal(raw_price)
        except InvalidOperation:
            skipped += 1
            if len(errors) < 3:
                errors.append(f"Bad price: <code>{line[:60]}</code>")
            continue
        if not content:
            skipped += 1
            if len(errors) < 3:
                errors.append(f"Empty content: <code>{line[:60]}</code>")
            continue

        item_id = uuid.uuid4().hex[:8]
        rows.append((item_id, subl_id, bin_, year, code, price, content))

    return rows, skipped, errors


async def _run_upload(message, subl_id: str, file_id: str, context) -> None:
    """Download the file, parse it, bulk-insert, reply with a report."""
    label = _subl_label(subl_id)
    status_msg = await message.reply_text(
        f"⏳ Downloading and parsing file for <b>{label}</b>…",
        parse_mode="HTML",
    )

    try:
        tg_file = await context.bot.get_file(file_id)
        raw_bytes = await tg_file.download_as_bytearray()
        # Decode — try UTF-8, fall back to latin-1.
        try:
            raw_text = raw_bytes.decode("utf-8")
        except UnicodeDecodeError:
            raw_text = raw_bytes.decode("latin-1")
    except Exception as exc:
        logger.exception("File download failed")
        await status_msg.edit_text(f"❌ Could not download the file: {exc}")
        return

    rows, skipped, sample_errors = _parse_stock_file(raw_text, subl_id)

    if not rows and skipped == 0:
        await status_msg.edit_text(
            "⚠️ The file appears empty or contains no parseable lines.",
        )
        return

    result = await db.bulk_add_stock_items(rows)
    inserted  = result["inserted"]
    duplicate = result["duplicate"]
    total_now = len(await db.get_stock(subl_id))

    report_lines = [
        f"📤 <b>Upload complete — {label}</b>\n",
        f"✅ Inserted:    <b>{inserted}</b>",
        f"♻️ Duplicates:  <b>{duplicate}</b>",
        f"⚠️ Parse errors: <b>{skipped}</b>",
        f"📦 Total in stock now: <b>{total_now}</b>",
    ]
    if sample_errors:
        report_lines.append("\nSample bad lines (up to 3):")
        report_lines += [f"  • {e}" for e in sample_errors]

    await status_msg.edit_text("\n".join(report_lines), parse_mode="HTML")


# ============================================================
#  /upload command
# ============================================================
@admin_only
async def cmd_upload(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Usage: /upload SUBL_ID  — bot will then wait for a file."""
    parts = context.args or []
    if not parts:
        await update.message.reply_text(
            "Usage: /upload <b>LIST_ID</b>\n"
            "Then send your .txt or .csv file as the next message.\n\n"
            "Available list IDs:\n" +
            "\n".join(
                f"  <code>{s['id']}</code>  {s['label']}"
                for cat in config.CATEGORIES
                for s in cat.get("sublists", [])
            ),
            parse_mode="HTML",
        )
        return
    subl_id = parts[0].lower()
    # Validate it exists
    valid = [s["id"] for cat in config.CATEGORIES for s in cat.get("sublists", [])]
    if subl_id not in valid:
        await update.message.reply_text(
            f"❌ Unknown list ID: <code>{subl_id}</code>\n"
            "Valid IDs: " + ", ".join(f"<code>{i}</code>" for i in valid),
            parse_mode="HTML",
        )
        return
    context.user_data["adm_awaiting"]    = "upload_file"
    context.user_data["adm_upload_subl"] = subl_id
    label = _subl_label(subl_id)
    await update.message.reply_text(
        f"📤 Ready to import into <b>{label}</b>.\n\n"
        "Now send your <code>.txt</code> or <code>.csv</code> file.\n\n"
        "<b>Required format</b> (one item per line):\n"
        "<code>BIN|YEAR|CODE|PRICE|CONTENT</code>\n"
        "e.g. <code>459667|2012|Ex3|5|4597xx 09/28 123 John Doe</code>\n\n"
        "Comma and tab delimiters are also accepted.\n"
        "Lines starting with <code>#</code> and blank lines are skipped.",
        parse_mode="HTML",
    )


# ============================================================
#  Document (file) handler
# ============================================================
@admin_only
async def adm_document(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles .txt / .csv file uploads from admin for bulk stock import."""
    doc = update.message.document
    if not doc:
        return

    # Accept only text-like files.
    fname = (doc.file_name or "").lower()
    mime  = (doc.mime_type or "").lower()
    is_text = (fname.endswith(".txt") or fname.endswith(".csv")
               or "text" in mime or mime == "application/octet-stream")
    if not is_text:
        await update.message.reply_text(
            "⚠️ Please send a <code>.txt</code> or <code>.csv</code> file.",
            parse_mode="HTML",
        )
        return

    # Size guard — reject files over 5 MB to avoid memory issues.
    if doc.file_size and doc.file_size > 5 * 1024 * 1024:
        await update.message.reply_text("⚠️ File too large (max 5 MB).")
        return

    caption = (update.message.caption or "").strip().lower()

    # 1. Admin used /upload LIST_ID and is now sending the file.
    if context.user_data.get("adm_awaiting") == "upload_file":
        subl_id = context.user_data.pop("adm_upload_subl", "")
        context.user_data["adm_awaiting"] = None
        await _run_upload(update.message, subl_id, doc.file_id, context)
        return

    # 2. Caption matches a list ID directly — e.g. file sent with caption "dd-28th".
    subl_id = _find_subl_by_name(caption)
    if subl_id:
        await _run_upload(update.message, subl_id, doc.file_id, context)
        return

    # 3. No hint — store file_id and show list picker.
    context.user_data["adm_pending_file_id"] = doc.file_id
    await update.message.reply_text(
        "📂 File received.\n\n"
        "Which list should this be imported into?\n"
        "<i>Tip: next time add the list ID as the file caption to skip this step.</i>",
        reply_markup=upload_list_picker_kb(),
        parse_mode="HTML",
    )


async def _do_broadcast(reply_fn, bot, msg: str) -> None:
    user_ids = await db.get_all_user_ids()
    ok, fail = 0, 0
    for uid in user_ids:
        try:
            await bot.send_message(uid, msg, parse_mode="HTML")
            ok += 1
        except (Forbidden, BadRequest):
            fail += 1
        except Exception:
            fail += 1
    await reply_fn(
        f"📢 Broadcast complete.\n✅ Sent: {ok}   ❌ Failed: {fail}",
    )
    await channel_log.broadcast_sent(0, ok, fail)  # admin_id not available here


# ============================================================
#  Register all handlers with the Application
# ============================================================
def register_admin_handlers(app: Application) -> None:
    # Commands
    app.add_handler(CommandHandler("admin",     cmd_admin))
    app.add_handler(CommandHandler("credit",    cmd_credit))
    app.add_handler(CommandHandler("deduct",    cmd_deduct))
    app.add_handler(CommandHandler("userinfo",  cmd_userinfo))
    app.add_handler(CommandHandler("broadcast", cmd_broadcast))
    app.add_handler(CommandHandler("upload",    cmd_upload))
    app.add_handler(CommandHandler("rename",    cmd_rename))

    # All adm_ callbacks — must run BEFORE the general on_button handler
    app.add_handler(CallbackQueryHandler(
        adm_button, pattern=r"^adm_"
    ))

    # File uploads from admin — group 1 so it never blocks user messages in group 0
    app.add_handler(MessageHandler(
        filters.Document.ALL & filters.UpdateType.MESSAGE,
        adm_document,
    ), group=1)

    # NOTE: admin TEXT input is NOT registered here.
    # bot.py's on_text() already routes to adm_text() when adm_awaiting is set.
    # Registering it here caused ALL user text messages to be silently swallowed.
