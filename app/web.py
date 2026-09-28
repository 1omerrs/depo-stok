from __future__ import annotations

import secrets
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse

from app.normalize import PayloadError, normalize
from app.pages import render_page
from app.results import Result

router = APIRouter()

TONES = {
    "completed": "ok",
    "restocked": "ok",
    "added": "ok",
    "matched": "ok",
    "created": "ok",
    "already_processed": "info",
    "duplicate": "info",
    "inspection_pending": "info",
    "pending": "info",
    "needs_manual_match": "warn",
    "insufficient_stock": "warn",
    "location_unsuitable": "warn",
    "not_found": "bad",
    "invalid_state": "bad",
}


def _service(request: Request):
    return request.app.state.service


def _settings(request: Request):
    return request.app.state.settings


def _json(result: Result) -> JSONResponse:
    return JSONResponse(result.json(), status_code=result.status_code)


def _require_user(request: Request) -> dict:
    user = request.session.get("user")
    if isinstance(user, str):
        user = {"role": "admin", "name": user}
    if not isinstance(user, dict) or not user.get("role"):
        raise HTTPException(status_code=401, detail="Giriş gerekli")
    return user


def _owner(request: Request) -> str:
    user = _require_user(request)
    if user.get("role") != "member":
        return ""
    return str(user.get("id") or "")


def _require_admin(request: Request) -> dict:
    user = _require_user(request)
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Bu sayfa yalnızca yönetici içindir")
    return user


def _webhook_ok(request: Request) -> None:
    secret = _settings(request).webhook_secret
    if not secret:
        return
    got = request.headers.get("x-webhook-token", "")
    if not secrets.compare_digest(got, secret):
        raise HTTPException(status_code=401, detail="Webhook anahtarı geçersiz")


def _page(result: Result, token_path: str, label: str) -> HTMLResponse:
    tone = TONES.get(result.code, "bad" if not result.ok else "info")
    action = token_path if (result.data or {}).get("can_confirm") else None
    html = render_page(title="Depo onayı", message=result.message, tone=tone, action=action, action_label=label)
    return HTMLResponse(html, status_code=result.status_code, headers={"Cache-Control": "no-store"})


@router.get("/api/health")
def health(request: Request):
    store = request.app.state.store
    try:
        count = len(store.list_products())
    except Exception as exc:
        return JSONResponse({"ok": False, "backend": _settings(request).data_backend, "message": str(exc)}, status_code=503)
    return {"ok": True, "backend": _settings(request).data_backend, "products": count}


@router.get("/api/n8n/reminders")
def n8n_reminders(request: Request):
    _webhook_ok(request)
    return {"ok": True, "data": _service(request).reminder_feed()}


@router.get("/api/n8n/channels")
def n8n_channels(request: Request):
    _webhook_ok(request)
    return {"ok": True, "data": {"rows": _service(request).channel_feed()}}


@router.get("/api/n8n/summary")
def n8n_summary(request: Request):
    _webhook_ok(request)
    return {"ok": True, "data": {"rows": _service(request).daily_feed()}}


@router.get("/api/meta")
def meta(request: Request):
    settings = _settings(request)
    return {"ok": True, "data": {"mail_configured": bool(settings.approval_webhook or settings.signup_form)}}


@router.post("/api/login")
def login(request: Request, body: dict):
    result = _service(request).authenticate(str(body.get("username", "")), str(body.get("password", "")))
    if result.ok:
        request.session["user"] = result.data["user"]
    return _json(result)


@router.post("/api/admin/login")
def admin_login(request: Request, body: dict):
    result = _service(request).authenticate_admin(str(body.get("username", "")), str(body.get("password", "")))
    if result.ok:
        request.session["user"] = result.data["user"]
    return _json(result)


@router.post("/api/register")
def register(request: Request, body: dict):
    return _json(
        _service(request).register(
            str(body.get("first_name", "")),
            str(body.get("last_name", "")),
            str(body.get("email", "")),
            str(body.get("company_name", "")),
            str(body.get("password", "")),
        )
    )


@router.get("/api/members")
def members(request: Request):
    _require_admin(request)
    return {"ok": True, "data": {"rows": _service(request).list_members()}}


@router.post("/api/members/{user_id}/approve")
def approve_member(user_id: str, request: Request):
    _require_admin(request)
    return _json(_service(request).approve_member(user_id))


