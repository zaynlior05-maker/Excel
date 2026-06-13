"""
Central settings. Edit defaults here or set as Variables in Railway.
Railway values always win over the defaults written below.
"""

import os

# ---- Secrets (set in Railway, never in GitHub) ----
BOT_TOKEN    = os.environ.get("BOT_TOKEN", "")
DATABASE_URL = os.environ.get("DATABASE_URL", "")   # auto-set by Railway Postgres

# ---- Admin access ----
# Set ADMIN_IDS in Railway as comma-separated Telegram user IDs.
# Find your ID by messaging @userinfobot in Telegram.
_raw_ids = os.environ.get("ADMIN_IDS", "")
ADMIN_IDS: set[int] = {int(x.strip()) for x in _raw_ids.split(",") if x.strip().isdigit()}

# ---- Admin password ----
# Anyone (including you) can type /admin then this password to get access.
# Set ADMIN_PASSWORD in Railway Variables. Sessions last 2 hours.
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "")

# ---- Log channel ----
# Every bot event is sent here.
# Steps: create a Telegram channel → add your bot as Admin → set this to
# the channel's @username or numeric ID (e.g. -1001234567890).
# Leave blank to disable.
LOG_CHANNEL_ID = os.environ.get("LOG_CHANNEL_ID", "")

# ---- Public-facing links ----
SUPPORT_HANDLE = os.environ.get("SUPPORT_HANDLE", "@YourSupportHandle")
SUPPORT_URL    = os.environ.get("SUPPORT_URL",    "https://t.me/YourSupportHandle")
CHANNEL_URL    = os.environ.get("CHANNEL_URL",    "https://t.me/YourChannel")

# ---- Currency ----
CURRENCY_SYMBOL = os.environ.get("CURRENCY_SYMBOL", "£")

# ---- Top-up preset buttons ----
TOPUP_PRESETS = [50, 100, 150, 200, 250, 300, 350, 400, 450, 500, 750, 1000]

# Minimum top-up amount
TOPUP_MIN = 50

# ---- Items per page in store lists ----
ITEMS_PER_PAGE = 8

# ---- Your wallet addresses ----
# Users send crypto directly to these. Set them as Railway Variables.
# Leave a value blank ("") to hide that coin from the top-up menu.
WALLET_ADDRESSES = {
    "USDT (TRC20)": os.environ.get("WALLET_USDT_TRC20", ""),
    "USDT (ERC20)": os.environ.get("WALLET_USDT_ERC20", ""),
    "BTC":          os.environ.get("WALLET_BTC",        ""),
    "ETH":          os.environ.get("WALLET_ETH",        ""),
    "LTC":          os.environ.get("WALLET_LTC",        ""),
}

# ---- Payment instructions shown under the wallet address ----
PAYMENT_NOTE = os.environ.get(
    "PAYMENT_NOTE",
    "Send the exact £ value in your chosen coin.\n"
    "After sending, tap the button below and provide your Transaction ID.",
)

# ---- How quickly you promise to review (shown to user after they submit) ----
REVIEW_TIME = os.environ.get("REVIEW_TIME", "within 30 minutes")

# ---- Store categories ----
# Add categories here. "id" = short internal code, no spaces.
# Each category can have "sublists".
# Edit this and Railway redeploys automatically.
CATEGORIES = [
    {
        "id": "ff",
        "label": "🗓️ FF",
        "sublists": [
            {"id": "dd-28th",  "label": "🔸 DD-28th"},
            {"id": "dd-4th",   "label": "🔸 DD-4th"},
            {"id": "dd-7th",   "label": "🔸 DD-7th"},
            {"id": "5-base",   "label": "🔸 5-base"},

        ],
    },
]

# ---- Stock (seed data) ----
# These seed the database on first boot if it's empty.
# Manage stock live via the admin panel — edits here only apply on a fresh DB.
ITEMS = {
    "dd-28th": [
        {"id": "l1", "bin": "459667", "year": "2012", "code": "Ex3", "price": 5,
         "content": "DELIVERED CONTENT GOES HERE"},
        {"id": "l2", "bin": "446238", "year": "2009", "code": "SK1", "price": 5,
         "content": "DELIVERED CONTENT GOES HERE"},
    ],
    "dd-4th":   [],
    "dd-7th":   [],
    "5-base":   [],
    "10-pound": [],
}

