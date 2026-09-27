from __future__ import annotations

import json
import secrets
import sqlite3
import threading
from contextlib import contextmanager, nullcontext
from pathlib import Path

from app.models import Account, Location, Order, Product, ReturnRequest, StockError, StockRow

SCHEMA = """
CREATE TABLE IF NOT EXISTS products (
  id TEXT PRIMARY KEY,
  sku TEXT NOT NULL UNIQUE,
  product_name TEXT NOT NULL,
  brand TEXT NOT NULL DEFAULT '',
  category TEXT NOT NULL DEFAULT '',
  barcode TEXT NOT NULL DEFAULT '',
  alt_names TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS locations (
  id TEXT PRIMARY KEY,
  block TEXT NOT NULL,
  shelf_code TEXT NOT NULL,
  capacity INTEGER,
  active INTEGER NOT NULL DEFAULT 1,
  UNIQUE(block, shelf_code)
);
CREATE TABLE IF NOT EXISTS stock (
  id TEXT PRIMARY KEY,
  product_id TEXT NOT NULL REFERENCES products(id),
  location_id TEXT NOT NULL REFERENCES locations(id),
  quantity INTEGER NOT NULL,
  UNIQUE(product_id, location_id)
);
CREATE TABLE IF NOT EXISTS orders (
  id TEXT PRIMARY KEY,
  external_key TEXT NOT NULL UNIQUE,
  order_id TEXT NOT NULL,
  source TEXT NOT NULL,
  raw_product_text TEXT NOT NULL,
  matched_product_id TEXT REFERENCES products(id),
  match_confidence INTEGER,
  match_method TEXT,
  candidates_json TEXT NOT NULL DEFAULT '[]',
  quantity INTEGER NOT NULL,
  proposed_location_id TEXT REFERENCES locations(id),
  source_location_id TEXT REFERENCES locations(id),
  status TEXT NOT NULL,
  needs_manual_match INTEGER NOT NULL DEFAULT 0,
  approval_token TEXT NOT NULL UNIQUE,
  stock_note TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS returns (
  id TEXT PRIMARY KEY,
  original_order_id TEXT NOT NULL REFERENCES orders(id),
  return_reason TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL,
  approval_token TEXT NOT NULL UNIQUE,
  inspect_token TEXT NOT NULL UNIQUE,
  restock_note TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS users (
  id TEXT PRIMARY KEY,
  first_name TEXT NOT NULL,
  last_name TEXT NOT NULL,
  email TEXT NOT NULL UNIQUE,
  company_name TEXT NOT NULL,
  password_hash TEXT NOT NULL,
  status TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS blocks (
  code TEXT PRIMARY KEY
);
CREATE TABLE IF NOT EXISTS channel_links (
  user_id TEXT NOT NULL,
  channel TEXT NOT NULL,
  secrets TEXT NOT NULL,
  connected_at TEXT NOT NULL,
  last_sync_at TEXT,
  last_error TEXT,
  PRIMARY KEY (user_id, channel)
);
CREATE TABLE IF NOT EXISTS support_messages (
  id TEXT PRIMARY KEY,
  user_id TEXT NOT NULL,
  sender_name TEXT NOT NULL,
  sender_email TEXT NOT NULL,
  company_name TEXT NOT NULL,
  channel TEXT NOT NULL DEFAULT '',
  body TEXT NOT NULL,
  created_at TEXT NOT NULL,
  reply TEXT,
  replied_at TEXT
);
CREATE TABLE IF NOT EXISTS member_messages (
  id TEXT PRIMARY KEY,
  user_id TEXT NOT NULL,
  body TEXT NOT NULL,
  created_at TEXT NOT NULL,
  seen INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_orders_order_id ON orders(order_id);
CREATE INDEX IF NOT EXISTS idx_returns_order ON returns(original_order_id);
"""