@router.post("/api/members/{user_id}/reject")
def reject_member(user_id: str, request: Request):
    _require_admin(request)
    return _json(_service(request).reject_member(user_id))


@router.delete("/api/members/{user_id}")
def delete_member(user_id: str, request: Request):
    _require_admin(request)
    return _json(_service(request).delete_member(user_id))


@router.get("/api/support")
def support_inbox(request: Request):
    _require_admin(request)
    return {"ok": True, "data": {"rows": _service(request).list_support()}}


@router.post("/api/support/{message_id}/reply")
async def reply_support(message_id: str, request: Request):
    _require_admin(request)
    body = await request.json()
    return _json(_service(request).reply_support(message_id, str(body.get("body") or "")))


@router.post("/api/support")
async def send_support(request: Request):
    user = _require_user(request)
    if user.get("role") != "member":
        raise HTTPException(status_code=403, detail="Bu işlem üye hesabı içindir")
    body = await request.json()
    return _json(_service(request).send_support(str(user.get("id") or ""), str(body.get("channel") or ""), str(body.get("body") or "")))


@router.post("/api/members/{user_id}/message")
def member_message(user_id: str, request: Request, body: dict):
    _require_admin(request)
    return _json(_service(request).send_member_message(user_id, str(body.get("body", ""))))


@router.get("/api/messages")
def my_messages(request: Request):
    user = _require_user(request)
    if user.get("role") != "member":
        return {"ok": True, "data": {"rows": []}}
    return {"ok": True, "data": {"rows": _service(request).list_my_messages(str(user.get("id") or ""))}}


@router.post("/api/messages/seen")
def see_messages(request: Request):
    user = _require_user(request)
    if user.get("role") != "member":
        return {"ok": True, "code": "seen", "message": "", "data": {}}
    return _json(_service(request).mark_my_messages_seen(str(user.get("id") or "")))


@router.get("/api/blocks")
def blocks(request: Request):
    _require_user(request)
    return {"ok": True, "data": {"blocks": _service(request).list_block_codes(_owner(request))}}


@router.post("/api/blocks")
def create_block(request: Request, body: dict):
    _require_user(request)
    return _json(_service(request).add_block(str(body.get("code", "")), _owner(request)))


@router.delete("/api/blocks/{code}")
def remove_block(code: str, request: Request):
    _require_user(request)
    return _json(_service(request).delete_block(code, _owner(request)))


@router.delete("/api/locations/{location_id}")
def remove_location(location_id: str, request: Request):
    _require_user(request)
    return _json(_service(request).delete_shelf(location_id, _owner(request)))


@router.post("/api/stock/{stock_id}/adjust")
def adjust_stock_line(stock_id: str, request: Request, body: dict):
    _require_user(request)
    try:
        delta = int(body.get("delta"))
    except (TypeError, ValueError):
        return _json(Result(False, "invalid", "Adet bir artar veya bir azalır", 400))
    return _json(_service(request).bump_stock(stock_id, delta, _owner(request)))


@router.delete("/api/stock/{stock_id}")
def remove_stock(stock_id: str, request: Request):
    _require_user(request)
    return _json(_service(request).delete_stock_line(stock_id, _owner(request)))


@router.post("/api/stock/place")
def place_stock(request: Request, body: dict):
    _require_user(request)
    try:
        quantity = int(body.get("quantity"))
    except (TypeError, ValueError):
        return _json(Result(False, "invalid", "Adet sayı olmalı", 400))
    return _json(
        _service(request).place_stock(
            str(body.get("product_name", "")),
            str(body.get("block", "")),
            str(body.get("shelf_code", "")),
            quantity,
            bool(body.get("confirmed")),
            _owner(request),
        )
    )


@router.post("/api/locations")
def create_location(request: Request, body: dict):
    _require_user(request)
    return _json(_service(request).add_location(str(body.get("block", "")), str(body.get("shelf_code", "")), owner=_owner(request)))


@router.post("/api/products")
def create_product(request: Request, body: dict):
    _require_user(request)
    return _json(_service(request).add_product(str(body.get("sku", "")), str(body.get("product_name", "")), str(body.get("brand", ""))))


