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
    user = _require_user(request)
    return {"ok": True, "code": "ok", "message": "", "data": {"user": user}}


@router.get("/api/stock")
def stock(request: Request, q: str = "", block: str = ""):
    _require_user(request)
    return {"ok": True, "data": {"rows": _service(request).list_stock(q, block, _owner(request))}}


@router.get("/api/orders")
def orders(request: Request, status: str = "", source: str = ""):
    _require_user(request)
    return {"ok": True, "data": {"rows": _service(request).list_orders(status, source)}}


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


async def _ingest(request: Request, source: str | None):
    try:
        payload = await request.json()
        lines = normalize(payload, source)
    except PayloadError as exc:
        return _json(Result(False, "invalid", exc.message, 400))
    except Exception:
        return _json(Result(False, "invalid", "Sipariş gövdesi okunamadı", 400))
    results = [_service(request).ingest_order(line).json() for line in lines]
    if any(item["code"] == "created" for item in results):
        status = 201
    elif results and not results[0]["ok"]:
        status = 400
    else:
        status = 200
    return JSONResponse({"ok": all(item["ok"] for item in results), "results": results}, status_code=status)
