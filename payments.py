"""
Manual payment handling — no external API.

Users send crypto directly to your wallet addresses (set in config.py),
then submit a Transaction ID or screenshot as proof.
Admin approves or rejects via the admin panel.
"""

import uuid


def new_payment_id() -> str:
    """Generate a short unique ID for a pending payment."""
    return uuid.uuid4().hex[:12]


def active_coins() -> dict[str, str]:
    """Return only coins that have a wallet address configured."""
    import config
    return {name: addr for name, addr in config.WALLET_ADDRESSES.items() if addr.strip()}