@router.post("/api/logout")
def logout(request: Request):
    request.session.clear()
    return {"ok": True, "code": "ok", "message": "Çıkış yapıldı", "data": {}}


@router.get("/api/me")
def me(request: Request):
    user = dict(_require_user(request))
    if user.get("role") == "member" and user.get("id"):
        account = _service(request).store.get_user(str(user["id"]))
        if account is not None:
            user["intake_key"] = account.intake_key
    return {"ok": True, "code": "ok", "message": "", "data": {"user": user}}


@router.get("/api/stock")
def stock(request: Request, q: str = "", block: str = ""):
    _require_user(request)
    service = _service(request)
    owner = _owner(request)
    return {"ok": True, "data": {"rows": service.list_stock(q, block, owner), "summary": service.stock_summary(owner)}}


@router.get("/api/channels")
def channels(request: Request):
    _require_user(request)
    if _owner(request) == "" and _require_user(request).get("role") != "member":
        return {"ok": True, "data": {"rows": []}}
    return {"ok": True, "data": {"rows": _service(request).list_channels(_owner(request))}}


@router.post("/api/channels/sync")
def sync_channels(request: Request):
    user = _require_user(request)
    if user.get("role") != "member":
        return _json(Result(True, "idle", "", data={"imported": 0}))
    return _json(_service(request).sync_channels(str(user.get("id") or "")))


@router.post("/api/channels/{code}")
async def connect_channel(code: str, request: Request):
    user = _require_user(request)
    if user.get("role") != "member":
        raise HTTPException(status_code=403, detail="Bu işlem üye hesabı içindir")
    body = await request.json()
    fields = body if isinstance(body, dict) else {}
    return _json(_service(request).save_channel(str(user.get("id") or ""), code, fields))


@router.delete("/api/channels/{code}")
def disconnect_channel(code: str, request: Request):
    user = _require_user(request)
    if user.get("role") != "member":
        raise HTTPException(status_code=403, detail="Bu işlem üye hesabı içindir")
    return _json(_service(request).disconnect_channel(str(user.get("id") or ""), code))


@router.get("/api/orders")
def orders(request: Request, status: str = "", source: str = ""):
    _require_user(request)
    return {"ok": True, "data": {"rows": _service(request).list_orders(status, source, _owner(request))}}


@router.get("/api/returns")
def returns(request: Request, status: str = ""):
    _require_user(request)
    return {"ok": True, "data": {"rows": _service(request).list_returns(status)}}


@router.get("/api/options")
def options(request: Request):
    _require_user(request)
    service = _service(request)
    owner = _owner(request)
    return {"ok": True, "data": {"products": service.list_product_options(), "locations": service.list_location_options(owner=owner)}}


@router.post("/api/orders/{order_pk}/match")
def match_order(order_pk: str, request: Request, body: dict):
    _require_user(request)
    return _json(_service(request).confirm_match(order_pk, str(body.get("product_id", ""))))


@router.post("/api/orders/{order_pk}/approve")
def approve_order_panel(order_pk: str, request: Request):
    _require_user(request)
    return _json(_service(request).approve_order(order_pk=order_pk))


@router.post("/api/returns")
def create_return(request: Request, body: dict):
    _require_user(request)
    return _json(
        _service(request).ingest_return(
            str(body.get("order_id", "")),
            str(body.get("return_reason", "")),
            body.get("sku"),
            body.get("barcode"),
        )
    )


@router.post("/api/returns/{return_pk}/approve")
def approve_return_panel(return_pk: str, request: Request):
    _require_user(request)
    return _json(_service(request).approve_return(return_pk=return_pk))


@router.post("/api/returns/{return_pk}/restock")
def restock_panel(return_pk: str, request: Request, body: dict | None = None):
    _require_user(request)
    body = body or {}
    return _json(_service(request).restock_return(return_pk=return_pk, alternate_location_id=body.get("location_id")))


@router.post("/api/returns/{return_pk}/reject")
def reject_panel(return_pk: str, request: Request):
    _require_user(request)
    return _json(_service(request).reject_return(return_pk))


@router.post("/api/stock/preview")
def preview_stock(request: Request, body: dict):
    _require_user(request)
    try:
        quantity = int(body.get("quantity"))
    except (TypeError, ValueError):
        return _json(Result(False, "invalid", "Adet sayı olmalı", 400))
    return _json(_service(request).preview_add(str(body.get("product_id", "")), str(body.get("location_id", "")), quantity, _owner(request)))