# ---- Rules ----
RULES_TEXT = (
    "🛡️ <b>STORE RULES & REFUND POLICY</b>\n\n"
    "⚠️ <b>IF YOU FAIL TO FOLLOW OUR CLEAR INSTRUCTED RULES YOU WILL NOT BE REFUNDED.</b>\n\n"
    "ℹ️ <b>How to Apply for a Refund:</b>\n"
    "1. Check card on pay.google.com\n"
    "2. If the card is dead, click refund at the bottom of purchased card.\n"
    "3. Send the bot a Screenshot/Photo that proves the card is dead.\n"
    "4. When checking card on pay.google.com, you have an automatic 3 minute timer.\n"
    "5. Failing to check card / provide proof of card being dead past the 3 minute timer can result in no refund.\n"
    "6. When providing a photo or a screenshot, please make sure: Card Number, Expiry Date and CVV are fully visible.\n"
    "7. If number doesn't call or is invalid this doesn't qualify for refund unless all info is missing or fake.\n"
    "8. If all the details are valid and the card is dead your account will be credited again with a refund within 5 minutes.\n\n"
    "💡 <b>Keep in Mind:</b>\n"
    "• <b>£10 & £5 BASES ARE NOT REFUNDABLE</b>\n"
    "• <b>HSBC CARDS ARE NOT REFUNDABLE</b> (Or ANY company under them such as John Lewis, M&S, First Direct, etc.)\n\n"
    "⛔️ <b>NOTE</b> ⛔️\n"
    "🔹 Support account is available 24/7 @EXCELV1.\n"
    "🔹 1 Transaction per wallet unless payment is underpaid. Our wallet always changes after each completed deposit.\n"
    "🔹 Payment: <b>CRYPTO ONLY</b>\n\n"
    "<b>BY PURCHASING YOU AGREE TO THESE RULES. FAILURE TO READ THEM WILL FORFEIT YOUR REFUND / REPLACEMENT. WE SHALL GIVE NO WARNINGS.</b>"
)

# ---- Refund Policy shown on every /start ----
# This is displayed as the FIRST message every time a user sends /start.
# Edit the text between the quotes. Keep \n for line breaks, \n\n for blank lines.
# <b>text</b> = bold   <i>text</i> = italic
REFUND_RULES_TEXT = (
    "🛡️ <b>STORE RULES & REFUND POLICY</b>\n\n"
    "⚠️ <b>IF YOU FAIL TO FOLLOW OUR CLEAR INSTRUCTED RULES YOU WILL NOT BE REFUNDED.</b>\n\n"
    "ℹ️ <b>How to Apply for a Refund:</b>\n"
    "1. Check card on pay.google.com\n"
    "2. If the card is dead, click refund at the bottom of purchased card.\n"
    "3. Send the bot a Screenshot/Photo that proves the card is dead.\n"
    "4. When checking card on pay.google.com, you have an automatic 3 minute timer.\n"
    "5. Failing to check card / provide proof of card being dead past the 3 minute timer can result in no refund.\n"
    "6. When providing a photo or a screenshot, please make sure: Card Number, Expiry Date and CVV are fully visible.\n"
    "7. If number doesn't call or is invalid this doesn't qualify for refund unless all info is missing or fake.\n"
    "8. If all the details are valid and the card is dead your account will be credited again with a refund within 5 minutes.\n\n"
    "💡 <b>Keep in Mind:</b>\n"
    "• <b>£10 & £5 BASES ARE NOT REFUNDABLE</b>\n"
    "• <b>HSBC CARDS ARE NOT REFUNDABLE</b> (Or ANY company under them such as John Lewis, M&S, First Direct, etc.)\n\n"
    "⛔️ <b>NOTE</b> ⛔️\n"
    "🔹 Support account is available 24/7 @EXCELV1.\n"
    "🔹 1 Transaction per wallet unless payment is underpaid. Our wallet always changes after each completed deposit.\n"
    "🔹 Payment: <b>CRYPTO ONLY</b>\n\n"
    "<b>BY PURCHASING YOU AGREE TO THESE RULES. FAILURE TO READ THEM WILL FORFEIT YOUR REFUND / REPLACEMENT. WE SHALL GIVE NO WARNINGS.</b>"
)

# ---- Renameable labels (auto-built — do not edit) ----
def _build_renameable() -> dict[str, str]:
    r = {
        "menu:store":   "\U0001F6D2 Store",
        "menu:wallet":  "\U0001F4B5 Wallet",
        "menu:rules":   "\U0001F6E1\uFE0F Rules",
        "menu:support": "\u260E\uFE0F Support \u2197",
        "menu:channel": "\U0001F4C4 Channel \u2197",
    }
    for cat in CATEGORIES:
        r[f"cat:{cat['id']}"] = cat["label"]
        for subl in cat.get("sublists", []):
            r[f"subl:{subl['id']}"] = subl["label"]
    return r

RENAMEABLE: dict[str, str] = _build_renameable()

def default_label(key: str) -> str:
    return RENAMEABLE.get(key, key)
