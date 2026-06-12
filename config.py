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
# Example: ADMIN_IDS=123456789,987654321
_raw_ids = os.environ.get("ADMIN_IDS", "")
ADMIN_IDS: set[int] = {int(x.strip()) for x in _raw_ids.split(",") if x.strip().isdigit()}

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
            {"id": "10-pound", "label": "🔸 10-pound"},
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
    "🛡️ <b>STORE RULES</b>\n\n"
    "1. All sales are final unless a replacement is agreed in advance.\n"
    "2. Refunds / replacements are only valid if you followed these rules.\n"
    "3. Do not share, resell, or redistribute purchased items.\n"
    "4. Be respectful to support staff.\n"
    "5. By purchasing you confirm you have read and accepted these rules.\n\n"
    "<i>Edit this text in config.py (RULES_TEXT).</i>"
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
