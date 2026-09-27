from __future__ import annotations

import logging
import re
import secrets
import uuid
from datetime import datetime, timezone

import httpx

from app.channels import ChannelError, channel_by_code, CHANNELS, pull_trendyol
from app.config import Settings
from app.matching import Matcher
from app.secretbox import open_sealed, seal
from app.models import Account, IncomingLine, Location, Order, Product, ReturnRequest, StockError
from app.passwords import hash_password, verify_password
from app.results import Leave, Result
from app.textutil import fold, location_label, manual_sentence, now_iso, order_sentence, shelf_key, stock_sentence

log = logging.getLogger(__name__)


def _same(left: str, right: str) -> bool:
    if len(left) != len(right):
        return False
    return secrets.compare_digest(left, right)

ORDER_DONE = {"onaylandı", "tamamlandı"}
RETURN_OPEN = {"talep edildi", "onaylandı", "kontrol bekliyor", "stoğa eklendi"}

METHOD_LABELS = {
    "sku": "SKU",
    "barcode": "barkod",
    "name": "isim",
    "alt_name": "eski eşleşme",
    "llm": "AI",
    "fuzzy": "benzerlik",
    "manual": "manuel",
}


class WarehouseService:
    def __init__(self, store, settings: Settings, matcher: Matcher):
        self.store = store
        self.settings = settings
        self.matcher = matcher

    def ingest_order(self, line: IncomingLine, owner: str = "") -> Result:
        def op():
            existing = self.store.find_order_by_external(line.external_key)
            if existing:
                raise Leave(Result(True, "duplicate", "Bu sipariş zaten kayıtlı", data={"order": self._order_view(existing)}))
            outcome = self.matcher.match(self.store.list_products(), line.product_name, line.sku, line.barcode)
            auto = outcome.is_auto(self.settings.auto_match_threshold)
            order = Order(
                id=str(uuid.uuid4()),
                external_key=line.external_key,
                order_id=line.order_id,
                source=line.source,
                raw_product_text=line.product_name,
                quantity=line.quantity,
                status="beklemede",
                approval_token=secrets.token_urlsafe(32),
                matched_product_id=outcome.product.id if auto and outcome.product else None,
                match_confidence=outcome.confidence,
                match_method=outcome.method,
                candidates=[self._candidate(item) for item in outcome.candidates],
                needs_manual_match=not auto,
                created_at=now_iso(),
                updated_at=now_iso(),
                owner_id=owner,
            )
            if auto and outcome.product:
                place, note = self._choose(outcome.product.id, line.quantity, owner)
                order.proposed_location_id = place.id if place else None
                order.stock_note = note
            self.store.save_order(order)
            log.info("sipariş %s %s güven=%s", order.order_id, order.match_method, order.match_confidence)
            return Result(True, "created", "Sipariş kaydedildi", 201, {"order": self._order_view(order), "notification": self._order_notification(order)})

        result = self._run(op)
        if result.ok and result.code == "created":
            self._ping_new_order((result.data or {}).get("order") or {})
        return result

    def confirm_match(self, order_pk: str, product_id: str) -> Result:
        def op():
            order = self.store.get_order(order_pk)
            if order is None:
                raise Leave(Result(False, "not_found", "Sipariş bulunamadı", 404))
            if order.status in ORDER_DONE:
                raise Leave(Result(True, "already_processed", "Bu sipariş zaten işlendi"))
            if not order.needs_manual_match:
                raise Leave(Result(False, "invalid_state", "Bu sipariş manuel eşleştirme beklemiyor", 409))
            product = self.store.get_product(product_id)
            if product is None:
                raise Leave(Result(False, "not_found", "Ürün bulunamadı", 404))
            self._remember_alias(product, order.raw_product_text)
            order.matched_product_id = product.id
            order.match_confidence = 100
            order.match_method = "manual"
            order.needs_manual_match = False
            place, note = self._choose(product.id, order.quantity, order.owner_id)
            order.proposed_location_id = place.id if place else None
            order.stock_note = note
            order.updated_at = now_iso()
            self.store.save_order(order)
            return Result(
                True,
                "matched",
                "Ürün eşleştirildi. Stok henüz düşülmedi.",
                data={"order": self._order_view(order), "notification": self._order_notification(order)},
            )

        return self._run(op)

    def approve_order(self, token: str | None = None, order_pk: str | None = None) -> Result:
        def op():
            order = self.store.get_order_by_token(token) if token else self.store.get_order(order_pk or "")
            if order is None:
                raise Leave(Result(False, "not_found", "Sipariş bulunamadı", 404))
            if order.status in ORDER_DONE:
                raise Leave(Result(True, "already_processed", "Bu sipariş zaten işlendi", data={"order": self._order_view(order)}))
            if order.status == "iptal":
                raise Leave(Result(False, "invalid_state", "Sipariş iptal edilmiş", 409))
            if order.needs_manual_match or not order.matched_product_id:
                raise Leave(Result(False, "needs_manual_match", "Bu sipariş manuel ürün eşleştirmesi bekliyor", 409, {"order": self._order_view(order)}))
            if not order.proposed_location_id:
                place, note = self._choose(order.matched_product_id, order.quantity, order.owner_id)
                order.proposed_location_id = place.id if place else None
                order.stock_note = note
            if not order.proposed_location_id:
                raise Leave(Result(False, "insufficient_stock", "Stokta yok. Stok düşülmedi.", 409, {"order": self._order_view(order)}))
            location = self.store.get_location(order.proposed_location_id)
            if location is None or not location.active:
                raise Leave(Result(False, "location_unsuitable", "Seçilen raf artık uygun değil. Stok düşülmedi.", 409))
            try:
                self.store.adjust_stock(order.matched_product_id, location.id, -order.quantity)
            except StockError as exc:
                if exc.code == "negative":
                    raise Leave(Result(False, "insufficient_stock", "Bu rafta yeterli stok yok. Stok düşülmedi.", 409, {"order": self._order_view(order)}))
                raise
            order.source_location_id = location.id
            order.status = "tamamlandı"
            order.updated_at = now_iso()
            self.store.save_order(order)
            new_qty = self.store.get_stock_quantity(order.matched_product_id, location.id)
            view = self._order_view(order)
            return Result(True, "completed", "Stok düşüldü, sipariş tamamlandı.", data={"order": view, "stock_after": new_qty})

        result = self._run(op)
        if result.ok and result.code == "completed":
            self._ping_low_stock(result.data or {})
        return result

    def describe_order_token(self, token: str) -> Result:
        order = self.store.get_order_by_token(token)
        if order is None:
            return Result(False, "not_found", "Sipariş bulunamadı", 404)
        view = self._order_view(order)
        if order.status in ORDER_DONE:
            return Result(True, "already_processed", "Bu sipariş zaten işlendi", data={"order": view, "can_confirm": False})
        if order.needs_manual_match:
            return Result(False, "needs_manual_match", "Bu sipariş manuel ürün eşleştirmesi bekliyor", 409, {"order": view, "can_confirm": False})
        sentence = view.get("sentence") or "Sipariş onay bekliyor"
        return Result(True, "pending", sentence, data={"order": view, "can_confirm": True})

    def ingest_return(self, order_id: str, reason: str = "", sku: str | None = None, barcode: str | None = None) -> Result:
        def op():
            matches = [item for item in self.store.list_orders_by_order_id(order_id.strip()) if item.status == "tamamlandı"]
            if sku or barcode:
                wanted = fold(sku or barcode)
                matches = [item for item in matches if self._order_matches_code(item, wanted)]
            if not matches:
                raise Leave(Result(False, "not_found", "Tamamlanmış sipariş bulunamadı", 404))
            if len(matches) > 1:
                raise Leave(Result(False, "ambiguous", "Bu sipariş numarasında birden fazla ürün var, SKU gönderin", 409))
            order = matches[0]
            if self.store.active_return_for_order(order.id):
                raise Leave(Result(False, "duplicate_return", "Bu sipariş için zaten bir iade kaydı var", 409))
            item = ReturnRequest(
                id=str(uuid.uuid4()),
                original_order_id=order.id,
                status="talep edildi",
                approval_token=secrets.token_urlsafe(32),
                inspect_token=secrets.token_urlsafe(32),
                return_reason=reason.strip(),
                created_at=now_iso(),
                updated_at=now_iso(),
            )
            self.store.save_return(item)
            return Result(True, "created", "İade talebi kaydedildi. Stok henüz değişmedi.", 201, {"return": self._return_view(item), "notification": self._return_notification(item, order)})

        return self._run(op)

    def approve_return(self, token: str | None = None, return_pk: str | None = None) -> Result:
        result = self._run(lambda: self._approve_return_tx(token, return_pk))
        if result.ok and result.code == "inspection_pending":
            self._ping_inspect(result.data or {})
        return result

    def _approve_return_tx(self, token: str | None, return_pk: str | None) -> Result:
        item = self.store.get_return_by_token(token, "approval") if token else self.store.get_return(return_pk or "")
        if item is None:
            raise Leave(Result(False, "not_found", "İade bulunamadı", 404))
        if item.status in {"onaylandı", "kontrol bekliyor", "stoğa eklendi"}:
            raise Leave(Result(True, "already_processed", "Bu iade zaten işlendi", data={"return": self._return_view(item)}))
        if item.status == "reddedildi":
            raise Leave(Result(False, "invalid_state", "Bu iade reddedilmiş", 409))
        if item.status != "talep edildi":
            raise Leave(Result(False, "invalid_state", "İade bu aşamada onaylanamaz", 409))
        item.status = "kontrol bekliyor"
        item.updated_at = now_iso()
        self.store.save_return(item)
        view = self._return_view(item)
        return Result(True, "inspection_pending", "İade onaylandı. Ürün fiziksel kontrol bekliyor; stoğa eklenmedi.", data={"return": view, "notification": self._inspect_notification(item)})

    def restock_return(self, token: str | None = None, return_pk: str | None = None, alternate_location_id: str | None = None) -> Result:
        def op():
            item = self.store.get_return_by_token(token, "inspect") if token else self.store.get_return(return_pk or "")
            if item is None:
                raise Leave(Result(False, "not_found", "İade bulunamadı", 404))
            if item.status == "stoğa eklendi":
                raise Leave(Result(True, "already_processed", "Bu iade zaten stoğa eklendi", data={"return": self._return_view(item)}))
            if item.status != "kontrol bekliyor":
                raise Leave(Result(False, "invalid_state", "Önce iade onaylanmalı ve fiziksel kontrol yapılmalı", 409))
            order = self.store.get_order(item.original_order_id)
            if order is None or not order.matched_product_id:
                raise Leave(Result(False, "not_found", "Orijinal sipariş bulunamadı", 404))
            original = self.store.get_location(order.source_location_id) if order.source_location_id else None
            suitable, why = self._suitable(original, order.matched_product_id, order.quantity)
            if suitable and original is not None:
                target = original
            else:
                if not alternate_location_id:
                    raise Leave(self._unsuitable(item, original, why))
                target = self.store.get_location(alternate_location_id)
                alt_ok, alt_why = self._suitable(target, order.matched_product_id, order.quantity)
                if not alt_ok or target is None:
                    raise Leave(Result(False, "location_unsuitable", f"Seçilen raf uygun değil ({alt_why}).", 409, {"return": self._return_view(item)}))
            try:
                self.store.adjust_stock(order.matched_product_id, target.id, order.quantity)
            except StockError as exc:
                message = "Seçilen rafın kapasitesi dolu." if exc.code == "capacity" else "Stok güncellenemedi."
                raise Leave(Result(False, "location_unsuitable", message, 409))
            item.status = "stoğa eklendi"
            item.restock_note = location_label(target.block, target.shelf_code)
            item.updated_at = now_iso()
            self.store.save_return(item)
            return Result(True, "restocked", f"Ürün {item.restock_note} konumuna geri eklendi.", data={"return": self._return_view(item)})

        return self._run(op)

    def reject_return(self, return_pk: str) -> Result:
        def op():
            item = self.store.get_return(return_pk)
            if item is None:
                raise Leave(Result(False, "not_found", "İade bulunamadı", 404))
            if item.status == "stoğa eklendi":
                raise Leave(Result(False, "invalid_state", "Stoğa eklenen iade reddedilemez", 409))
            if item.status == "reddedildi":
                raise Leave(Result(True, "already_processed", "Bu iade zaten reddedildi"))
            item.status = "reddedildi"
            item.updated_at = now_iso()
            self.store.save_return(item)
            return Result(True, "rejected", "İade reddedildi", data={"return": self._return_view(item)})

        return self._run(op)

    def describe_return_token(self, token: str, kind: str) -> Result:
        item = self.store.get_return_by_token(token, kind)
        if item is None:
            return Result(False, "not_found", "İade bulunamadı", 404)
        view = self._return_view(item)
        if kind == "approval":
            if item.status in {"onaylandı", "kontrol bekliyor", "stoğa eklendi"}:
                return Result(True, "already_processed", "Bu iade zaten işlendi", data={"return": view, "can_confirm": False})
            if item.status != "talep edildi":
                return Result(False, "invalid_state", "Bu iade onaylanamaz", 409, {"return": view, "can_confirm": False})
            return Result(True, "pending", "İade talebini onayla. Bu adım stoğa ürün eklemez.", data={"return": view, "can_confirm": True})
        if item.status == "stoğa eklendi":
            return Result(True, "already_processed", "Bu iade zaten stoğa eklendi", data={"return": view, "can_confirm": False})
        if item.status != "kontrol bekliyor":
            return Result(False, "invalid_state", "Önce iade talebi onaylanmalı", 409, {"return": view, "can_confirm": False})
        label = view.get("original_location_label") or "orijinal raf"
        return Result(True, "pending", f"Sağlam, stoğa ekle. Ürün {label} konumuna yazılacak.", data={"return": view, "can_confirm": True})

    def preview_add(self, product_id: str, location_id: str, quantity: int, owner: str = "") -> Result:
        if quantity <= 0:
            return Result(False, "invalid", "Adet pozitif olmalı", 400)
        product = self.store.get_product(product_id)
        location = self.store.get_location(location_id)
        if product is None or location is None or location.owner_id != owner:
            return Result(False, "not_found", "Ürün veya raf bulunamadı", 404)
        if not location.active:
            return Result(False, "location_unsuitable", "Bu raf kaldırılmış", 409)
        current = self._qty(product.id, location.id)
        resulting = current + quantity
        summary = stock_sentence(product.product_name, quantity, location.block, location.shelf_code)
        data = {
            "summary": summary,
            "product_id": product.id,
            "location_id": location.id,
            "quantity": quantity,
            "current_quantity": current,
            "resulting_quantity": resulting,
            "capacity": location.capacity,
        }
        if location.capacity is not None and resulting > location.capacity:
            return Result(False, "capacity_exceeded", f"{summary} Kapasite aşılıyor ({location.capacity}).", 409, data)
        return Result(True, "preview", summary, data=data)

    def add_stock(self, product_id: str, location_id: str, quantity: int, confirmed: bool, owner: str = "") -> Result:
        if not confirmed:
            return Result(False, "confirmation_required", "Özet onaylanmadan stok eklenmez", 400)
        preview = self.preview_add(product_id, location_id, quantity, owner)
        if not preview.ok:
            return preview

        def op():
            try:
                new_qty = self.store.adjust_stock(product_id, location_id, quantity, capacity_check=False)
            except StockError as exc:
                message = "Kapasite aşılıyor." if exc.code == "capacity" else "Stok güncellenemedi."
                raise Leave(Result(False, "capacity_exceeded", message, 409))
            return Result(True, "added", preview.message, data={**(preview.data or {}), "resulting_quantity": new_qty})

        return self._run(op)

    def list_stock(self, q: str = "", block: str = "", owner: str = "") -> list[dict]:
        rows = self._stock_views(owner)
        needle = fold(q)
        block_fold = fold(block)
        selected = []
        for row in rows:
            if block_fold and fold(row["block"]) != block_fold:
                continue
            if needle and needle not in fold(" ".join([row["sku"], row["product_name"], row["brand"], row["block"], row["shelf_code"], row["location_label"]])):
                continue
            selected.append(row)
        selected.sort(key=lambda row: (row["block"], shelf_key(row["shelf_code"]), row["sku"]))
        return selected

    def list_channels(self, owner: str) -> list[dict]:
        saved = {row["channel"]: row for row in self.store.list_channel_links(owner)}
        rows = []
        for spec in CHANNELS:
            link = saved.get(spec["code"])
            rows.append(
                {
                    "code": spec["code"],
                    "name": spec["name"],
                    "mode": spec["mode"],
                    "live": spec["live"],
                    "hint": spec["hint"],
                    "lead": spec.get("lead") or spec["hint"],
                    "steps": list(spec.get("steps") or ()),
                    "fields": [dict(item) for item in spec["fields"]],
                    "connected": link is not None,
                    "last_error": (link or {}).get("last_error") or "",
                }
            )
        return rows

    def save_channel(self, owner: str, code: str, fields: dict) -> Result:
        spec = channel_by_code(code)
        if spec is None or spec["mode"] != "api":
            return Result(False, "invalid", "Bu kanal buradan bağlanmaz", 400)
        cleaned = {}
        for field in spec["fields"]:
            value = str(fields.get(field["name"]) or "").strip()
            if not value:
                return Result(False, "invalid", f"{field['label']} gerekli", 400)
            cleaned[field["name"]] = value
        self.store.upsert_channel_link(owner, code, seal(cleaned, self.settings.panel_secret), now_iso())
        if spec["code"] in {"hepsiburada", "n11", "amazon"}:
            self._ping_channel(owner, spec["code"], cleaned)
        if spec["live"]:
            return Result(True, "connected", f"{spec['name']} bağlandı. Yeni siparişler bu listede görünür.")
        return Result(True, "stored", f"{spec['name']} bağlantısı kaydedildi.")

    def disconnect_channel(self, owner: str, code: str) -> Result:
        self.store.delete_channel_link(owner, code)
        return Result(True, "disconnected", "Bağlantı kaldırıldı")

    def sync_channels(self, owner: str) -> Result:
        imported = 0
        notes: list[str] = []
        for link in self.store.list_channel_links(owner):
            spec = channel_by_code(link["channel"])
            if spec is None or not spec["live"] or spec["code"] != "trendyol":
                continue
            try:
                secrets = open_sealed(link["secrets"], self.settings.panel_secret)
                lines = pull_trendyol(secrets)
            except (ChannelError, ValueError) as exc:
                message = exc.message if isinstance(exc, ChannelError) else "Bağlantı bilgisi okunamadı"
                self.store.touch_channel_link(owner, link["channel"], link.get("last_sync_at"), message)
                self._ping_channel_error(owner, spec["name"], message)
                notes.append(message)
                continue
            for line in lines:
                line.external_key = f"{owner}|{line.external_key}"
                result = self.ingest_order(line, owner)
                if result.code == "created":
                    imported += 1
            self.store.touch_channel_link(owner, link["channel"], now_iso(), None)
        if notes and imported == 0:
            return Result(False, "sync_failed", notes[0], 502, {"imported": 0})
        return Result(True, "synced", f"{imported} yeni sipariş düştü" if imported else "", data={"imported": imported})

    def list_orders(self, status: str = "", source: str = "", owner: str = "") -> list[dict]:
        rows = [self._order_view(order) for order in self.store.list_orders() if order.owner_id == owner]
        if status:
            rows = [row for row in rows if row["status"] == status]
        if source:
            rows = [row for row in rows if row["source"] == source]
        return rows

    def list_returns(self, status: str = "") -> list[dict]:
        rows = [self._return_view(item) for item in self.store.list_returns()]
        if status:
            rows = [row for row in rows if row["status"] == status]
        return rows

    def register(self, first_name: str, last_name: str, email: str, company_name: str, password: str) -> Result:
        first_name, last_name, company_name = first_name.strip(), last_name.strip(), company_name.strip()
        email = email.strip().lower()
        if not first_name or not last_name or not company_name:
            return Result(False, "invalid", "İsim, soyisim ve şirket adı gerekli", 400)
        if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
            return Result(False, "invalid", "Geçerli bir e-posta gerekli", 400)
        if len(password) < 6:
            return Result(False, "invalid", "Parola en az 6 karakter olmalı", 400)
        if self.store.find_user_by_email(email):
            return Result(False, "duplicate", "Bu e-posta ile kayıt var", 409)
        account = Account(
            id=str(uuid.uuid4()),
            first_name=first_name,
            last_name=last_name,
            email=email,
            company_name=company_name,
            password_hash=hash_password(password),
            status="beklemede",
            created_at=now_iso(),
            intake_key=secrets.token_urlsafe(18),
        )
        self.store.insert_user(account)
        return Result(True, "created", "Üyeliğiniz alındı. Yönetici onayından sonra giriş yapabilirsiniz.", 201, {"account": self._account_view(account)})

    def authenticate_admin(self, username: str, password: str) -> Result:
        if _same(username, self.settings.panel_username) and _same(password, self.settings.panel_password):
            return Result(True, "ok", "Giriş yapıldı", data={"user": {"role": "admin", "name": "Yönetici", "email": self.settings.notify_email}})
        return Result(False, "unauthorized", "Kullanıcı adı veya parola hatalı", 401)

    def authenticate(self, username: str, password: str) -> Result:
        account = self.store.find_user_by_email(username.strip())
        if account is None or not verify_password(password, account.password_hash):
            return Result(False, "unauthorized", "Kullanıcı adı veya parola hatalı", 401)
        if account.status == "beklemede":
            return Result(False, "pending_approval", "Hesabınız henüz onaylanmadı", 403)
        if account.status != "onaylandı":
            return Result(False, "rejected", "Hesabınız reddedildi", 403)
        return Result(
            True,
            "ok",
            "Giriş yapıldı",
            data={"user": {"role": "member", "id": account.id, "name": f"{account.first_name} {account.last_name}", "email": account.email, "company_name": account.company_name, "intake_key": account.intake_key}},
        )

    def list_members(self) -> list[dict]:
        locations = self.store.list_locations()
        stock = self.store.list_stock()
        views = []
        for account in self.store.list_users():
            owned = [item for item in locations if item.owner_id == account.id]
            owned_ids = {item.id for item in owned}
            view = self._account_view(account)
            view["block_count"] = len({item.block for item in owned})
            view["shelf_count"] = len(owned)
            view["stock_count"] = sum(1 for row in stock if row.location_id in owned_ids)
            views.append(view)
        return views

    def approve_member(self, user_id: str) -> Result:
        account = self.store.get_user(user_id)
        if account is None:
            return Result(False, "not_found", "Üye bulunamadı", 404)
        if account.mail_sent:
            return Result(True, "already_processed", f"{account.first_name} {account.last_name} için onay maili zaten gitti.", data={"account": self._account_view(account), "mail_sent": True})
        already = account.status == "onaylandı"
        account.status = "onaylandı"
        note = self._member_mail(account)
        form_sent = self._ping_signup(account)
        mailed = self._ping_approval(note)
        sent = form_sent is True or mailed is True
        if sent:
            account.mail_sent = True
            message = f"{account.first_name} {account.last_name} için onay maili n8n'e gitti."
        elif form_sent is False or mailed is False:
            message = f"{account.first_name} {account.last_name} onaylandı. n8n'e ulaşılamadı, mail gitmedi."
        else:
            message = f"{account.first_name} {account.last_name} onaylandı. Mail gitmedi: n8n formu bağlı değil."
        self.store.save_user(account)
        code = "already_processed" if already else "approved"
        return Result(True, code, message, data={"account": self._account_view(account), "notification": note, "mail_sent": sent})

    def reject_member(self, user_id: str) -> Result:
        account = self.store.get_user(user_id)
        if account is None:
            return Result(False, "not_found", "Üye bulunamadı", 404)
        account.status = "reddedildi"
        self.store.save_user(account)
        return Result(True, "rejected", "Üyelik reddedildi", data={"account": self._account_view(account)})

    def send_member_message(self, user_id: str, body: str) -> Result:
        account = self.store.get_user(user_id)
        if account is None:
            return Result(False, "not_found", "Üye bulunamadı", 404)
        text = body.strip()
        if not text:
            return Result(False, "invalid", "Mesaj boş olamaz", 400)
        if len(text) > 500:
            return Result(False, "invalid", "Mesaj en fazla 500 karakter olsun", 400)
        self.store.insert_message(str(uuid.uuid4()), account.id, text, now_iso())
        return Result(True, "sent", f"{account.first_name} {account.last_name} bildirimine düştü", 201)

    def send_support(self, user_id: str, channel: str, body: str) -> Result:
        account = self.store.get_user(user_id)
        if account is None:
            return Result(False, "not_found", "Üye bulunamadı", 404)
        text = body.strip()
        if not text:
            return Result(False, "invalid", "Mesaj boş olamaz", 400)
        if len(text) > 500:
            return Result(False, "invalid", "Mesaj en fazla 500 karakter olsun", 400)
        spec = channel_by_code(channel.strip())
        label = spec["name"] if spec else channel.strip()[:40]
        self.store.insert_support(
            str(uuid.uuid4()),
            account.id,
            f"{account.first_name} {account.last_name}",
            account.email,
            account.company_name,
            label,
            text,
            now_iso(),
        )
        return Result(True, "sent", "Mesajın yönetime ulaştı", 201)

    def list_support(self) -> list[dict]:
        return self.store.list_support()

    def reply_support(self, message_id: str, body: str) -> Result:
        note = self.store.get_support(message_id)
        if note is None:
            return Result(False, "not_found", "Mesaj bulunamadı", 404)
        text = body.strip()
        if not text:
            return Result(False, "invalid", "Cevap boş olamaz", 400)
        if len(text) > 500:
            return Result(False, "invalid", "Cevap en fazla 500 karakter olsun", 400)
        account = self.store.get_user(note["user_id"])
        if account is None:
            return Result(False, "not_found", "Üye silinmiş, cevap iletilemedi", 404)
        self.store.insert_message(str(uuid.uuid4()), account.id, text, now_iso())
        self.store.save_support_reply(message_id, text, now_iso())
        self._post_n8n(
            self.settings.reply_webhook,
            {
                "type": "admin_reply",
                "to": account.email,
                "subject": "Depo panelinden cevap",
                "body": text,
                "name": f"{account.first_name} {account.last_name}",
            },
        )
        return Result(True, "sent", f"Cevap {account.first_name} {account.last_name} bildirimine düştü", 201)

    def delete_member(self, user_id: str) -> Result:
        def op():
            account = self.store.get_user(user_id)
            if account is None:
                raise Leave(Result(False, "not_found", "Üye bulunamadı", 404))
            self.store.delete_member_data(user_id)
            return Result(True, "deleted", f"{account.first_name} {account.last_name} silindi")

        return self._run(op)

    def list_my_messages(self, user_id: str) -> list[dict]:
        return self.store.list_messages(user_id)

    def mark_my_messages_seen(self, user_id: str) -> Result:
        self.store.mark_messages_seen(user_id)
        return Result(True, "seen", "Bildirimler görüldü")

    def list_block_codes(self, owner: str = "") -> list[str]:
        codes = set(self.store.list_block_codes(owner))
        codes.update(location.block for location in self.store.list_locations() if location.owner_id == owner and location.block)
        return sorted(codes)

    def add_block(self, code: str, owner: str = "") -> Result:
        code = code.strip().upper()
        if not re.fullmatch(r"[A-Z0-9]{1,8}", code):
            return Result(False, "invalid", "Blok harf veya sayı olmalı", 400)
        if code in self.list_block_codes(owner):
            return Result(False, "duplicate", "Bu blok zaten var", 409)
        self.store.insert_block_code(code, owner)
        return Result(True, "created", f"{code} bloğu eklendi", 201, {"block": code})

    def delete_block(self, code: str, owner: str = "") -> Result:
        code = code.strip().upper()
        shelves = [item for item in self.store.list_locations() if item.owner_id == owner and item.block.upper() == code]
        for shelf in shelves:
            self.store.delete_location(shelf.id)
        self.store.delete_block_code(code, owner)
        return Result(True, "deleted", f"{code} bloğu silindi")

    def delete_shelf(self, location_id: str, owner: str = "") -> Result:
        location = self.store.get_location(location_id)
        if location is None or location.owner_id != owner:
            return Result(False, "not_found", "Raf bulunamadı", 404)
        self.store.delete_location(location_id)
        return Result(True, "deleted", f"{location_label(location.block, location.shelf_code)} silindi")

    def delete_stock_line(self, stock_id: str, owner: str = "") -> Result:
        row = next((item for item in self.store.list_stock() if item.id == stock_id), None)
        location = self.store.get_location(row.location_id) if row else None
        if row is None or location is None or location.owner_id != owner:
            return Result(False, "not_found", "Stok satırı bulunamadı", 404)
        self.store.delete_stock_row(stock_id)
        return Result(True, "deleted", "Ürün raftan silindi")

    def bump_stock(self, stock_id: str, delta: int, owner: str = "") -> Result:
        if delta not in (1, -1):
            return Result(False, "invalid", "Adet bir artar veya bir azalır", 400)
        row = next((item for item in self.store.list_stock() if item.id == stock_id), None)
        location = self.store.get_location(row.location_id) if row else None
        if row is None or location is None or location.owner_id != owner:
            return Result(False, "not_found", "Stok satırı bulunamadı", 404)
        if row.quantity + delta <= 0:
            self.store.delete_stock_row(stock_id)
            return Result(True, "deleted", "Adet sıfırlandı, ürün raftan kalktı", data={"quantity": 0})
        new_qty = self.store.adjust_stock(row.product_id, row.location_id, delta, capacity_check=False)
        return Result(True, "updated", f"Adet {new_qty}", data={"quantity": new_qty})

    def add_location(self, block: str, shelf_code: str, capacity: int | None = None, owner: str = "") -> Result:
        block = block.strip().upper()
        shelf_code = shelf_code.strip()
        if not re.fullmatch(r"[A-Z0-9]{1,8}", block):
            return Result(False, "invalid", "Blok harf veya sayı olmalı", 400)
        if not shelf_code or len(shelf_code) > 32:
            return Result(False, "invalid", "Raf kodu gerekli", 400)
        if self.store.find_location(block, shelf_code, owner):
            return Result(False, "duplicate", "Bu blok ve raf zaten var", 409)
        self.store.insert_block_code(block, owner)
        location = Location(id=str(uuid.uuid4()), block=block, shelf_code=shelf_code, capacity=None, active=True, owner_id=owner)
        self.store.insert_location(location)
        return Result(True, "created", f"{location_label(block, shelf_code)} eklendi", 201, {"location": self._location_option(location)})

    def place_stock(self, product_name: str, block: str, shelf_code: str, quantity: int, confirmed: bool, owner: str = "") -> Result:
        name = product_name.strip()
        block = block.strip().upper()
        shelf_code = shelf_code.strip()
        if not name:
            return Result(False, "invalid", "Ürün adı gerekli", 400)
        if quantity <= 0:
            return Result(False, "invalid", "Adet pozitif olmalı", 400)
        location = self.store.find_location(block, shelf_code, owner)
        if location is None:
            return Result(False, "not_found", "Raf bulunamadı", 404)
        product = next((item for item in self.store.list_products() if fold(item.product_name) == fold(name)), None)
        summary = stock_sentence(name, quantity, location.block, location.shelf_code)
        current = self._qty(product.id, location.id) if product else 0
        data = {
            "summary": summary,
            "product_name": name,
            "block": location.block,
            "shelf_code": location.shelf_code,
            "location_id": location.id,
            "quantity": quantity,
            "current_quantity": current,
            "resulting_quantity": current + quantity,
        }
        if not confirmed:
            return Result(True, "preview", summary, data=data)

        def op():
            chosen = product or self._create_named_product(name)
            new_qty = self.store.adjust_stock(chosen.id, location.id, quantity, capacity_check=False)
            payload = {**data, "resulting_quantity": new_qty, "product_id": chosen.id}
            return Result(True, "added", summary, data=payload)

        return self._run(op)

    def _create_named_product(self, name: str) -> Product:
        base = re.sub(r"[^A-Z0-9]+", "-", fold(name).upper()).strip("-") or "URUN"
        sku = base[:24]
        taken = {fold(item.sku) for item in self.store.list_products()}
        if fold(sku) in taken:
            sku = f"{sku[:18]}-{uuid.uuid4().hex[:4].upper()}"
        product = Product(id=str(uuid.uuid4()), sku=sku, product_name=name)
        self.store.insert_product(product)
        return product

    def add_product(self, sku: str, product_name: str, brand: str = "") -> Result:
        sku = sku.strip().upper()
        product_name = product_name.strip()
        if not sku or not product_name:
            return Result(False, "invalid", "SKU ve ürün adı gerekli", 400)
        if any(fold(item.sku) == fold(sku) for item in self.store.list_products()):
            return Result(False, "duplicate", "Bu SKU zaten var", 409)
        product = Product(id=str(uuid.uuid4()), sku=sku, product_name=product_name, brand=brand.strip())
        self.store.insert_product(product)
        return Result(True, "created", f"{product.product_name} kataloga eklendi", 201, {"product": {"id": product.id, "sku": product.sku, "product_name": product.product_name}})

    def list_product_options(self) -> list[dict]:
        return [
            {"id": product.id, "sku": product.sku, "product_name": product.product_name, "brand": product.brand}
            for product in self.store.list_products()
        ]

    def list_location_options(self, active_only: bool = True, owner: str = "") -> list[dict]:
        rows = []
        for location in self.store.list_locations():
            if location.owner_id != owner:
                continue
            if active_only and not location.active:
                continue
            rows.append(self._location_option(location))
        rows.sort(key=lambda row: (row["block"], shelf_key(row["shelf_code"])))
        return rows

    def _run(self, fn):
        try:
            with self.store.transaction():
                return fn()
        except Leave as exc:
            return exc.result

    def _choose(self, product_id: str, quantity: int, owner: str = ""):
        spots = [spot for spot in self._positions(product_id, owner) if spot["active"] and spot["quantity"] > 0]
        spots.sort(key=lambda spot: (spot["block"], shelf_key(spot["shelf_code"])))
        for spot in spots:
            if spot["quantity"] >= quantity:
                return self.store.get_location(spot["location_id"]), None
        if spots:
            spot = spots[0]
            note = f"Bu rafta {spot['quantity']} adet var, sipariş {quantity} adet"
            return self.store.get_location(spot["location_id"]), note
        return None, "Stokta yok"

    def _positions(self, product_id: str, owner: str = "") -> list[dict]:
        locations = {item.id: item for item in self.store.list_locations() if item.owner_id == owner}
        rows = []
        for stock in self.store.list_stock():
            if stock.product_id != product_id:
                continue
            location = locations.get(stock.location_id)
            if location is None:
                continue
            rows.append(
                {
                    "location_id": location.id,
                    "block": location.block,
                    "shelf_code": location.shelf_code,
                    "quantity": stock.quantity,
                    "active": location.active,
                    "capacity": location.capacity,
                }
            )
        return rows

    def _qty(self, product_id: str, location_id: str) -> int:
        row = self.store.find_stock(product_id, location_id)
        return row.quantity if row else 0

    def _suitable(self, location: Location | None, product_id: str, quantity: int) -> tuple[bool, str]:
        if location is None:
            return False, "raf kaydı yok"
        if not location.active:
            return False, "raf kaldırılmış"
        current = self._qty(product_id, location.id)
        if location.capacity is not None and current + quantity > location.capacity:
            return False, "kapasite dolu"
        return True, ""

    def _unsuitable(self, item: ReturnRequest, location: Location | None, why: str) -> Result:
        where = location_label(location.block, location.shelf_code) if location else "kayıp raf"
        return Result(
            False,
            "location_unsuitable",
            f"Orijinal raf uygun değil ({where}, {why}), alternatif seçin",
            409,
            {"return": self._return_view(item), "locations": self.list_location_options(True)},
        )

    def _remember_alias(self, product: Product, raw_text: str) -> None:
        text = raw_text.strip()
        if not text:
            return
        folded = fold(text)
        known = {fold(product.product_name), fold(product.sku), fold(product.barcode)}
        known.update(fold(alt) for alt in product.alt_list())
        if folded in known:
            return
        product.alt_names = f"{product.alt_names} | {text}".strip(" |")
        self.store.save_product(product)

    def _order_matches_code(self, order: Order, wanted: str) -> bool:
        product = self.store.get_product(order.matched_product_id or "")
        if product is None:
            return False
        return fold(product.sku) == wanted or fold(product.barcode) == wanted

    def _candidate(self, item) -> dict:
        return {
            "product_id": item.product.id,
            "sku": item.product.sku,
            "product_name": item.product.product_name,
            "confidence": item.confidence,
        }

    def _stock_views(self, owner: str = "") -> list[dict]:
        products = {item.id: item for item in self.store.list_products()}
        locations = {item.id: item for item in self.store.list_locations() if item.owner_id == owner}
        rows = []
        for stock in self.store.list_stock():
            product = products.get(stock.product_id)
            location = locations.get(stock.location_id)
            if product is None or location is None:
                continue
            rows.append(
                {
                    "stock_id": stock.id,
                    "product_id": product.id,
                    "sku": product.sku,
                    "product_name": product.product_name,
                    "brand": product.brand,
                    "category": product.category,
                    "location_id": location.id,
                    "block": location.block,
                    "shelf_code": location.shelf_code,
                    "location_label": location_label(location.block, location.shelf_code),
                    "quantity": stock.quantity,
                    "capacity": location.capacity,
                    "active": location.active,
                }
            )
        return rows

    def _order_view(self, order: Order) -> dict:
        product = self.store.get_product(order.matched_product_id) if order.matched_product_id else None
        loc_id = order.source_location_id or order.proposed_location_id
        location = self.store.get_location(loc_id) if loc_id else None
        sentence = None
        if product and not order.needs_manual_match:
            sentence = order_sentence(
                order.quantity,
                product.product_name,
                location.block if location else None,
                location.shelf_code if location else None,
                order.match_confidence,
                order.stock_note if order.stock_note and order.stock_note != "Stokta yok" else (None if location else "Stokta yok"),
            )
        elif order.needs_manual_match:
            sentence = manual_sentence(order.candidates)
        return {
            "id": order.id,
            "order_id": order.order_id,
            "source": order.source,
            "raw_product_text": order.raw_product_text,
            "quantity": order.quantity,
            "status": order.status,
            "match_confidence": order.match_confidence,
            "match_method": order.match_method,
            "match_method_label": METHOD_LABELS.get(order.match_method or "", order.match_method),
            "needs_manual_match": order.needs_manual_match,
            "matched_product": None
            if product is None
            else {"id": product.id, "sku": product.sku, "product_name": product.product_name},
            "location_label": location_label(location.block, location.shelf_code) if location else None,
            "location_role": "source" if order.source_location_id else ("proposed" if order.proposed_location_id else None),
            "stock_note": order.stock_note,
            "candidates": order.candidates,
            "sentence": sentence,
            "created_at": order.created_at,
            "owner_id": order.owner_id,
        }

    def _return_view(self, item: ReturnRequest) -> dict:
        order = self.store.get_order(item.original_order_id)
        product = self.store.get_product(order.matched_product_id) if order and order.matched_product_id else None
        location = self.store.get_location(order.source_location_id) if order and order.source_location_id else None
        return {
            "id": item.id,
            "order_record_id": item.original_order_id,
            "order_id": order.order_id if order else "",
            "source": order.source if order else "",
            "product_name": product.product_name if product else "",
            "quantity": order.quantity if order else 0,
            "return_reason": item.return_reason,
            "status": item.status,
            "original_location_label": location_label(location.block, location.shelf_code) if location else None,
            "restock_note": item.restock_note,
            "created_at": item.created_at,
        }

    def _location_option(self, location: Location) -> dict:
        return {
            "id": location.id,
            "block": location.block,
            "shelf_code": location.shelf_code,
            "label": location_label(location.block, location.shelf_code),
            "capacity": location.capacity,
            "active": location.active,
        }

    def _order_notification(self, order: Order) -> dict:
        view = self._order_view(order)
        url = f"{self.settings.public_base_url}/approve/order/{order.approval_token}"
        body = view.get("sentence") or manual_sentence(order.candidates)
        if order.needs_manual_match:
            body += f"\n\nSipariş {order.order_id} manuel onay bekliyor.\nOnay linki, panelden eşleştirme yapıldıktan sonra çalışır: {url}"
        else:
            body += f"\n\nOnay linki: {url}"
        return {
            "to": self.settings.notify_email,
            "subject": f"Sipariş {order.order_id} ({order.source})",
            "body": body,
            "approval_url": url,
            "needs_manual_match": order.needs_manual_match,
        }

    def _return_notification(self, item: ReturnRequest, order: Order) -> dict:
        url = f"{self.settings.public_base_url}/approve/return/{item.approval_token}"
        body = f"İade talebi {order.order_id}. Onay stoğa eklemez, ürünü kontrol beklemeye alır.\n\nOnay linki: {url}"
        return {"to": self.settings.notify_email, "subject": f"İade {order.order_id}", "body": body, "approval_url": url}

    def _inspect_notification(self, item: ReturnRequest) -> dict:
        url = f"{self.settings.public_base_url}/inspect/return/{item.inspect_token}"
        view = self._return_view(item)
        order = self.store.get_order(item.original_order_id)
        account = self.store.get_user(order.owner_id) if order and order.owner_id else None
        body = f"{view['order_id']} iadesi fiziksel kontrol bekliyor. Sağlamsa orijinal rafa eklenir.\n\nKontrol linki: {url}"
        return {"to": account.email if account else self.settings.notify_email, "subject": "İade kontrolü", "body": body, "inspect_url": url}

    def _account_view(self, account: Account) -> dict:
        return {
            "id": account.id,
            "first_name": account.first_name,
            "last_name": account.last_name,
            "email": account.email,
            "company_name": account.company_name,
            "status": account.status,
            "created_at": account.created_at,
            "mail_sent": account.mail_sent,
        }

    def _member_mail(self, account: Account) -> dict:
        login_url = f"{self.settings.public_base_url}/"
        body = (
            f"Merhaba {account.first_name} {account.last_name},\n\n"
            f"{account.company_name} için depo paneli üyeliğiniz onaylandı.\n"
            f"E-posta adresiniz ve belirlediğiniz parola ile giriş yapabilirsiniz.\n\n"
            f"Giriş: {login_url}"
        )
        return {
            "to": account.email,
            "subject": "Depo paneli üyeliğiniz onaylandı",
            "body": body,
            "first_name": account.first_name,
            "last_name": account.last_name,
            "company_name": account.company_name,
        }

    def _ping_signup(self, account: Account) -> bool | None:
        url = self.settings.signup_form
        if not url:
            return None
        try:
            response = httpx.post(
                url,
                files={
                    "field-0": (None, account.first_name),
                    "field-1": (None, account.last_name),
                    "field-2": (None, account.email),
                    "field-3": (None, account.company_name),
                },
                timeout=25,
            )
            return response.status_code < 400
        except Exception:
            log.warning("Üye ol formu n8n'e ulaşamadı")
            return False

    def _ping_approval(self, note: dict) -> bool | None:
        url = self.settings.approval_webhook
        if not url:
            log.info("N8N_APPROVAL_WEBHOOK boş, onay maili gönderilmedi")
            return None
        try:
            httpx.post(url, json={"type": "member_approved", **note}, timeout=5)
        except Exception:
            log.warning("Üye onay webhook'u ulaşılamadı")
            return False
        return True

    def reminder_feed(self) -> dict:
        pending = []
        for order in self.store.list_orders():
            if order.status != "beklemede" or self._hours_old(order.created_at) < 24:
                continue
            account = self.store.get_user(order.owner_id) if order.owner_id else None
            pending.append(
                {
                    "to": account.email if account else self.settings.notify_email,
                    "subject": "Bekleyen sipariş var",
                    "body": f"{order.source} siparişi {order.order_id} bir gündür onay bekliyor. Stok, onaylayınca düşer.",
                    "order_id": order.order_id,
                    "source": order.source,
                    "product_name": order.raw_product_text,
                    "quantity": order.quantity,
                }
            )
        low_stock = []
        for account in self.store.list_users():
            for row in self._stock_views(account.id):
                if row["quantity"] > self.settings.low_stock_at:
                    continue
                low_stock.append(
                    {
                        "to": account.email,
                        "subject": "Stok azaldı",
                        "body": f"{row['product_name']} {row['location_label']} rafında {row['quantity']} adet kaldı.",
                        "product_name": row["product_name"],
                        "location_label": row["location_label"],
                        "quantity": row["quantity"],
                    }
                )
        return {"pending": pending, "low_stock": low_stock}

    def daily_feed(self) -> list[dict]:
        pending = [order for order in self.store.list_orders() if order.status == "beklemede"]
        rows = []
        for account in self.store.list_users():
            if account.status != "onaylandı":
                continue
            mine = [order for order in pending if order.owner_id == account.id]
            low = [row for row in self._stock_views(account.id) if row["quantity"] <= self.settings.low_stock_at]
            if not mine and not low:
                continue
            lines = []
            if mine:
                lines.append(f"Bekleyen sipariş: {len(mine)}")
                for order in mine:
                    name = order.raw_product_text or "ürün"
                    lines.append(f"- {order.source} {order.order_id}: {order.quantity} adet {name}")
            if low:
                lines.append(f"Azalan stok: {len(low)}")
                for row in low:
                    lines.append(f"- {row['product_name']} {row['location_label']} rafında {row['quantity']} adet")
            rows.append({"to": account.email, "subject": "Günlük depo özeti", "body": "\n".join(lines)})
        return rows

    def channel_feed(self) -> list[dict]:
        users = {account.id: account for account in self.store.list_users()}
        rows = []
        for link in self.store.list_all_channel_links():
            if link["channel"] not in {"hepsiburada", "n11", "amazon"}:
                continue
            account = users.get(link["user_id"])
            if account is None or not account.intake_key:
                continue
            try:
                credentials = open_sealed(link["secrets"], self.settings.panel_secret)
            except (ValueError, KeyError):
                continue
            rows.append(
                {
                    "channel": link["channel"],
                    "to": account.email,
                    "intake_url": f"{self.settings.public_base_url}/api/webhooks/in/{account.intake_key}",
                    "credentials": credentials,
                }
            )
        return rows

    def _ping_new_order(self, order: dict) -> None:
        account = self.store.get_user(str(order.get("owner_id") or "")) if order.get("owner_id") else None
        name = order.get("raw_product_text") or "ürün"
        self._post_n8n(
            self.settings.order_webhook,
            {
                "type": "new_order",
                "to": account.email if account else self.settings.notify_email,
                "subject": "Yeni sipariş var",
                "body": f"{order.get('source') or 'Sipariş'} {order.get('order_id')}: {order.get('quantity')} adet {name}. Stok, onaylayınca düşer.",
                "order_id": order.get("order_id"),
                "source": order.get("source"),
                "product_name": name,
                "quantity": order.get("quantity"),
            },
        )

    def _ping_low_stock(self, data: dict) -> None:
        after = data.get("stock_after")
        if after is None or int(after) > self.settings.low_stock_at:
            return
        order = data.get("order") or {}
        account = self.store.get_user(str(order.get("owner_id") or "")) if order.get("owner_id") else None
        product = (order.get("matched_product") or {}).get("product_name") or order.get("raw_product_text") or "ürün"
        self._post_n8n(
            self.settings.low_stock_webhook,
            {
                "type": "low_stock",
                "to": account.email if account else self.settings.notify_email,
                "subject": "Stok azaldı",
                "body": f"{product} {order.get('location_label') or 'rafta'} {after} adet kaldı.",
                "product_name": product,
                "quantity": after,
            },
        )

    def _ping_channel_error(self, owner: str, channel_name: str, message: str) -> None:
        account = self.store.get_user(owner)
        self._post_n8n(
            self.settings.channel_error_webhook,
            {
                "type": "channel_error",
                "to": account.email if account else self.settings.notify_email,
                "subject": "Kanal bağlantısı koptu",
                "body": f"{channel_name} bağlantısı kurulamadı. {message}",
                "channel": channel_name,
            },
        )

    def _ping_channel(self, owner: str, channel: str, credentials: dict) -> None:
        account = self.store.get_user(owner)
        if account is None or not account.intake_key:
            return
        self._post_n8n(
            self.settings.channel_webhook,
            {
                "type": "channel_sync",
                "channel": channel,
                "to": account.email,
                "intake_url": f"{self.settings.public_base_url}/api/webhooks/in/{account.intake_key}",
                "credentials": credentials,
            },
        )

    def _hours_old(self, stamp: str) -> float:
        if not stamp:
            return 0
        try:
            moment = datetime.fromisoformat(stamp)
        except ValueError:
            return 0
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - moment).total_seconds() / 3600

    def _post_n8n(self, url: str, payload: dict) -> None:
        if not url:
            return
        try:
            httpx.post(url, json=payload, timeout=8)
        except Exception:
            log.warning("n8n adresine ulaşılamadı")

    def _ping_inspect(self, data: dict) -> None:
        note = (data or {}).get("notification") or {}
        self._post_n8n(self.settings.inspect_webhook, {"type": "return_inspection", **note})