class SqliteStore:
    atomic = True

    def __init__(self, path: str):
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(target, check_same_thread=False, isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.executescript(SCHEMA)
        self._migrate_owners()
        self.conn.execute(
            "INSERT OR IGNORE INTO blocks (owner_id, code) SELECT owner_id, block FROM locations"
        )
        self.conn.commit()
        self._lock = threading.RLock()
        self._in_tx = False

    def _guard(self):
        if self._in_tx:
            return nullcontext()
        return self._lock

    def _exec(self, sql: str, params: tuple = ()):
        cur = self.conn.execute(sql, params)
        if not self._in_tx:
            self.conn.commit()
        return cur

    @contextmanager
    def transaction(self):
        with self._lock:
            if self._in_tx:
                yield
                return
            self._in_tx = True
            try:
                self.conn.execute("BEGIN IMMEDIATE")
                yield
                self.conn.commit()
            except Exception:
                self.conn.rollback()
                raise
            finally:
                self._in_tx = False

    def insert_user(self, account: Account) -> Account:
        with self._guard():
            self._exec(
                """
                INSERT INTO users (id, first_name, last_name, email, company_name, password_hash, status, created_at, intake_key)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    account.id,
                    account.first_name,
                    account.last_name,
                    account.email,
                    account.company_name,
                    account.password_hash,
                    account.status,
                    account.created_at,
                    account.intake_key,
                ),
            )
        return account

    def save_user(self, account: Account) -> Account:
        with self._guard():
            self._exec(
                "UPDATE users SET status=?, mail_sent=? WHERE id=?",
                (account.status, int(account.mail_sent), account.id),
            )
        return account

    def get_user(self, user_id: str) -> Account | None:
        with self._guard():
            row = self.conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        return self._account(row) if row else None

    def upsert_channel_link(self, user_id: str, channel: str, secrets: str, connected_at: str) -> None:
        with self._guard():
            self._exec(
                """
                INSERT INTO channel_links (user_id, channel, secrets, connected_at, last_sync_at, last_error)
                VALUES (?, ?, ?, ?, NULL, NULL)
                ON CONFLICT(user_id, channel) DO UPDATE SET
                  secrets=excluded.secrets, connected_at=excluded.connected_at, last_error=NULL
                """,
                (user_id, channel, secrets, connected_at),
            )

    def list_all_channel_links(self) -> list[dict]:
        with self._guard():
            rows = self.conn.execute(
                "SELECT user_id, channel, secrets, connected_at FROM channel_links"
            ).fetchall()
        return [dict(row) for row in rows]

    def get_stock_quantity(self, product_id: str, location_id: str) -> int:
        with self._guard():
            row = self.conn.execute(
                "SELECT quantity FROM stock WHERE product_id = ? AND location_id = ?",
                (product_id, location_id),
            ).fetchone()
        return int(row["quantity"]) if row else 0

    def list_channel_links(self, user_id: str) -> list[dict]:
        with self._guard():
            rows = self.conn.execute(
                "SELECT channel, secrets, connected_at, last_sync_at, last_error FROM channel_links WHERE user_id = ?",
                (user_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def delete_channel_link(self, user_id: str, channel: str) -> None:
        with self._guard():
            self._exec("DELETE FROM channel_links WHERE user_id = ? AND channel = ?", (user_id, channel))

    def touch_channel_link(self, user_id: str, channel: str, last_sync_at: str | None, last_error: str | None) -> None:
        with self._guard():
            self._exec(
                "UPDATE channel_links SET last_sync_at = ?, last_error = ? WHERE user_id = ? AND channel = ?",
                (last_sync_at, last_error, user_id, channel),
            )

    def find_user_by_intake(self, intake_key: str) -> Account | None:
        with self._guard():
            row = self.conn.execute("SELECT * FROM users WHERE intake_key = ?", (intake_key,)).fetchone()
        return self._account(row) if row else None

    def find_user_by_email(self, email: str) -> Account | None:
        with self._guard():
            row = self.conn.execute("SELECT * FROM users WHERE email = ? COLLATE NOCASE", (email,)).fetchone()
        return self._account(row) if row else None

    def insert_support(self, message_id: str, user_id: str, sender_name: str, sender_email: str, company_name: str, channel: str, body: str, created_at: str) -> None:
        with self._guard():
            self._exec(
                """
                INSERT INTO support_messages (
                  id, user_id, sender_name, sender_email, company_name, channel, body, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (message_id, user_id, sender_name, sender_email, company_name, channel, body, created_at),
            )

    def list_support(self) -> list[dict]:
        with self._guard():
            rows = self.conn.execute(
                """
                SELECT id, user_id, sender_name, sender_email, company_name, channel, body, created_at, reply, replied_at
                FROM support_messages ORDER BY created_at DESC
                """
            ).fetchall()
        return [dict(row) for row in rows]

    def get_support(self, message_id: str) -> dict | None:
        with self._guard():
            row = self.conn.execute("SELECT * FROM support_messages WHERE id = ?", (message_id,)).fetchone()
        return dict(row) if row else None

    def save_support_reply(self, message_id: str, reply: str, replied_at: str) -> None:
        with self._guard():
            self._exec(
                "UPDATE support_messages SET reply = ?, replied_at = ? WHERE id = ?",
                (reply, replied_at, message_id),
            )

    def delete_member_data(self, user_id: str) -> None:
        with self._guard():
            order_ids = [row["id"] for row in self.conn.execute("SELECT id FROM orders WHERE owner_id = ?", (user_id,))]
            for order_id in order_ids:
                self._exec("DELETE FROM returns WHERE original_order_id = ?", (order_id,))
            self._exec("DELETE FROM orders WHERE owner_id = ?", (user_id,))
            location_ids = [row["id"] for row in self.conn.execute("SELECT id FROM locations WHERE owner_id = ?", (user_id,))]
            for location_id in location_ids:
                self._exec("DELETE FROM stock WHERE location_id = ?", (location_id,))
                self._exec("UPDATE orders SET proposed_location_id = NULL WHERE proposed_location_id = ?", (location_id,))
                self._exec("UPDATE orders SET source_location_id = NULL WHERE source_location_id = ?", (location_id,))
                self._exec("DELETE FROM locations WHERE id = ?", (location_id,))
            self._exec("DELETE FROM blocks WHERE owner_id = ?", (user_id,))
            self._exec("DELETE FROM channel_links WHERE user_id = ?", (user_id,))
            self._exec("DELETE FROM member_messages WHERE user_id = ?", (user_id,))
            self._exec("DELETE FROM users WHERE id = ?", (user_id,))

    def insert_message(self, message_id: str, user_id: str, body: str, created_at: str) -> None:
        with self._guard():
            self._exec(
                "INSERT INTO member_messages (id, user_id, body, created_at) VALUES (?, ?, ?, ?)",
                (message_id, user_id, body, created_at),
            )

    def list_messages(self, user_id: str) -> list[dict]:
        with self._guard():
            rows = self.conn.execute(
                "SELECT id, body, created_at, seen FROM member_messages WHERE user_id = ? ORDER BY created_at DESC",
                (user_id,),
            ).fetchall()
        return [{"id": row["id"], "body": row["body"], "created_at": row["created_at"], "seen": bool(row["seen"])} for row in rows]

    def mark_messages_seen(self, user_id: str) -> None:
        with self._guard():
            self._exec("UPDATE member_messages SET seen = 1 WHERE user_id = ? AND seen = 0", (user_id,))

    def list_users(self) -> list[Account]:
        with self._guard():
            rows = self.conn.execute("SELECT * FROM users ORDER BY created_at DESC").fetchall()
        return [self._account(row) for row in rows]

    def list_products(self) -> list[Product]:
        with self._guard():
            rows = self.conn.execute("SELECT * FROM products ORDER BY sku").fetchall()
        return [self._product(row) for row in rows]

    def get_product(self, product_id: str) -> Product | None:
        with self._guard():
            row = self.conn.execute("SELECT * FROM products WHERE id = ?", (product_id,)).fetchone()
        return self._product(row) if row else None

    def save_product(self, product: Product) -> Product:
        with self._guard():
            self._exec(
                """
                UPDATE products
                SET sku=?, product_name=?, brand=?, category=?, barcode=?, alt_names=?
                WHERE id=?
                """,
                (
                    product.sku,
                    product.product_name,
                    product.brand,
                    product.category,
                    product.barcode,
                    product.alt_names,
                    product.id,
                ),
            )
        return product

    def insert_product(self, product: Product) -> Product:
        with self._guard():
            self._exec(
                """
                INSERT INTO products (id, sku, product_name, brand, category, barcode, alt_names)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    product.id,
                    product.sku,
                    product.product_name,
                    product.brand,
                    product.category,
                    product.barcode,
                    product.alt_names,
                ),
            )
        return product

    def _migrate_owners(self) -> None:
        self.conn.execute("PRAGMA foreign_keys = OFF")
        location_cols = {row[1] for row in self.conn.execute("PRAGMA table_info(locations)")}
        if "owner_id" not in location_cols:
            self.conn.execute(
                """
                CREATE TABLE locations_new (
                  id TEXT PRIMARY KEY,
                  owner_id TEXT NOT NULL DEFAULT '',
                  block TEXT NOT NULL,
                  shelf_code TEXT NOT NULL,
                  capacity INTEGER,
                  active INTEGER NOT NULL DEFAULT 1,
                  UNIQUE(owner_id, block, shelf_code)
                )
                """
            )
            self.conn.execute(
                """
                INSERT INTO locations_new (id, owner_id, block, shelf_code, capacity, active)
                SELECT id, '', block, shelf_code, capacity, active FROM locations
                """
            )
            self.conn.execute("DROP TABLE locations")
            self.conn.execute("ALTER TABLE locations_new RENAME TO locations")
        block_cols = {row[1] for row in self.conn.execute("PRAGMA table_info(blocks)")}
        if "owner_id" not in block_cols:
            self.conn.execute(
                """
                CREATE TABLE blocks_new (
                  owner_id TEXT NOT NULL DEFAULT '',
                  code TEXT NOT NULL,
                  PRIMARY KEY (owner_id, code)
                )
                """
            )
            self.conn.execute("INSERT INTO blocks_new (owner_id, code) SELECT '', code FROM blocks")
            self.conn.execute("DROP TABLE blocks")
            self.conn.execute("ALTER TABLE blocks_new RENAME TO blocks")
        user_cols = {row[1] for row in self.conn.execute("PRAGMA table_info(users)")}
        if "mail_sent" not in user_cols:
            self.conn.execute("ALTER TABLE users ADD COLUMN mail_sent INTEGER NOT NULL DEFAULT 0")
        if "intake_key" not in user_cols:
            self.conn.execute("ALTER TABLE users ADD COLUMN intake_key TEXT NOT NULL DEFAULT ''")
        for row in self.conn.execute("SELECT id FROM users WHERE intake_key = ''").fetchall():
            self.conn.execute(
                "UPDATE users SET intake_key = ? WHERE id = ?",
                (secrets.token_urlsafe(18), row["id"]),
            )
        self.conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_users_intake ON users(intake_key)")
        message_cols = {row[1] for row in self.conn.execute("PRAGMA table_info(member_messages)")}
        if message_cols and "seen" not in message_cols:
            self.conn.execute("ALTER TABLE member_messages ADD COLUMN seen INTEGER NOT NULL DEFAULT 0")
        support_cols = {row[1] for row in self.conn.execute("PRAGMA table_info(support_messages)")}
        if support_cols and "reply" not in support_cols:
            self.conn.execute("ALTER TABLE support_messages ADD COLUMN reply TEXT")
            self.conn.execute("ALTER TABLE support_messages ADD COLUMN replied_at TEXT")
        order_cols = {row[1] for row in self.conn.execute("PRAGMA table_info(orders)")}
        if "owner_id" not in order_cols:
            self.conn.execute("ALTER TABLE orders ADD COLUMN owner_id TEXT NOT NULL DEFAULT ''")
        self.conn.execute("PRAGMA foreign_keys = ON")

    def list_block_codes(self, owner_id: str = "") -> list[str]:
        with self._guard():
            rows = self.conn.execute("SELECT code FROM blocks WHERE owner_id = ?", (owner_id,)).fetchall()
        return [row["code"] for row in rows]

    def insert_block_code(self, code: str, owner_id: str = "") -> None:
        with self._guard():
            self._exec("INSERT OR IGNORE INTO blocks (owner_id, code) VALUES (?, ?)", (owner_id, code))

    def delete_block_code(self, code: str, owner_id: str = "") -> None:
        with self._guard():
            self._exec("DELETE FROM blocks WHERE owner_id = ? AND code = ?", (owner_id, code))

    def delete_stock_row(self, stock_id: str) -> None:
        with self._guard():
            self._exec("DELETE FROM stock WHERE id = ?", (stock_id,))

    def delete_location(self, location_id: str) -> None:
        with self._guard():
            self._exec("DELETE FROM stock WHERE location_id = ?", (location_id,))
            self._exec("UPDATE orders SET proposed_location_id = NULL WHERE proposed_location_id = ?", (location_id,))
            self._exec("UPDATE orders SET source_location_id = NULL WHERE source_location_id = ?", (location_id,))
            self._exec("DELETE FROM locations WHERE id = ?", (location_id,))

    def list_locations(self) -> list[Location]:
        with self._guard():
            rows = self.conn.execute("SELECT * FROM locations").fetchall()
        return [self._location(row) for row in rows]

    def get_location(self, location_id: str) -> Location | None:
        with self._guard():
            row = self.conn.execute("SELECT * FROM locations WHERE id = ?", (location_id,)).fetchone()
        return self._location(row) if row else None

    def find_location(self, block: str, shelf_code: str, owner_id: str = "") -> Location | None:
        with self._guard():
            row = self.conn.execute(
                "SELECT * FROM locations WHERE owner_id = ? AND block = ? AND shelf_code = ?",
                (owner_id, block, shelf_code),
            ).fetchone()
        return self._location(row) if row else None

    def insert_location(self, location: Location) -> Location:
        with self._guard():
            self._exec(
                "INSERT INTO locations (id, owner_id, block, shelf_code, capacity, active) VALUES (?, ?, ?, ?, ?, ?)",
                (location.id, location.owner_id, location.block, location.shelf_code, location.capacity, int(location.active)),
            )
        return location

    def save_location(self, location: Location) -> Location:
        with self._guard():
            self._exec(
                "UPDATE locations SET block=?, shelf_code=?, capacity=?, active=? WHERE id=?",
                (location.block, location.shelf_code, location.capacity, int(location.active), location.id),
            )
        return location

    def list_stock(self) -> list[StockRow]:
        with self._guard():
            rows = self.conn.execute("SELECT * FROM stock").fetchall()
        return [self._stock(row) for row in rows]

    def find_stock(self, product_id: str, location_id: str) -> StockRow | None:
        with self._guard():
            row = self.conn.execute(
                "SELECT * FROM stock WHERE product_id = ? AND location_id = ?",
                (product_id, location_id),
            ).fetchone()
        return self._stock(row) if row else None

    def insert_stock(self, row: StockRow) -> StockRow:
        with self._guard():
            self._exec(
                "INSERT INTO stock (id, product_id, location_id, quantity) VALUES (?, ?, ?, ?)",
                (row.id, row.product_id, row.location_id, row.quantity),
            )
        return row

    def adjust_stock(self, product_id: str, location_id: str, delta: int, capacity_check: bool = True) -> int:
        with self._guard():
            location = self.get_location(location_id)
            if location is None:
                raise StockError("missing")
            current_row = self.find_stock(product_id, location_id)
            current = current_row.quantity if current_row else 0
            new_qty = current + delta
            if new_qty < 0:
                raise StockError("negative")
            if capacity_check and delta > 0 and location.capacity is not None and new_qty > location.capacity:
                raise StockError("capacity")
            if current_row is None:
                self._exec(
                    "INSERT INTO stock (id, product_id, location_id, quantity) VALUES (?, ?, ?, ?)",
                    (f"stk-{product_id}-{location_id}", product_id, location_id, new_qty),
                )
            else:
                self._exec("UPDATE stock SET quantity = ? WHERE id = ?", (new_qty, current_row.id))
            return new_qty

    def get_order(self, order_id: str) -> Order | None:
        with self._guard():
            row = self.conn.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()
        return self._order(row) if row else None

    def get_order_by_token(self, token: str) -> Order | None:
        with self._guard():
            row = self.conn.execute("SELECT * FROM orders WHERE approval_token = ?", (token,)).fetchone()
        return self._order(row) if row else None

    def find_order_by_external(self, external_key: str) -> Order | None:
        with self._guard():
            row = self.conn.execute("SELECT * FROM orders WHERE external_key = ?", (external_key,)).fetchone()
        return self._order(row) if row else None

    def list_orders(self) -> list[Order]:
        with self._guard():
            rows = self.conn.execute("SELECT * FROM orders ORDER BY created_at DESC").fetchall()
        return [self._order(row) for row in rows]

    def list_orders_by_order_id(self, order_id: str) -> list[Order]:
        with self._guard():
            rows = self.conn.execute("SELECT * FROM orders WHERE order_id = ?", (order_id,)).fetchall()
        return [self._order(row) for row in rows]

    def save_order(self, order: Order) -> Order:
        payload = (
            order.external_key,
            order.order_id,
            order.source,
            order.raw_product_text,
            order.matched_product_id,
            order.match_confidence,
            order.match_method,
            json.dumps(order.candidates, ensure_ascii=False),
            order.quantity,
            order.proposed_location_id,
            order.source_location_id,
            order.status,
            int(order.needs_manual_match),
            order.approval_token,
            order.stock_note,
            order.created_at,
            order.updated_at,
            order.owner_id,
            order.id,
        )
        with self._guard():
            existing = self.conn.execute("SELECT id FROM orders WHERE id = ?", (order.id,)).fetchone()
            if existing:
                self._exec(
                    """
                    UPDATE orders SET
                      external_key=?, order_id=?, source=?, raw_product_text=?, matched_product_id=?,
                      match_confidence=?, match_method=?, candidates_json=?, quantity=?,
                      proposed_location_id=?, source_location_id=?, status=?, needs_manual_match=?,
                      approval_token=?, stock_note=?, created_at=?, updated_at=?, owner_id=?
                    WHERE id=?
                    """,
                    payload,
                )
            else:
                self._exec(
                    """
                    INSERT INTO orders (
                      external_key, order_id, source, raw_product_text, matched_product_id,
                      match_confidence, match_method, candidates_json, quantity,
                      proposed_location_id, source_location_id, status, needs_manual_match,
                      approval_token, stock_note, created_at, updated_at, owner_id, id
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    payload,
                )
        return order

    def get_return(self, return_id: str) -> ReturnRequest | None:
        with self._guard():
            row = self.conn.execute("SELECT * FROM returns WHERE id = ?", (return_id,)).fetchone()
        return self._return(row) if row else None

    def get_return_by_token(self, token: str, kind: str) -> ReturnRequest | None:
        column = "approval_token" if kind == "approval" else "inspect_token"
        with self._guard():
            row = self.conn.execute(f"SELECT * FROM returns WHERE {column} = ?", (token,)).fetchone()
        return self._return(row) if row else None

    def list_returns(self) -> list[ReturnRequest]:
        with self._guard():
            rows = self.conn.execute("SELECT * FROM returns ORDER BY created_at DESC").fetchall()
        return [self._return(row) for row in rows]

    def active_return_for_order(self, order_pk: str) -> ReturnRequest | None:
        with self._guard():
            row = self.conn.execute(
                "SELECT * FROM returns WHERE original_order_id = ? AND status != 'reddedildi' ORDER BY created_at DESC",
                (order_pk,),
            ).fetchone()
        return self._return(row) if row else None

    def save_return(self, item: ReturnRequest) -> ReturnRequest:
        payload = (
            item.original_order_id,
            item.return_reason,
            item.status,
            item.approval_token,
            item.inspect_token,
            item.restock_note,
            item.created_at,
            item.updated_at,
            item.id,
        )
        with self._guard():
            existing = self.conn.execute("SELECT id FROM returns WHERE id = ?", (item.id,)).fetchone()
            if existing:
                self._exec(
                    """
                    UPDATE returns SET
                      original_order_id=?, return_reason=?, status=?, approval_token=?,
                      inspect_token=?, restock_note=?, created_at=?, updated_at=?
                    WHERE id=?
                    """,
                    payload,
                )
            else:
                self._exec(
                    """
                    INSERT INTO returns (
                      original_order_id, return_reason, status, approval_token,
                      inspect_token, restock_note, created_at, updated_at, id
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    payload,
                )
        return item

    def _account(self, row) -> Account:
        return Account(
            id=row["id"],
            first_name=row["first_name"],
            last_name=row["last_name"],
            email=row["email"],
            company_name=row["company_name"],
            password_hash=row["password_hash"],
            status=row["status"],
            created_at=row["created_at"],
            mail_sent=bool(row["mail_sent"]) if "mail_sent" in row.keys() else False,
            intake_key=row["intake_key"] if "intake_key" in row.keys() else "",
        )

    def _product(self, row) -> Product:
        return Product(
            id=row["id"],
            sku=row["sku"],
            product_name=row["product_name"],
            brand=row["brand"],
            category=row["category"],
            barcode=row["barcode"],
            alt_names=row["alt_names"],
        )

    def _location(self, row) -> Location:
        return Location(
            id=row["id"],
            block=row["block"],
            shelf_code=row["shelf_code"],
            capacity=row["capacity"],
            active=bool(row["active"]),
            owner_id=row["owner_id"] if "owner_id" in row.keys() else "",
        )

    def _stock(self, row) -> StockRow:
        return StockRow(
            id=row["id"],
            product_id=row["product_id"],
            location_id=row["location_id"],
            quantity=row["quantity"],
        )

    def _order(self, row) -> Order:
        try:
            candidates = json.loads(row["candidates_json"] or "[]")
        except json.JSONDecodeError:
            candidates = []
        return Order(
            id=row["id"],
            external_key=row["external_key"],
            order_id=row["order_id"],
            source=row["source"],
            raw_product_text=row["raw_product_text"],
            quantity=row["quantity"],
            status=row["status"],
            approval_token=row["approval_token"],
            matched_product_id=row["matched_product_id"],
            match_confidence=row["match_confidence"],
            match_method=row["match_method"],
            candidates=candidates,
            proposed_location_id=row["proposed_location_id"],
            source_location_id=row["source_location_id"],
            needs_manual_match=bool(row["needs_manual_match"]),
            stock_note=row["stock_note"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            owner_id=row["owner_id"] if "owner_id" in row.keys() else "",
        )

    def _return(self, row) -> ReturnRequest:
        return ReturnRequest(
            id=row["id"],
            original_order_id=row["original_order_id"],
            status=row["status"],
            approval_token=row["approval_token"],
            inspect_token=row["inspect_token"],
            return_reason=row["return_reason"],
            restock_note=row["restock_note"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )
