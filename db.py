"""
Database layer (Postgres via Railway).

Tables:
  users    -> one row per Telegram user, holds balance + ban status
  payments -> one row per top-up, used to credit safely (no double credit)
  stock    -> every line item, managed live via admin panel
  orders   -> record of every purchase
  labels   -> display-name overrides for any button/category/sublist
  sublists -> dynamic sublist/base registry (add/remove via admin panel)
"""

import uuid
from decimal import Decimal

import asyncpg

import config

_pool: asyncpg.Pool | None = None

# In-memory label cache
_label_cache: dict[str, str] = {}

# In-memory sublist cache: cat_id -> list of {id, cat_id, label}
_sublist_cache: dict[str, list[dict]] = {}


# ============================================================
#  Init
# ============================================================
async def init() -> None:
    global _pool
    _pool = await asyncpg.create_pool(config.DATABASE_URL, min_size=1, max_size=5)
    async with _pool.acquire() as con:
        await con.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id    BIGINT PRIMARY KEY,
                username   TEXT NOT NULL DEFAULT '',
                balance    NUMERIC(18,2) NOT NULL DEFAULT 0,
                banned     BOOLEAN NOT NULL DEFAULT FALSE,
                joined_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
            CREATE TABLE IF NOT EXISTS payments (
                payment_id  TEXT PRIMARY KEY,
                user_id     BIGINT NOT NULL,
                amount      NUMERIC(18,2) NOT NULL,
                coin        TEXT NOT NULL DEFAULT '',
                tx_ref      TEXT NOT NULL DEFAULT '',
                status      TEXT NOT NULL DEFAULT 'pending',
                credited    BOOLEAN NOT NULL DEFAULT FALSE,
                created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
            CREATE TABLE IF NOT EXISTS stock (
                id         TEXT PRIMARY KEY,
                subl_id    TEXT NOT NULL,
                bin        TEXT NOT NULL,
                year       TEXT NOT NULL,
                code       TEXT NOT NULL,
                price      NUMERIC(10,2) NOT NULL,
                content    TEXT NOT NULL,
                sold       BOOLEAN NOT NULL DEFAULT FALSE,
                added_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
            CREATE TABLE IF NOT EXISTS orders (
                id         TEXT PRIMARY KEY,
                user_id    BIGINT NOT NULL,
                item_id    TEXT NOT NULL,
                subl_id    TEXT NOT NULL,
                amount     NUMERIC(10,2) NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
            CREATE TABLE IF NOT EXISTS labels (
                key        TEXT PRIMARY KEY,
                value      TEXT NOT NULL,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
            CREATE TABLE IF NOT EXISTS sublists (
                id         TEXT PRIMARY KEY,
                cat_id     TEXT NOT NULL DEFAULT 'ff',
                label      TEXT NOT NULL,
                sort_order INTEGER NOT NULL DEFAULT 0,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
        """)
    await _seed_stock()
    await _load_label_cache()
    await _seed_sublists()
    await _refresh_sublist_cache()
    # Add username column if this is an existing database that predates it
    async with _pool.acquire() as con:
        await con.execute(
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS username TEXT NOT NULL DEFAULT ''"
        )


# ============================================================
#  Label cache
# ============================================================
async def _seed_sublists() -> None:
    """Populate sublists table from config.CATEGORIES if it's empty."""
    async with _pool.acquire() as con:
        count = await con.fetchval("SELECT COUNT(*) FROM sublists")
        if count == 0:
            for cat in config.CATEGORIES:
                for i, subl in enumerate(cat.get("sublists", [])):
                    await con.execute(
                        "INSERT INTO sublists(id,cat_id,label,sort_order) "
                        "VALUES($1,$2,$3,$4) ON CONFLICT DO NOTHING",
                        subl["id"], cat["id"], subl["label"], i,
                    )


async def _refresh_sublist_cache() -> None:
    global _sublist_cache
    async with _pool.acquire() as con:
        rows = await con.fetch(
            "SELECT id,cat_id,label FROM sublists ORDER BY cat_id,sort_order,id"
        )
    cache: dict[str, list[dict]] = {}
    for r in rows:
        cat_id = r["cat_id"]
        cache.setdefault(cat_id, []).append(
            {"id": r["id"], "cat_id": r["cat_id"], "label": r["label"]}
        )
    _sublist_cache = cache


def get_sublists(cat_id: str) -> list[dict]:
    """Synchronous — reads from in-memory cache."""
    return list(_sublist_cache.get(cat_id, []))


def get_all_sublists() -> list[dict]:
    """All sublists across all categories, flat list."""
    result = []
    for items in _sublist_cache.values():
        result.extend(items)
    return result


def find_sublist_by_id(subl_id: str) -> dict | None:
    """Find a sublist dict by its ID regardless of category."""
    for items in _sublist_cache.values():
        for s in items:
            if s["id"] == subl_id:
                return s
    return None


async def move_sublist(subl_id: str, direction: int) -> None:
    """Move a sublist up (direction=-1) or down (direction=+1) within its category."""
    async with _pool.acquire() as con:
        row = await con.fetchrow(
            "SELECT id, cat_id, sort_order FROM sublists WHERE id=$1", subl_id
        )
        if not row:
            return
        cat_id, cur_order = row["cat_id"], row["sort_order"]
        if direction < 0:
            adjacent = await con.fetchrow(
                "SELECT id, sort_order FROM sublists "
                "WHERE cat_id=$1 AND sort_order < $2 ORDER BY sort_order DESC LIMIT 1",
                cat_id, cur_order,
            )
        else:
            adjacent = await con.fetchrow(
                "SELECT id, sort_order FROM sublists "
                "WHERE cat_id=$1 AND sort_order > $2 ORDER BY sort_order ASC LIMIT 1",
                cat_id, cur_order,
            )
        if not adjacent:
            return
        await con.execute(
            "UPDATE sublists SET sort_order=$1 WHERE id=$2",
            adjacent["sort_order"], subl_id,
        )
        await con.execute(
            "UPDATE sublists SET sort_order=$1 WHERE id=$2",
            cur_order, adjacent["id"],
        )
    await _refresh_sublist_cache()


async def add_sublist(subl_id: str, cat_id: str, label: str) -> bool:
    """Add a new sublist. Returns False if ID already exists."""
    try:
        async with _pool.acquire() as con:
            max_order = await con.fetchval(
                "SELECT COALESCE(MAX(sort_order),0) FROM sublists WHERE cat_id=$1", cat_id
            )
            await con.execute(
                "INSERT INTO sublists(id,cat_id,label,sort_order) VALUES($1,$2,$3,$4)",
                subl_id, cat_id, label, (max_order or 0) + 1,
            )
        await _refresh_sublist_cache()
        return True
    except Exception:
        return False


async def remove_sublist(subl_id: str) -> int:
    """Delete a sublist and ALL its stock. Returns number of stock items deleted."""
    async with _pool.acquire() as con:
        result = await con.execute("DELETE FROM stock WHERE subl_id=$1", subl_id)
        stock_deleted = int(result.split()[-1])
        await con.execute("DELETE FROM sublists WHERE id=$1", subl_id)
    await _refresh_sublist_cache()
    return stock_deleted


async def get_stock_counts() -> dict[str, int]:
    """Return {subl_id: unsold_count} for all sublists."""
    async with _pool.acquire() as con:
        rows = await con.fetch(
            "SELECT subl_id, COUNT(*) AS cnt FROM stock "
            "WHERE sold=FALSE GROUP BY subl_id"
        )
    return {r["subl_id"]: r["cnt"] for r in rows}


async def _load_label_cache() -> None:
    """Read all label overrides from DB into the in-memory cache."""
    global _label_cache
    async with _pool.acquire() as con:
        rows = await con.fetch("SELECT key, value FROM labels")
        _label_cache = {r["key"]: r["value"] for r in rows}


def get_label(key: str, default: str = "") -> str:
    """Synchronous — reads the in-memory cache. Falls back to default."""
    return _label_cache.get(key, default)


async def set_label(key: str, value: str) -> None:
    """Persist a label override and refresh the cache entry immediately."""
    async with _pool.acquire() as con:
        await con.execute(
            "INSERT INTO labels(key, value) VALUES($1, $2) "
            "ON CONFLICT(key) DO UPDATE SET value=EXCLUDED.value, "
            "updated_at=NOW()",
            key, value,
        )
    _label_cache[key] = value


async def reset_label(key: str) -> None:
    """Delete a label override (reverts to hard-coded default)."""
    async with _pool.acquire() as con:
        await con.execute("DELETE FROM labels WHERE key=$1", key)
    _label_cache.pop(key, None)


async def get_all_label_overrides() -> dict[str, str]:
    """Return every key that currently has an override stored in the DB."""
    async with _pool.acquire() as con:
        rows = await con.fetch(
            "SELECT key, value FROM labels ORDER BY key"
        )
        return {r["key"]: r["value"] for r in rows}


async def _seed_stock() -> None:
    async with _pool.acquire() as con:
        count = await con.fetchval("SELECT COUNT(*) FROM stock")
        if count > 0:
            return
        rows = []
        for subl_id, items in config.ITEMS.items():
            for it in items:
                rows.append((
                    it["id"], subl_id, it["bin"], it["year"],
                    it["code"], Decimal(str(it["price"])), it["content"],
                ))
        if rows:
            await con.executemany(
                "INSERT INTO stock(id,subl_id,bin,year,code,price,content) "
                "VALUES($1,$2,$3,$4,$5,$6,$7) ON CONFLICT DO NOTHING",
                rows,
            )


# ============================================================
#  Users
# ============================================================
async def ensure_user(user_id: int, username: str = "") -> None:
    """Create user if new, always update username so it stays current."""
    async with _pool.acquire() as con:
        await con.execute(
            "INSERT INTO users(user_id, username) VALUES($1, $2) "
            "ON CONFLICT(user_id) DO UPDATE SET username = EXCLUDED.username",
            user_id, username or "",
        )


async def get_username(user_id: int) -> str:
    """Return stored @username for a user, or empty string if unknown."""
    async with _pool.acquire() as con:
        row = await con.fetchrow(
            "SELECT username FROM users WHERE user_id=$1", user_id
        )
        return (row["username"] or "") if row else ""


async def get_balance(user_id: int) -> Decimal:
    async with _pool.acquire() as con:
        row = await con.fetchrow("SELECT balance FROM users WHERE user_id=$1", user_id)
        return row["balance"] if row else Decimal("0")


async def is_banned(user_id: int) -> bool:
    async with _pool.acquire() as con:
        row = await con.fetchrow("SELECT banned FROM users WHERE user_id=$1", user_id)
        return bool(row["banned"]) if row else False


async def set_banned(user_id: int, banned: bool) -> None:
    async with _pool.acquire() as con:
        await con.execute(
            "UPDATE users SET banned=$2 WHERE user_id=$1", user_id, banned
        )


async def get_user_info(user_id: int) -> dict | None:
    async with _pool.acquire() as con:
        row = await con.fetchrow(
            "SELECT user_id, username, balance, banned, joined_at FROM users WHERE user_id=$1",
            user_id,
        )
        if not row:
            return None
        orders = await con.fetchval(
            "SELECT COUNT(*) FROM orders WHERE user_id=$1", user_id
        )
        spent = await con.fetchval(
            "SELECT COALESCE(SUM(amount),0) FROM orders WHERE user_id=$1", user_id
        )
        return {
            "user_id":  row["user_id"],
            "username": row["username"] or "",
            "balance":  row["balance"],
            "banned":   row["banned"],
            "joined":   row["joined_at"],
            "orders":   orders,
            "spent":    spent,
        }


async def adjust_balance(user_id: int, delta: Decimal) -> Decimal:
    """Add (positive) or deduct (negative) balance. Returns new balance."""
    async with _pool.acquire() as con:
        row = await con.fetchrow(
            "UPDATE users SET balance = GREATEST(0, balance + $2) "
            "WHERE user_id=$1 RETURNING balance",
            user_id, delta,
        )
        return row["balance"] if row else Decimal("0")


async def get_all_user_ids() -> list[int]:
    async with _pool.acquire() as con:
        rows = await con.fetch("SELECT user_id FROM users WHERE banned=FALSE")
        return [r["user_id"] for r in rows]


# ============================================================
#  Payments
# ============================================================
async def record_payment(payment_id: str, user_id: int,
                         amount: Decimal, coin: str) -> None:
    """Store a new pending manual top-up."""
    async with _pool.acquire() as con:
        await con.execute(
            "INSERT INTO payments(payment_id,user_id,amount,coin,status) "
            "VALUES($1,$2,$3,$4,'pending') ON CONFLICT DO NOTHING",
            payment_id, user_id, amount, coin,
        )


async def submit_proof(payment_id: str, tx_ref: str) -> bool:
    """Attach the user's TX ID / screenshot reference to the payment."""
    async with _pool.acquire() as con:
        result = await con.execute(
            "UPDATE payments SET tx_ref=$2, status='submitted' "
            "WHERE payment_id=$1 AND status='pending'",
            payment_id, tx_ref,
        )
        return result == "UPDATE 1"


async def approve_payment(payment_id: str) -> dict | None:
    """
    Approve a payment: credit the user's balance exactly once.
    Returns {"user_id": ..., "amount": ...} or None if already handled.
    """
    async with _pool.acquire() as con:
        async with con.transaction():
            row = await con.fetchrow(
                "UPDATE payments SET status='approved', credited=TRUE "
                "WHERE payment_id=$1 AND credited=FALSE "
                "RETURNING user_id, amount",
                payment_id,
            )
            if not row:
                return None
            await con.execute(
                "INSERT INTO users(user_id,balance) VALUES($1,$2) "
                "ON CONFLICT(user_id) DO UPDATE "
                "SET balance = users.balance + EXCLUDED.balance",
                row["user_id"], row["amount"],
            )
            return {"user_id": row["user_id"], "amount": row["amount"]}


async def reject_payment(payment_id: str) -> dict | None:
    """Mark a payment as rejected. Returns row or None if already handled."""
    async with _pool.acquire() as con:
        row = await con.fetchrow(
            "UPDATE payments SET status='rejected' "
            "WHERE payment_id=$1 AND status IN ('pending','submitted') "
            "RETURNING user_id, amount, coin",
            payment_id,
        )
        return dict(row) if row else None


async def get_pending_payments() -> list[dict]:
    """All payments awaiting admin review (pending or submitted)."""
    async with _pool.acquire() as con:
        rows = await con.fetch(
            "SELECT payment_id, user_id, amount, coin, tx_ref, status, created_at "
            "FROM payments WHERE status IN ('pending','submitted') "
            "ORDER BY created_at ASC"
        )
        return [dict(r) for r in rows]


async def get_payment(payment_id: str) -> dict | None:
    async with _pool.acquire() as con:
        row = await con.fetchrow(
            "SELECT payment_id, user_id, amount, coin, tx_ref, status, created_at "
            "FROM payments WHERE payment_id=$1",
            payment_id,
        )
        return dict(row) if row else None


# ============================================================
#  Stock
# ============================================================
async def get_stock(subl_id: str) -> list[dict]:
    async with _pool.acquire() as con:
        rows = await con.fetch(
            "SELECT id,subl_id,bin,year,code,price,content "
            "FROM stock WHERE subl_id=$1 AND sold=FALSE "
            "ORDER BY bin::bigint ASC, added_at ASC",
            subl_id,
        )
        return [dict(r) for r in rows]


async def get_stock_counts() -> dict[str, int]:
    async with _pool.acquire() as con:
        rows = await con.fetch(
            "SELECT subl_id, COUNT(*) AS n FROM stock "
            "WHERE sold=FALSE GROUP BY subl_id"
        )
        return {r["subl_id"]: r["n"] for r in rows}


async def add_stock_item(subl_id: str, bin_: str, year: str,
                         code: str, price: Decimal, content: str) -> str:
    item_id = uuid.uuid4().hex[:8]
    async with _pool.acquire() as con:
        await con.execute(
            "INSERT INTO stock(id,subl_id,bin,year,code,price,content) "
            "VALUES($1,$2,$3,$4,$5,$6,$7)",
            item_id, subl_id, bin_, year, code, price, content,
        )
    return item_id


async def bulk_add_stock_items(rows: list[tuple]) -> dict:
    """
    Insert many items in a single transaction.
    rows = list of (id, subl_id, bin, year, code, price, content)
    Returns {"inserted": N, "duplicate": N}
    """
    if not rows:
        return {"inserted": 0, "duplicate": 0}
    async with _pool.acquire() as con:
        async with con.transaction():
            inserted = 0
            duplicate = 0
            for row in rows:
                result = await con.execute(
                    "INSERT INTO stock(id,subl_id,bin,year,code,price,content) "
                    "VALUES($1,$2,$3,$4,$5,$6,$7) ON CONFLICT(id) DO NOTHING",
                    *row,
                )
                if result == "INSERT 0 1":
                    inserted += 1
                else:
                    duplicate += 1
    return {"inserted": inserted, "duplicate": duplicate}


async def remove_stock_item(item_id: str) -> bool:
    async with _pool.acquire() as con:
        result = await con.execute(
            "DELETE FROM stock WHERE id=$1 AND sold=FALSE", item_id
        )
        return result == "DELETE 1"


async def get_sublist_price(subl_id: str) -> Decimal | None:
    """Return the current price of unsold items in a list, or None if empty."""
    async with _pool.acquire() as con:
        row = await con.fetchrow(
            "SELECT price FROM stock WHERE subl_id=$1 AND sold=FALSE LIMIT 1",
            subl_id,
        )
        return row["price"] if row else None


async def set_global_price(price: Decimal) -> int:
    """Set price for ALL unsold items across every list. Returns count updated."""
    async with _pool.acquire() as con:
        result = await con.execute(
            "UPDATE stock SET price=$1 WHERE sold=FALSE", price
        )
        return int(result.split()[-1])


async def set_bin_price(bin_: str, price: Decimal) -> int:
    """Set price for all unsold items with a specific BIN. Returns count updated."""
    async with _pool.acquire() as con:
        result = await con.execute(
            "UPDATE stock SET price=$2 WHERE bin=$1 AND sold=FALSE", bin_, price
        )
        return int(result.split()[-1])


async def set_item_price(item_id: str, price: Decimal) -> bool:
    """Set price for a single item. Returns True if updated."""
    async with _pool.acquire() as con:
        result = await con.execute(
            "UPDATE stock SET price=$2 WHERE id=$1 AND sold=FALSE", item_id, price
        )
        return int(result.split()[-1]) > 0


async def set_sublist_price(subl_id: str, price: Decimal) -> int:
    """Update price of all unsold items in a sublist. Returns count updated."""
    async with _pool.acquire() as con:
        result = await con.execute(
            "UPDATE stock SET price=$2 WHERE subl_id=$1 AND sold=FALSE",
            subl_id, price,
        )
        return int(result.split()[-1])


async def get_stock_item(item_id: str) -> dict | None:
    async with _pool.acquire() as con:
        row = await con.fetchrow(
            "SELECT id,subl_id,bin,year,code,price,content,sold FROM stock WHERE id=$1",
            item_id,
        )
        return dict(row) if row else None


async def purchase_item(user_id: int, item_id: str) -> dict:
    """
    Attempt to buy an item. Returns a result dict:
      {"status": "success",       "content": ..., "price": ..., "new_balance": ...}
      {"status": "not_available"}   item sold or missing
      {"status": "insufficient",  "balance": ..., "price": ..., "shortfall": ...}
    """
    async with _pool.acquire() as con:
        async with con.transaction():
            item = await con.fetchrow(
                "SELECT id,subl_id,bin,year,code,price,content "
                "FROM stock WHERE id=$1 AND sold=FALSE FOR UPDATE",
                item_id,
            )
            if not item:
                return {"status": "not_available"}

            user = await con.fetchrow(
                "SELECT balance FROM users WHERE user_id=$1", user_id
            )
            balance = user["balance"] if user else Decimal("0")
            price   = item["price"]

            if balance < price:
                return {
                    "status":   "insufficient",
                    "balance":  balance,
                    "price":    price,
                    "shortfall": price - balance,
                }

            # Deduct balance and mark sold
            await con.execute(
                "UPDATE users SET balance = balance - $2 WHERE user_id=$1",
                user_id, price,
            )
            await con.execute(
                "UPDATE stock SET sold=TRUE WHERE id=$1", item_id
            )

            # Record order
            order_id = uuid.uuid4().hex[:12]
            await con.execute(
                "INSERT INTO orders(id,user_id,item_id,subl_id,amount) "
                "VALUES($1,$2,$3,$4,$5)",
                order_id, user_id, item_id, item["subl_id"], price,
            )

            new_bal = await con.fetchval(
                "SELECT balance FROM users WHERE user_id=$1", user_id
            )
            return {
                "status":      "success",
                "content":     item["content"],
                "price":       price,
                "new_balance": new_bal,
            }


# ============================================================
#  Orders
# ============================================================
async def get_recent_orders(limit: int = 20) -> list[dict]:
    async with _pool.acquire() as con:
        rows = await con.fetch(
            "SELECT o.id, o.user_id, o.subl_id, o.amount, o.created_at, "
            "s.bin, s.year, s.code "
            "FROM orders o LEFT JOIN stock s ON s.id=o.item_id "
            "ORDER BY o.created_at DESC LIMIT $1",
            limit,
        )
        return [dict(r) for r in rows]


async def get_user_orders(user_id: int, limit: int = 10) -> list[dict]:
    async with _pool.acquire() as con:
        rows = await con.fetch(
            "SELECT o.id, o.subl_id, o.amount, o.created_at, "
            "s.bin, s.year, s.code "
            "FROM orders o LEFT JOIN stock s ON s.id=o.item_id "
            "WHERE o.user_id=$1 ORDER BY o.created_at DESC LIMIT $2",
            user_id, limit,
        )
        return [dict(r) for r in rows]


# ============================================================
#  Admin stats
# ============================================================
async def get_stats() -> dict:
    async with _pool.acquire() as con:
        total_users   = await con.fetchval("SELECT COUNT(*) FROM users")
        banned_users  = await con.fetchval("SELECT COUNT(*) FROM users WHERE banned=TRUE")
        total_stock   = await con.fetchval("SELECT COUNT(*) FROM stock WHERE sold=FALSE")
        sold_stock    = await con.fetchval("SELECT COUNT(*) FROM stock WHERE sold=TRUE")
        total_revenue = await con.fetchval(
            "SELECT COALESCE(SUM(amount),0) FROM payments WHERE credited=TRUE"
        )
        pending_pays  = await con.fetchval(
            "SELECT COUNT(*) FROM payments WHERE credited=FALSE"
        )
        total_orders  = await con.fetchval("SELECT COUNT(*) FROM orders")
        return {
            "total_users":   total_users,
            "banned_users":  banned_users,
            "total_stock":   total_stock,
            "sold_stock":    sold_stock,
            "total_revenue": total_revenue,
            "pending_pays":  pending_pays,
            "total_orders":  total_orders,
        }