@router.post("/api/stock/add")
def add_stock(request: Request, body: dict):
    _require_user(request)
    try:
        quantity = int(body.get("quantity"))
    except (TypeError, ValueError):
        return _json(Result(False, "invalid", "Adet sayı olmalı", 400))
    return _json(
        _service(request).add_stock(
            str(body.get("product_id", "")),
            str(body.get("location_id", "")),
            quantity,
            bool(body.get("confirmed")),
            _owner(request),
        )
    )


@router.post("/api/webhooks/orders")
@router.post("/api/webhooks/trendyol")
@router.post("/api/webhooks/amazon")
@router.post("/api/webhooks/website")
async def webhook_order(request: Request):
    _webhook_ok(request)
    source = {"trendyol": "Trendyol", "amazon": "Amazon", "website": "Kendi site"}.get(request.url.path.rsplit("/", 1)[-1])
    return await _ingest(request, source)


@router.post("/api/webhooks/in/{intake_key}")
async def webhook_member_order(intake_key: str, request: Request):
    account = _service(request).store.find_user_by_intake(intake_key)
    if account is None or account.status != "onaylandı":
        return _json(Result(False, "not_found", "Sipariş adresi bulunamadı", 404))
    return await _ingest(request, None, owner=account.id, open_source=True)


@router.post("/api/webhooks/returns")
async def webhook_return(request: Request):
    _webhook_ok(request)
    body = await request.json()
    result = _service(request).ingest_return(
        str(body.get("order_id") or body.get("orderNumber") or ""),
        str(body.get("return_reason") or body.get("reason") or ""),
        body.get("sku"),
        body.get("barcode"),
    )
    return _json(result)


@router.post("/api/approve/order/{token}")
def approve_order_json(token: str, request: Request):
    return _json(_service(request).approve_order(token=token))


@router.post("/api/approve/return/{token}")
def approve_return_json(token: str, request: Request):
    return _json(_service(request).approve_return(token=token))


@router.post("/api/inspect/return/{token}")
def inspect_json(token: str, request: Request):
    return _json(_service(request).restock_return(token=token))


@router.get("/approve/order/{token}", response_class=HTMLResponse)
def approve_order_page(token: str, request: Request):
    return _page(_service(request).describe_order_token(token), f"/approve/order/{token}", "Onayla ve stoktan düş")


@router.post("/approve/order/{token}", response_class=HTMLResponse)
def approve_order_post(token: str, request: Request):
    return _page(_service(request).approve_order(token=token), "", "")


@router.get("/approve/return/{token}", response_class=HTMLResponse)
def approve_return_page(token: str, request: Request):
    return _page(_service(request).describe_return_token(token, "approval"), f"/approve/return/{token}", "İade talebini onayla")


@router.post("/approve/return/{token}", response_class=HTMLResponse)
def approve_return_post(token: str, request: Request):
    return _page(_service(request).approve_return(token=token), "", "")


@router.get("/inspect/return/{token}", response_class=HTMLResponse)
def inspect_page(token: str, request: Request):
    return _page(_service(request).describe_return_token(token, "inspect"), f"/inspect/return/{token}", "Sağlam, stoğa ekle")


@router.post("/inspect/return/{token}", response_class=HTMLResponse)
def inspect_post(token: str, request: Request):
    return _page(_service(request).restock_return(token=token), "", "")


async def _ingest(request: Request, source: str | None, owner: str = "", open_source: bool = False):
    try:
        payload = await request.json()
        lines = normalize(payload, source, open_source=open_source)
    except PayloadError as exc:
        return _json(Result(False, "invalid", exc.message, 400))
    except Exception:
        return _json(Result(False, "invalid", "Sipariş gövdesi okunamadı", 400))
    if owner:
        for line in lines:
            line.external_key = f"{owner}|{line.external_key}"
    results = [_service(request).ingest_order(line, owner).json() for line in lines]
    if any(item["code"] == "created" for item in results):
        status = 201
    elif results and not results[0]["ok"]:
        status = 400
    else:
        status = 200
    return JSONResponse({"ok": all(item["ok"] for item in results), "results": results}, status_code=status)
