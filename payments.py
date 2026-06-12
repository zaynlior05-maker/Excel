"""
Manual payment handling — no external API.
Users send crypto directly to your wallet addresses (set in config.py).
Exchange rates fetched live from CoinGecko (free, no API key needed).
"""

import time
import uuid
import httpx

# Map button label → CoinGecko coin ID
_GECKO_IDS = {
    "BTC":          "bitcoin",
    "USDT (TRC20)": "tether",
    "USDT (ERC20)": "tether",
    "ETH":          "ethereum",
    "LTC":          "litecoin",
}

# Simple in-memory rate cache — refresh every 5 minutes
_rate_cache: dict = {}
_rate_ts: float   = 0.0
_CACHE_TTL        = 300   # seconds


def new_payment_id() -> str:
    return uuid.uuid4().hex[:12]


def active_coins() -> dict[str, str]:
    """Return only coins that have a wallet address configured."""
    import config
    return {name: addr for name, addr in config.WALLET_ADDRESSES.items() if addr.strip()}


async def get_rates_gbp() -> dict[str, float]:
    """
    Returns {coin_name: price_in_gbp} for all active coins.
    Uses cached value if fresh enough.
    Falls back to empty dict on error (caller shows manual note).
    """
    global _rate_cache, _rate_ts
    if time.time() - _rate_ts < _CACHE_TTL and _rate_cache:
        return _rate_cache

    coins = active_coins()
    ids   = list({_GECKO_IDS[c] for c in coins if c in _GECKO_IDS})
    if not ids:
        return {}

    url = (
        "https://api.coingecko.com/api/v3/simple/price"
        f"?ids={','.join(ids)}&vs_currencies=gbp"
    )
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            resp = await client.get(url)
        resp.raise_for_status()
        data = resp.json()
        rates = {}
        for name in coins:
            gecko_id = _GECKO_IDS.get(name)
            if gecko_id and gecko_id in data:
                rates[name] = float(data[gecko_id]["gbp"])
        _rate_cache = rates
        _rate_ts    = time.time()
        return rates
    except Exception:
        return _rate_cache or {}   # return last known or empty


def format_crypto_amount(gbp_amount: float, rate_gbp: float, coin: str) -> str:
    """
    Given £amount and the coin's GBP price, return a human-readable string.
    e.g.  '≈ 0.00125 BTC'  or  '≈ 127.85 USDT'
    """
    if not rate_gbp:
        return ""
    crypto = gbp_amount / rate_gbp
    coin_short = coin.split("(")[0].strip()   # "USDT (TRC20)" → "USDT"

    if crypto < 0.001:
        return f"≈ {crypto:.8f} {coin_short}"
    elif crypto < 1:
        return f"≈ {crypto:.6f} {coin_short}"
    else:
        return f"≈ {crypto:.2f} {coin_short}"
