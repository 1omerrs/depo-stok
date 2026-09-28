const state = {
  user: null,
  mailConfigured: false,
  view: "stok",
  stock: [],
  summary: { in_total: 0, out_total: 0, entries: [], exits: [] },
  orders: [],
  returns: [],
  products: [],
  locations: [],
  q: "",
  block: "",
  orderStatus: "",
  form: { product_id: "", location_id: "", quantity: 1 },
  preview: null,
  flash: null,
  unsuitable: null,
  returnForm: { order_id: "", return_reason: "fikir değişikliği" },
  busy: false,
  screen: "welcome",
  openBlock: "",
  openShelf: "",
  blocks: [],
  members: [],
  messages: [],
  noticeOpen: false,
  composeFor: "",
  channels: [],
  openChannel: "",
  support: [],
  helpFor: "",
  confirmDelete: "",
  replyFor: "",
};

const views = ["stok", "siparis", "iade", "uyeler", "kullanicilar", "mesajlar"];

function esc(value) {
  return String(value ?? "").replace(/[&<>"']/g, (ch) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch]));
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    method: options.method || "GET",
    credentials: "same-origin",
    headers: options.body ? { "Content-Type": "application/json" } : {},
    body: options.body ? JSON.stringify(options.body) : undefined,
  });
  let body = {};
  try { body = await response.json(); } catch { body = { ok: false, message: "Yanıt okunamadı" }; }
  if (response.status === 401 && path !== "/api/login") state.user = null;
  return { http: response.status, ...body };
}

function badge(status) {
  const tone = status === "tamamlandı" || status === "stoğa eklendi" ? "ok"
    : status === "iptal" || status === "reddedildi" ? "bad"
    : status === "beklemede" || status === "talep edildi" || status === "kontrol bekliyor" ? "warn" : "info";
  return `<span class="badge ${tone}">${esc(status)}</span>`;
}

function unansweredMessages() {
  return (state.support || []).filter((row) => !row.reply).length;
}

function shelfMeter(item) {
  const rows = state.stock.filter((row) => row.location_id === item.id);
  const qty = rows.reduce((sum, row) => sum + Number(row.quantity || 0), 0);
  const cap = Number(item.capacity) || 0;
  let pct = 8;
  if (cap > 0) pct = Math.round((qty / cap) * 100);
  else if (qty > 0) pct = Math.min(100, 16 + qty * 8);
  return { pct: Math.max(8, Math.min(100, pct)), hot: qty > 0 && qty <= 5 };
}

function meter(fills) {
  const bars = (fills.length ? fills : [{ pct: 8, hot: false }]).slice(0, 3).map((bar) => `<i class="${bar.hot ? "hot" : ""}" style="--fill:${bar.pct}%"></i>`).join("");
  return `<span class="meter" aria-hidden="true">${bars}</span>`;
}

function stockRows() {
  const needle = state.q.toLocaleLowerCase("tr");
  return state.stock.filter((row) => {
    if (state.block && row.block !== state.block) return false;
    if (!needle) return true;
    return `${row.sku} ${row.product_name} ${row.brand} ${row.block} ${row.shelf_code}`.toLocaleLowerCase("tr").includes(needle);
  });
}

function shell(content) {
  if (!state.user) {
    const flash = state.flash ? `<p class="flash bad">${esc(state.flash.text)}</p>` : "";
    if (state.screen === "admin") {
      return `<main class="wrap"><form class="card login" id="admin-login">
        <p class="mark">Depo Stok</p>
        <h1>Yönetim</h1>
        ${flash}
        <label>Kullanıcı adı<input name="username" autocomplete="username" required></label>
        <label>Parola<input name="password" type="password" autocomplete="current-password" required></label>
        <button class="primary" type="submit">Giriş yap</button>
      </form></main>`;
    }
    if (state.screen === "signup") {
      return `<main class="wrap"><form class="card login" id="signup">
        <p class="mark">Depo Stok</p>
        <h1>Üye ol</h1>
        ${flash}
        <label>İsim<input name="first_name" required></label>
        <label>Soyisim<input name="last_name" required></label>
        <label>E-posta<input name="email" type="email" required></label>
        <label>Şirket adı<input name="company_name" required></label>
        <label>Parola<input name="password" type="password" minlength="6" required></label>
        <div class="auth-actions">
          <button class="primary" type="submit">Üyelik talebi gönder</button>
          <a class="ghost ink" href="/giris" data-action="show-login">Giriş yap</a>
        </div>
      </form></main>`;
    }
    if (state.screen === "login") {
      return `<main class="wrap"><form class="card login" id="login">
        <p class="mark">Depo Stok</p>
        <h1>Giriş</h1>
        ${flash}
        <label>E-posta<input name="username" type="email" autocomplete="username" required></label>
        <label>Parola<input name="password" type="password" autocomplete="current-password" required></label>
        <div class="auth-actions">
          <button class="primary" type="submit">Giriş yap</button>
          <a class="ghost ink" href="/uye-ol" data-action="show-signup">Üye ol</a>
        </div>
      </form></main>`;
    }
    return `<main class="welcome">
      <section class="welcome-copy">
        <p class="mark">Depo-Stok</p>
        <h1>Stok takibi tek panelde.</h1>
        <p class="lede">Siparişler listeye düşer. Adet, onaydan sonra değişir.</p>
        <div class="welcome-actions">
          <a class="primary" href="/giris" data-action="show-login">Giriş yap</a>
          <a class="ghost ink" href="/uye-ol" data-action="show-signup">Üye ol</a>
        </div>
      </section>
      <section class="welcome-stage" aria-hidden="true">
        <div class="aisle">
          <span>A Blok</span>
          <i style="--fill: 78%"></i>
          <i style="--fill: 46%"></i>
          <i style="--fill: 90%"></i>
          <b>101</b>
        </div>
        <div class="aisle hot">
          <span>B Blok</span>
          <i style="--fill: 22%"></i>
          <i style="--fill: 14%"></i>
          <i style="--fill: 38%"></i>
          <b>204</b>
        </div>
        <div class="aisle">
          <span>C Blok</span>
          <i style="--fill: 64%"></i>
          <i style="--fill: 81%"></i>
          <i style="--fill: 55%"></i>
          <b>310</b>
        </div>
      </section>
    </main>`;
  }
  const admin = state.user.role === "admin";
  const pendingMembers = state.members.filter((row) => row.status === "beklemede").length;
  const pendingOrders = state.orders.filter((row) => row.status === "beklemede").length;
  const pendingReturns = state.returns.filter((row) => row.status === "talep edildi" || row.status === "kontrol bekliyor").length;
  const item = (id, label, count) => `<button type="button" data-action="nav" data-view="${id}" class="nav-item ${state.view === id ? "active" : ""}"><span>${label}</span>${count ? `<span class="count">${count}</span>` : ""}</button>`;
  const who = state.user.name || state.user.email || "";
  const unseen = state.messages.filter((row) => !row.seen).length;
  const notes = state.messages.map((row) => `<article class="note"><p>${esc(row.body)}</p><time>${esc((row.created_at || "").replace("T", " ").slice(0, 16))}</time></article>`).join("");
  const notice = state.noticeOpen ? `<aside class="notice-panel">
      <p class="kicker">Bildirimler</p>
      ${notes || `<p class="muted">Yeni mesaj yok.</p>`}
      <p class="kicker">Nasıl kullanılır</p>
      <p class="guide">Blok ekle, raf koy, rafa ürün yaz. Adedi yanındaki + ve − ile değiştir.</p>
    </aside>` : "";
  const bellCount = unseen ? `<span class="count">${unseen}</span>` : "";
  return `<header class="top">
      <div class="brand">
        <span class="brand-mark" aria-hidden="true">D</span>
        <span class="brand-copy"><strong>Depo Stok</strong><small>${admin ? "Yönetim" : "Depo paneli"}</small></span>
      </div>
      <nav class="nav top-nav" aria-label="Sayfalar">
        ${admin ? `${item("kullanicilar", "Kullanıcılar", pendingMembers)}
        ${item("mesajlar", "Mesajlar", unansweredMessages())}` : `${item("stok", "Stok", 0)}
        ${item("siparis", "Siparişler", pendingOrders)}
        ${item("iade", "İadeler", pendingReturns)}`}
      </nav>
      <div class="session">
        <span class="who">${esc(who)}</span>
        ${admin ? "" : `<button type="button" class="bell" data-action="toggle-notice" aria-label="Bildirimler"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 3a5 5 0 0 0-5 5v2.1c0 .7-.3 1.4-.8 1.9L5 13.2V15h14v-1.8l-1.2-1.2a2.7 2.7 0 0 1-.8-1.9V8a5 5 0 0 0-5-5zm0 18a2.5 2.5 0 0 0 2.4-2h-4.8A2.5 2.5 0 0 0 12 21z"/></svg>${bellCount}</button>`}
        <button type="button" class="ghost logout" data-action="logout">Çıkış</button>
      </div>
    </header>
    ${notice}
    <main class="wrap">${state.flash ? `<p class="flash ${esc(state.flash.tone)}">${esc(state.flash.text)}</p>` : ""}${content}</main>
    <nav class="bottom-nav" aria-label="Sayfalar">
      ${admin ? `${item("kullanicilar", "Üyeler", pendingMembers)}
      ${item("mesajlar", "Mesaj", unansweredMessages())}` : `${item("stok", "Stok", 0)}
      ${item("siparis", "Sipariş", pendingOrders)}
      ${item("iade", "İade", pendingReturns)}`}
    </nav>`;
}

function stockTableHtml() {
  const rows = stockRows().map((row) => `<tr>
      <td data-label="SKU">${esc(row.sku)}</td>
      <td data-label="Ürün">${esc(row.product_name)} <span class="muted">${esc(row.brand)}</span></td>
      <td data-label="Raf"><span class="pill">${esc(row.block)} · ${esc(row.shelf_code)}</span></td>
      <td data-label="Adet" class="qty">${row.quantity}${row.capacity != null ? ` / ${row.capacity}` : ""}</td>
    </tr>`).join("");
  return `<table>
      <thead><tr><th>SKU</th><th>Ürün</th><th>Raf</th><th>Adet</th></tr></thead>
      <tbody>${rows || `<tr><td colspan="4">Kayıt yok</td></tr>`}</tbody>
    </table>`;
}

function shortWhen(value) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  return date.toLocaleString("tr-TR", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" });
}

function summaryColumn(kind, title, total, rows, empty) {
  const shown = rows.slice(0, 8);
  const extra = rows.length - shown.length;
  const items = shown.map((row) => `<li>
      <div><strong>${esc(row.product_name)}</strong><small>${esc(row.location_label || "")}</small></div>
      <div class="move-qty"><b>${row.quantity}</b><small>${esc(row.reason || "")}</small><time>${esc(shortWhen(row.created_at))}</time></div>
    </li>`).join("");
  return `<article class="summary-card ${kind}">
      <header><div><p class="kicker">Son 30 gün</p><h2>${title}</h2></div><strong class="total">${total}</strong></header>
      <ul class="move-list">${items || `<li class="empty">${empty}</li>`}${extra ? `<li class="muted">+${extra} hareket daha</li>` : ""}</ul>
    </article>`;
}

function viewStock() {
  const codes = [...new Set([...(state.blocks || []), ...state.locations.map((item) => item.block)])].sort();
  const shelves = state.locations.filter((item) => item.block === state.openBlock);
  const shelf = shelves.find((item) => item.id === state.openShelf);
  const lines = state.stock.filter((row) => row.location_id === state.openShelf);
  const preview = state.preview;
  const initial = (name) => esc(String(name || "?").trim().charAt(0).toLocaleUpperCase("tr"));
  return `<header class="page-head">
      <div>
        <p class="kicker">Depo yerleşimi</p>
        <h1>Stok</h1>
        ${codes.length ? `<p class="hint">Bloğu seçin, rafı açın. Var olan ürünün adedini yanındaki düğmelerle değiştirebilir veya yeni ürün ekleyebilirsiniz.</p>` : `<p class="hint">Blok ekle, raf koy, rafa ürün yaz.</p>`}
      </div>
      <form class="toolbar" id="block-form">
        <label>Yeni blok<input name="code" placeholder="Örn. D" required></label>
        <button class="primary" type="submit">Blok ekle</button>
      </form>
    </header>
    <section class="stock-summary" aria-label="Son 30 gün">
      ${summaryColumn("in", "Girişler", (state.summary || {}).in_total || 0, (state.summary || {}).entries || [], "Son 30 günde giriş yok.")}
      ${summaryColumn("out", "Çıkışlar", (state.summary || {}).out_total || 0, (state.summary || {}).exits || [], "Son 30 günde çıkış yok.")}
    </section>
    <div class="block-board">
      ${codes.map((code) => {
        const shelvesInBlock = state.locations.filter((item) => item.block === code);
        const fills = shelvesInBlock.slice(0, 3).map(shelfMeter);
        return `<button type="button" class="block-tile ${state.openBlock === code ? "active" : ""}" data-action="open-block" data-block="${esc(code)}">
          <span class="eyebrow">Blok</span>
          ${meter(fills)}
          <span class="letter">${esc(code)}</span>
          <span class="meta">${shelvesInBlock.length} raf</span>
        </button>`;
      }).join("") || `<p class="empty">Henüz blok yok. Yukarıdan bir blok ekleyin.</p>`}
    </div>
    ${state.openBlock ? `<section class="workspace">
      <div class="section-head">
        <div>
          <p class="kicker">Seçili blok</p>
          <h2>${esc(state.openBlock)}</h2>
        </div>
        <button class="danger quiet" type="button" data-action="delete-block" data-block="${esc(state.openBlock)}">Bloğu sil</button>
      </div>
      <form class="toolbar" id="shelf-form">
        <input type="hidden" name="block" value="${esc(state.openBlock)}">
        <label>Yeni raf<input name="shelf_code" placeholder="101" required></label>
        <button class="primary" type="submit">Raf ekle</button>
      </form>
      <div class="shelf-board">
        ${shelves.map((item) => {
          const productCount = state.stock.filter((row) => row.location_id === item.id).length;
          const level = shelfMeter(item);
          return `<button type="button" class="shelf-tile ${state.openShelf === item.id ? "active" : ""}" data-action="open-shelf" data-id="${esc(item.id)}">
            <span class="eyebrow">Raf</span>
            ${meter([level])}
            <span class="code">${esc(item.shelf_code)}</span>
            <span class="meta">${productCount} ürün</span>
          </button>`;
        }).join("") || `<p class="empty">Bu blokta raf yok.</p>`}
      </div>
      ${shelf ? `<div class="shelf-panel">
        <div class="section-head">
          <div>
            <p class="kicker">${esc(shelf.block)} blok</p>
            <h2>${esc(shelf.shelf_code)} numaralı raf</h2>
          </div>
          <button class="danger quiet" type="button" data-action="delete-shelf" data-id="${esc(shelf.id)}">Rafı sil</button>
        </div>
        <div class="product-list">
          ${lines.length ? lines.map((row) => `<div class="stock-line">
            <div class="product-copy">
              <span class="swatch">${initial(row.product_name)}</span>
              <span><strong>${esc(row.product_name)}</strong><small>${esc(row.sku || "")}</small></span>
            </div>
            <span class="stepper">
              <button type="button" data-action="bump" data-id="${esc(row.stock_id)}" data-delta="-1" aria-label="Azalt">−</button>
              <span class="qty">${row.quantity}</span>
              <button type="button" data-action="bump" data-id="${esc(row.stock_id)}" data-delta="1" aria-label="Arttır">+</button>
            </span>
          </div>`).join("") : `<p class="empty">Bu rafta ürün yok.</p>`}
        </div>
        <form class="add-panel" id="add-form">
          <div>
            <p class="kicker">Yeni ürün</p>
            <h2>Bu rafa ekle</h2>
          </div>
          <label>Ürün adı<input name="product_name" placeholder="Ürün adını yazın" required></label>
          <div class="place-meta">
            <span>Blok <strong>${esc(shelf.block)}</strong></span>
            <span>Raf <strong>${esc(shelf.shelf_code)}</strong></span>
          </div>
          <input type="hidden" name="block" value="${esc(shelf.block)}">
          <input type="hidden" name="shelf_code" value="${esc(shelf.shelf_code)}">
          <label>Adet<input name="quantity" type="number" min="1" step="1" value="1" required></label>
          <button class="primary" type="button" data-action="preview-stock">Özeti göster</button>
          ${preview && preview.block === shelf.block && preview.shelf_code === shelf.shelf_code ? `<div class="preview-box">
            <p class="quote">${esc(preview.summary)}</p>
            <p class="muted">Ürün: ${esc(preview.product_name)} · Blok: ${esc(preview.block)} · Raf: ${esc(preview.shelf_code)} · Mevcut ${preview.current_quantity}, sonra ${preview.resulting_quantity}</p>
            ${preview.ok ? `<button class="primary" type="button" data-action="confirm-stock">Onayla ve ekle</button>` : ""}
          </div>` : ""}
        </form>
      </div>` : ""}
    </section>` : ""}`;
}

function orderGroups(rows) {
  const groups = [];
  const index = new Map();
  rows.forEach((row) => {
    const key = `${row.source}|${row.order_id}`;
    if (!index.has(key)) {
      const group = { key, source: row.source, order_id: row.order_id, lines: [] };
      index.set(key, group);
      groups.push(group);
    }
    index.get(key).lines.push(row);
  });
  return groups;
}

function lineNeedsChoice(row) {
  return !!row.needs_manual_match;
}

function outOfStock(row) {
  return row.status === "beklemede" && !row.needs_manual_match && !!row.matched_product && !row.location_label;
}

function viewOrders() {
  const visible = state.orders.filter((row) => !state.orderStatus || row.status === state.orderStatus);
  const cards = orderGroups(visible).map((group) => {
    const pending = group.lines.filter((row) => row.status === "beklemede");
    const openLines = pending.filter(lineNeedsChoice);
    const missing = pending.filter(outOfStock);
    const ready = pending.find((row) => !lineNeedsChoice(row) && !outOfStock(row));
    const status = group.lines.every((row) => row.status === "tamamlandı")
      ? "tamamlandı"
      : group.lines.every((row) => row.status === "iptal")
        ? "iptal"
        : pending.length ? "beklemede" : group.lines[0].status;
    const lines = group.lines.map((row, index) => {
      const choice = outOfStock(row)
        ? `<p class="flash warn">Stokta yok</p>`
        : lineNeedsChoice(row)
        ? `<p class="line-note">${esc(row.sentence || "Bu ürünü katalogdan seçin.")}</p>
           <div class="actions">${(row.candidates || []).map((item) => `<button type="button" data-action="match" data-id="${esc(row.id)}" data-product="${esc(item.product_id)}">${esc(item.product_name)} %${item.confidence}</button>`).join("")}</div>
           <label>Katalogdan seç<select data-catalog="${esc(row.id)}">${state.products.map((item) => `<option value="${esc(item.id)}">${esc(item.sku)} — ${esc(item.product_name)}</option>`).join("")}</select></label>
           <button type="button" data-action="match-select" data-id="${esc(row.id)}">Eşleştirmeyi kaydet</button>`
        : `<p class="line-note">${esc(row.sentence || row.raw_product_text)}</p>`;
      return `<li class="order-line">
        <div class="line-top">
          <span class="line-no">${String(index + 1).padStart(2, "0")}</span>
          <div>
            <strong>${esc(row.raw_product_text)}</strong>
            <p class="muted">${esc(row.match_method_label || "")}</p>
          </div>
          <span class="qty-pill">${row.quantity} adet</span>
        </div>
        ${choice}
      </li>`;
    }).join("");
    const foot = missing.length
      ? `<p>Stokta yok.</p>`
      : openLines.length && group.lines.length > 1
      ? `<p>Bu siparişte ${openLines.length} ürün seçilmedi. Hepsi seçilmeden onaylanmaz.</p>`
      : ready
        ? `<p>${group.lines.length > 1 ? "Ürünlerin hepsi seçildi." : "Onaylayınca stoktan düşer."}</p><button class="primary" type="button" data-action="approve-order" data-id="${esc(ready.id)}">Onayla ve stoktan düş</button>`
        : `<p>${status === "tamamlandı" ? "Stok düşüldü." : ""}</p>`;
    return `<article class="order-card">
      <header>
        <div>
          <p class="kicker">${esc(group.source)}</p>
          <h2>${esc(group.order_id)}</h2>
        </div>
        <div class="row">${badge(status)}<span class="badge">${group.lines.length} ürün</span></div>
      </header>
      <ol class="order-lines">${lines}</ol>
      <footer class="order-foot">${foot}</footer>
    </article>`;
  }).join("");
  return `<section class="page-head"><div><h1>Siparişler</h1><p>Satış kanalınızı seçin. Bağlantı bir kez kurulur. Gelen sipariş burada görünür ve onayınızla stoktan düşer.</p></div>
    <select id="order-status">
      <option value="">Tüm durumlar</option>
      ${["beklemede", "tamamlandı", "iptal"].map((item) => `<option ${state.orderStatus === item ? "selected" : ""}>${item}</option>`).join("")}
    </select></section>
    ${channelBoard()}
    ${cards ? `<div class="order-stack">${cards}</div>` : `<p class="card">Henüz sipariş gelmedi.</p>`}`;
}

function channelBoard() {
  const rows = state.channels || [];
  const tiles = rows.map((row) => {
    const mark = row.connected ? "Bağlı" : row.mode === "webhook" ? "Hazır" : row.mode === "oauth" ? "Sırada" : "Bağla";
    return `<button type="button" class="channel-tile ${state.openChannel === row.code ? "active" : ""} ${row.connected ? "connected" : ""}" data-action="open-channel" data-channel="${esc(row.code)}">
      <strong>${esc(row.name)}</strong><span>${mark}</span>
    </button>`;
  }).join("");
  const open = rows.find((row) => row.code === state.openChannel);
  if (!open) return `<div class="channel-grid">${tiles}</div>`;
  return `<div class="channel-grid">${tiles}</div>${channelPanel(open)}`;
}

function channelPanel(open) {
  const writing = state.helpFor === open.code;
  const steps = (open.steps || []).map((item) => `<li>${esc(item)}</li>`).join("");
  const note = `<div class="channel-copy">
      <p class="kicker">Bağlantı</p>
      <h2>${esc(open.name)}</h2>
      <p class="lead">${esc(open.lead || open.hint)}</p>
      ${steps ? `<ol class="steps">${steps}</ol>` : ""}
      <button type="button" class="ghost ink" data-action="open-help" data-channel="${esc(open.code)}">Bize mesaj gönder</button>
      ${writing ? `<label class="msg-box">Mesaj<textarea id="help-${esc(open.code)}" maxlength="500" placeholder="Kısaca yazın"></textarea></label>
      <button class="primary" type="button" data-action="send-help" data-channel="${esc(open.code)}">Gönder</button>` : ""}
    </div>`;
  if (open.mode === "webhook") {
    const intake = state.user?.intake_key ? `${location.origin}/api/webhooks/in/${state.user.intake_key}` : "";
    return `<section class="channel-panel">${note}
      <div class="channel-bind">
        <p class="kicker">Sipariş geliş adresi</p>
        <p class="hint">Bu adres yalnızca sizin deponuza aittir. Siteniz yeni siparişi buraya gönderir.</p>
        <p class="link-box">${esc(intake)}</p>
        <button type="button" class="ghost ink" data-action="copy-intake" data-url="${esc(intake)}">Adresi kopyala</button>
      </div>
    </section>`;
  }
  const fields = (open.fields || []).map((field) => `<label>${esc(field.label)}<input name="${esc(field.name)}" ${field.secret ? 'type="password"' : ""} autocomplete="off" required></label>`).join("");
  return `<section class="channel-panel">${note}
    <form id="channel-form" class="channel-bind">
      <p class="kicker">Bağlama bilgileri</p>
      ${open.last_error ? `<p class="flash bad">${esc(open.last_error)}</p>` : ""}
      ${open.live ? "" : `<p class="hint">Bilgiler saklanır. Siparişlerin otomatik düşmesi bu kanal için henüz açık değil.</p>`}
      <input type="hidden" name="code" value="${esc(open.code)}">
      ${fields}
      <div class="actions">
        <button class="primary" type="submit">${open.live ? "Bağla ve siparişleri al" : "Bağlantıyı kaydet"}</button>
        ${open.connected ? `<button class="ghost" type="button" data-action="disconnect-channel" data-channel="${esc(open.code)}">Bağlantıyı kaldır</button>` : ""}
      </div>
    </form>
  </section>`;
}

function viewReturns() {
  const cards = state.returns.map((row) => {
    let actions = "";
    if (row.status === "talep edildi") {
      actions = `<div class="actions">
        <button class="primary" type="button" data-action="approve-return" data-id="${esc(row.id)}">İadeyi onayla</button>
        <button class="danger" type="button" data-action="reject-return" data-id="${esc(row.id)}">Reddet</button>
      </div>`;
    } else if (row.status === "kontrol bekliyor") {
      const picker = state.unsuitable && state.unsuitable.id === row.id
        ? `<p>${esc(state.unsuitable.message)}</p>
           <select id="alt-${esc(row.id)}">${(state.unsuitable.locations || []).map((item) => `<option value="${esc(item.id)}">${esc(item.label)}</option>`).join("")}</select>
           <button class="primary" type="button" data-action="restock-alt" data-id="${esc(row.id)}">Seçilen rafa ekle</button>`
        : `<button class="primary" type="button" data-action="restock" data-id="${esc(row.id)}">Sağlam, stoğa ekle</button>`;
      actions = `<p class="muted">Orijinal raf: ${esc(row.original_location_label || "yok")}</p><div class="actions">${picker}</div>`;
    }
    return `<article class="card">
      <div class="row"><strong>${esc(row.order_id)}</strong> ${badge(row.status)}</div>
      <p>${esc(row.product_name)} · ${row.quantity} adet · ${esc(row.return_reason || "neden yok")}</p>
      ${row.restock_note ? `<p class="muted">Geri yazılan raf: ${esc(row.restock_note)}</p>` : ""}
      ${actions}
    </article>`;
  }).join("");
  return `<h1>İadeler</h1>
    <form class="card" id="return-form">
      <h2>İade talebi</h2>
      <label>Sipariş no<input name="order_id" value="${esc(state.returnForm.order_id)}" required></label>
      <label>Neden<select name="return_reason">
        ${["defolu", "yanlış ürün", "fikir değişikliği"].map((item) => `<option ${state.returnForm.return_reason === item ? "selected" : ""}>${esc(item)}</option>`).join("")}
      </select></label>
      <button class="primary" type="submit">Talep oluştur</button>
    </form>
    ${cards || `<p class="card">İade yok</p>`}`;
}

function viewAdd() {
  const preview = state.preview;
  return `<h1>Manuel stok girişi</h1>
    <form class="card" id="add-form">
      <label>Ürün<select name="product_id" required>
        <option value="">Seçin</option>
        ${state.products.map((item) => `<option value="${esc(item.id)}" ${state.form.product_id === item.id ? "selected" : ""}>${esc(item.sku)} — ${esc(item.product_name)}</option>`).join("")}
      </select></label>
      <label>Raf<select name="location_id" required>
        <option value="">Seçin</option>
        ${state.locations.map((item) => `<option value="${esc(item.id)}" ${state.form.location_id === item.id ? "selected" : ""}>${esc(item.label)}</option>`).join("")}
      </select></label>
      <label>Adet<input name="quantity" type="number" min="1" step="1" inputmode="numeric" value="${esc(state.form.quantity)}" required></label>
      <button class="primary" type="button" data-action="preview-stock">Özeti göster</button>
      ${preview ? `<div class="card">
        <p class="quote">${esc(preview.summary)}</p>
        <p class="muted">Mevcut ${preview.current_quantity}, sonra ${preview.resulting_quantity}</p>
        ${preview.ok ? `<button class="primary" type="button" data-action="confirm-stock">Onayla ve ekle</button>` : ""}
      </div>` : ""}
    </form>`;
}

function viewAdmin() {
  const pending = state.members.filter((row) => row.status === "beklemede").length;
  const approved = state.members.filter((row) => row.status === "onaylandı").length;
  const rejected = state.members.filter((row) => row.status === "reddedildi").length;
  const cards = state.members.map((row) => `<article class="member-card">
      <div class="section-head">
        <div>
          <p class="kicker">${esc(row.company_name)}</p>
          <h2>${esc(row.first_name)} ${esc(row.last_name)}</h2>
        </div>
        ${badge(row.status)}
      </div>
      <p class="muted">${esc(row.email)}</p>
      <div class="place-meta">
        <span>${row.block_count || 0} blok</span>
        <span>${row.shelf_count || 0} raf</span>
        <span>${row.stock_count || 0} ürün</span>
      </div>
      ${row.status === "beklemede" ? `<div class="actions">
        <button class="primary" type="button" data-action="approve-member" data-id="${esc(row.id)}">Onayla</button>
        <button class="danger" type="button" data-action="reject-member" data-id="${esc(row.id)}">Reddet</button>
      </div>` : row.status === "onaylandı" ? `<div class="actions stack">
        ${row.mail_sent
          ? `<button class="sent" type="button" disabled>Onay maili gönderildi</button>`
          : `<button class="primary" type="button" data-action="approve-member" data-id="${esc(row.id)}">Onay mailini gönder</button>`}
        <button class="ghost ink" type="button" data-action="open-message" data-id="${esc(row.id)}">Kullanıcıya mesaj gönder</button>
        ${state.composeFor === row.id ? `<label class="msg-box">Mesaj<textarea id="msg-${esc(row.id)}" maxlength="500" placeholder="Kısa bir duyuru yazın"></textarea></label>
        <button class="primary" type="button" data-action="send-message" data-id="${esc(row.id)}">Gönder</button>` : ""}
      </div>` : ""}
      <div class="actions">
        ${state.confirmDelete === row.id
          ? `<button class="danger" type="button" data-action="delete-member" data-id="${esc(row.id)}">Emin misin? Sil</button>`
          : `<button class="danger quiet" type="button" data-action="ask-delete" data-id="${esc(row.id)}">Kullanıcıyı sil</button>`}
      </div>
    </article>`).join("");
  return `<header class="page-head">
      <div>
        <p class="kicker">Yönetim</p>
        <h1>Kullanıcılar</h1>
        <p class="hint">Onay bekleyenler burada. Onaylayınca n8n maili gider.</p>
      </div>
    </header>
    <div class="stat-row">
      <article class="stat"><span>Bekleyen</span><strong>${pending}</strong></article>
      <article class="stat"><span>Onaylı</span><strong>${approved}</strong></article>
      <article class="stat"><span>Reddedilen</span><strong>${rejected}</strong></article>
    </div>
    <div class="member-grid">${cards || `<p class="empty">Henüz üye yok.</p>`}</div>`;
}

function viewInbox() {
  const cards = (state.support || []).map((row) => `<article class="card">
      <div class="row"><strong>${esc(row.sender_name)}</strong> <span class="badge info">${esc(row.channel || "Genel")}</span></div>
      <p class="muted">${esc(row.sender_email)} · ${esc(row.company_name)}</p>
      <p>${esc(row.body)}</p>
      <time class="muted">${esc((row.created_at || "").replace("T", " ").slice(0, 16))}</time>
      ${row.reply ? `<p class="reply"><span class="kicker">Cevabınız</span>${esc(row.reply)}</p>` : ""}
      <div class="actions">
        <button type="button" class="ghost ink" data-action="open-reply" data-id="${esc(row.id)}">Cevap yaz</button>
        ${state.replyFor === row.id ? `<label class="msg-box">Cevap<textarea id="reply-${esc(row.id)}" maxlength="500" placeholder="Üyeye iletilecek cevap"></textarea></label>
        <button class="primary" type="button" data-action="send-reply" data-id="${esc(row.id)}">Gönder</button>` : ""}
      </div>
    </article>`).join("");
  return `<header class="page-head">
      <div>
        <p class="kicker">Yönetim</p>
        <h1>Mesajlar</h1>
        <p class="hint">Üyeler satış yeri kartından buraya yazar.</p>
      </div>
    </header>
    ${cards || `<p class="card">Henüz mesaj yok.</p>`}`;
}

function viewMembers() {
  const cards = state.members.map((row) => `<article class="card">
      <div class="row"><strong>${esc(row.first_name)} ${esc(row.last_name)}</strong> ${badge(row.status)}</div>
      <p>${esc(row.email)} · ${esc(row.company_name)}</p>
      ${row.status === "beklemede" ? `<div class="actions">
        <button class="primary" type="button" data-action="approve-member" data-id="${esc(row.id)}">Onayla</button>
        <button class="danger" type="button" data-action="reject-member" data-id="${esc(row.id)}">Reddet</button>
      </div>` : ""}
    </article>`).join("");
  return `<h1>Üye onayları</h1>
    <p class="hint">${state.mailConfigured ? "Onay, üyenin e-postasına n8n üzerinden gider." : "Onay maili şu an gönderilmiyor. N8N_APPROVAL_WEBHOOK tanımlı değil."}</p>
    ${cards || `<p class="card">Üye yok</p>`}`;
}

function paint() {
  const admin = state.user?.role === "admin";
  const content = admin ? (state.view === "mesajlar" ? viewInbox() : viewAdmin())
    : state.view === "siparis" ? viewOrders()
    : state.view === "iade" ? viewReturns()
    : viewStock();
  document.body.classList.toggle("guest", !state.user);
  document.getElementById("app").innerHTML = shell(state.user ? content : "");
}

async function loadAll() {
  const calls = [api("/api/stock"), api("/api/orders"), api("/api/returns"), api("/api/options"), api("/api/blocks")];
  if (state.user?.role === "admin") calls.push(api("/api/members"), api("/api/support"));
  else calls.push(api("/api/messages"), api("/api/channels"));
  const [stock, orders, returns, options, blocks, extra, channels] = await Promise.all(calls);
  state.stock = stock.data?.rows || [];
  state.summary = stock.data?.summary || { in_total: 0, out_total: 0, entries: [], exits: [] };
  state.orders = orders.data?.rows || [];
  state.returns = returns.data?.rows || [];
  state.products = options.data?.products || [];
  state.locations = options.data?.locations || [];
  state.blocks = blocks.data?.blocks || [];
  if (state.user?.role === "admin") {
    state.members = extra?.data?.rows || [];
    state.support = channels?.data?.rows || [];
  } else {
    state.messages = extra?.data?.rows || [];
    state.channels = channels?.data?.rows || [];
  }
}

async function run(fn) {
  if (state.busy) return;
  state.busy = true;
  document.body.classList.add("busy");
  try { await fn(); } finally {
    state.busy = false;
    document.body.classList.remove("busy");
    paint();
  }
}

function readForm(form) {
  const data = {};
  new FormData(form).forEach((value, key) => { data[key] = value; });
  return data;
}

document.body.addEventListener("click", (event) => {
  const button = event.target.closest("[data-action]");
  if (!button) return;
  const action = button.dataset.action;
  const id = button.dataset.id;
  if (action === "show-signup" || action === "show-login") {
    event.preventDefault();
    const path = action === "show-signup" ? "/uye-ol" : "/giris";
    if (location.pathname !== path) history.pushState({}, "", path);
    state.screen = action === "show-signup" ? "signup" : "login";
    state.flash = null;
    paint();
  } else if (action === "approve-member" || action === "reject-member") {
    run(async () => {
      const path = action === "approve-member" ? "approve" : "reject";
      const result = await api(`/api/members/${id}/${path}`, { method: "POST", body: {} });
      state.flash = { tone: result.ok ? "ok" : "bad", text: result.message };
      await loadAll();
    });
  } else if (action === "copy-intake") {
    const text = button.dataset.url || "";
    navigator.clipboard.writeText(text).then(() => {
      state.flash = { tone: "ok", text: "Sipariş geliş adresi kopyalandı" };
      paint();
    }).catch(() => {
      state.flash = { tone: "bad", text: "Adres kopyalanamadı" };
      paint();
    });
  } else if (action === "open-help") {
    state.helpFor = state.helpFor === button.dataset.channel ? "" : button.dataset.channel;
    paint();
  } else if (action === "send-help") {
    const text = document.getElementById(`help-${button.dataset.channel}`)?.value || "";
    run(async () => {
      const result = await api("/api/support", { method: "POST", body: { channel: button.dataset.channel, body: text } });
      state.flash = { tone: result.ok ? "ok" : "bad", text: result.message };
      if (result.ok) state.helpFor = "";
    });
  } else if (action === "open-reply") {
    state.replyFor = state.replyFor === id ? "" : id;
    paint();
  } else if (action === "send-reply") {
    const text = document.getElementById(`reply-${id}`)?.value || "";
    run(async () => {
      const result = await api(`/api/support/${id}/reply`, { method: "POST", body: { body: text } });
      state.flash = { tone: result.ok ? "ok" : "bad", text: result.message };
      if (result.ok) state.replyFor = "";
      await loadAll();
    });
  } else if (action === "ask-delete") {
    state.confirmDelete = state.confirmDelete === id ? "" : id;
    paint();
  } else if (action === "delete-member") {
    run(async () => {
      const result = await api(`/api/members/${id}`, { method: "DELETE" });
      state.flash = { tone: result.ok ? "ok" : "bad", text: result.message };
      if (result.ok) state.confirmDelete = "";
      await loadAll();
    });
  } else if (action === "open-channel") {
    state.openChannel = state.openChannel === button.dataset.channel ? "" : button.dataset.channel;
    paint();
  } else if (action === "disconnect-channel") {
    run(async () => {
      const result = await api(`/api/channels/${button.dataset.channel}`, { method: "DELETE" });
      state.flash = { tone: result.ok ? "ok" : "bad", text: result.message };
      await loadAll();
    });
  } else if (action === "nav") {
    state.view = views.includes(button.dataset.view) ? button.dataset.view : "stok";
    state.preview = null;
    state.flash = null;
    location.hash = state.view;
    const linked = state.view === "siparis" && (state.channels || []).some((row) => row.connected && row.live);
    if (!linked) paint();
    else run(async () => {
      const synced = await api("/api/channels/sync", { method: "POST", body: {} });
      if (synced.data?.imported) state.flash = { tone: "ok", text: synced.message };
      else if (!synced.ok && synced.message) state.flash = { tone: "bad", text: synced.message };
      await loadAll();
    });
  } else if (action === "open-message") {
    state.composeFor = state.composeFor === id ? "" : id;
    paint();
  } else if (action === "send-message") {
    const text = document.getElementById(`msg-${id}`)?.value || "";
    run(async () => {
      const result = await api(`/api/members/${id}/message`, { method: "POST", body: { body: text } });
      state.flash = { tone: result.ok ? "ok" : "bad", text: result.message };
      if (result.ok) state.composeFor = "";
    });
  } else if (action === "toggle-notice") {
    state.noticeOpen = !state.noticeOpen;
    if (state.noticeOpen && state.messages.some((row) => !row.seen)) {
      state.messages = state.messages.map((row) => ({ ...row, seen: true }));
      api("/api/messages/seen", { method: "POST", body: {} });
    }
    paint();
  } else if (action === "logout") {
    run(async () => { await api("/api/logout", { method: "POST", body: {} }); state.user = null; });
  } else if (action === "open-block") {
    state.openBlock = state.openBlock === button.dataset.block ? "" : button.dataset.block;
    state.openShelf = "";
    state.preview = null;
    paint();
  } else if (action === "open-shelf") {
    state.openShelf = state.openShelf === button.dataset.id ? "" : button.dataset.id;
    state.preview = null;
    paint();
  } else if (action === "bump") {
    run(async () => {
      const result = await api(`/api/stock/${id}/adjust`, { method: "POST", body: { delta: Number(button.dataset.delta) } });
      state.flash = result.ok ? null : { tone: "bad", text: result.message };
      await loadAll();
    });
  } else if (action === "delete-block" || action === "delete-shelf" || action === "delete-stock") {
    run(async () => {
      const path = action === "delete-block" ? `/api/blocks/${button.dataset.block}` : action === "delete-shelf" ? `/api/locations/${id}` : `/api/stock/${id}`;
      const result = await api(path, { method: "DELETE" });
      state.flash = { tone: result.ok ? "ok" : "bad", text: result.message };
      if (result.ok && action === "delete-block") { state.openBlock = ""; state.openShelf = ""; }
      if (result.ok && action === "delete-shelf") state.openShelf = "";
      state.preview = null;
      await loadAll();
    });
  } else if (action === "preview-stock") {
    const data = readForm(button.closest("form"));
    run(async () => {
      const result = await api("/api/stock/place", { method: "POST", body: { ...data, quantity: Number(data.quantity), confirmed: false } });
      state.preview = { ...(result.data || {}), summary: result.message, ok: result.ok };
      state.flash = result.ok ? null : { tone: "bad", text: result.message };
    });
  } else if (action === "confirm-stock") {
    run(async () => {
      const pending = state.preview || {};
      const result = await api("/api/stock/place", { method: "POST", body: { product_name: pending.product_name, block: pending.block, shelf_code: pending.shelf_code, quantity: pending.quantity, confirmed: true } });
      state.flash = { tone: result.ok ? "ok" : "bad", text: result.message };
      if (result.ok) state.preview = null;
      await loadAll();
    });
  } else if (action === "approve-order") {
    run(async () => {
      const result = await api(`/api/orders/${id}/approve`, { method: "POST", body: {} });
      state.flash = { tone: result.ok ? "ok" : "warn", text: result.message };
      await loadAll();
    });
  } else if (action === "match" || action === "match-select") {
    const product = action === "match" ? button.dataset.product : document.querySelector(`[data-catalog="${id}"]`)?.value;
    run(async () => {
      const result = await api(`/api/orders/${id}/match`, { method: "POST", body: { product_id: product } });
      state.flash = { tone: result.ok ? "ok" : "bad", text: result.message };
      await loadAll();
    });
  } else if (action === "approve-return" || action === "reject-return" || action === "restock" || action === "restock-alt") {
    run(async () => {
      let result;
      if (action === "approve-return") result = await api(`/api/returns/${id}/approve`, { method: "POST", body: {} });
      else if (action === "reject-return") result = await api(`/api/returns/${id}/reject`, { method: "POST", body: {} });
      else if (action === "restock-alt") {
        const locationId = document.getElementById(`alt-${id}`)?.value;
        result = await api(`/api/returns/${id}/restock`, { method: "POST", body: { location_id: locationId } });
      } else result = await api(`/api/returns/${id}/restock`, { method: "POST", body: {} });
      state.flash = { tone: result.ok ? "ok" : "warn", text: result.message };
      state.unsuitable = result.code === "location_unsuitable" ? { id, message: result.message, locations: result.data?.locations || state.locations } : null;
      await loadAll();
    });
  }
});

document.body.addEventListener("input", (event) => {
  if (event.target.id === "q") {
    state.q = event.target.value;
    const box = document.getElementById("stock-rows");
    paintStockBody();
  }
});

function paintStockBody() {
  const box = document.getElementById("stock-rows");
  if (box) box.innerHTML = stockTableHtml();
}

document.body.addEventListener("change", (event) => {
  const target = event.target;
  if (target.id === "block") { state.block = target.value; paintStockBody(); }
  if (target.id === "order-status") { state.orderStatus = target.value; paint(); }
  if (target.name === "product_id" || target.name === "location_id" || target.name === "quantity") {
    state.form[target.name] = target.value;
    state.preview = null;
  }
  if (target.name === "order_id" || target.name === "return_reason") state.returnForm[target.name] = target.value;
  if (target.form?.id === "shelf-form" && target.name) state.shelfForm[target.name] = target.value;
  if (target.form?.id === "product-form" && target.name) state.productForm[target.name] = target.value;
});

document.body.addEventListener("submit", (event) => {
  const form = event.target;
  if (form.id === "login" || form.id === "admin-login") {
    event.preventDefault();
    const data = readForm(form);
    const path = form.id === "admin-login" ? "/api/admin/login" : "/api/login";
    run(async () => {
      const result = await api(path, { method: "POST", body: data });
      if (!result.ok) { state.flash = { tone: "bad", text: result.message }; return; }
      state.user = result.data.user;
      state.flash = null;
      state.view = "stok";
      await loadAll();
    });
  }
  if (form.id === "signup") {
    event.preventDefault();
    run(async () => {
      const result = await api("/api/register", { method: "POST", body: readForm(form) });
      state.flash = { tone: result.ok ? "ok" : "bad", text: result.message };
      if (result.ok) {
        if (location.pathname !== "/giris") history.pushState({}, "", "/giris");
        state.screen = "login";
      }
    });
  }
  if (form.id === "block-form") {
    event.preventDefault();
    run(async () => {
      const result = await api("/api/blocks", { method: "POST", body: readForm(form) });
      state.flash = { tone: result.ok ? "ok" : "bad", text: result.message };
      if (result.ok) state.openBlock = String(readForm(form).code || "").toUpperCase();
      await loadAll();
    });
  }
  if (form.id === "shelf-form") {
    event.preventDefault();
    run(async () => {
      const result = await api("/api/locations", { method: "POST", body: readForm(form) });
      state.flash = { tone: result.ok ? "ok" : "bad", text: result.message };
      if (result.ok) state.openShelf = result.data?.location?.id || state.openShelf;
      await loadAll();
    });
  }
  if (form.id === "channel-form") {
    event.preventDefault();
    const data = readForm(form);
    const code = data.code;
    delete data.code;
    run(async () => {
      const result = await api(`/api/channels/${code}`, { method: "POST", body: data });
      state.flash = { tone: result.ok ? "ok" : "bad", text: result.message };
      if (result.ok && result.code === "connected") {
        const synced = await api("/api/channels/sync", { method: "POST", body: {} });
        if (synced.data?.imported) state.flash = { tone: "ok", text: synced.message };
        else if (!synced.ok && synced.message) state.flash = { tone: "bad", text: synced.message };
      }
      await loadAll();
    });
  }
  if (form.id === "return-form") {
    event.preventDefault();
    run(async () => {
      const result = await api("/api/returns", { method: "POST", body: state.returnForm });
      state.flash = { tone: result.ok ? "ok" : "bad", text: result.message };
      if (result.ok) state.returnForm.order_id = "";
      await loadAll();
    });
  }
});

function screenFromPath() {
  if (location.pathname === "/yonetim") return "admin";
  if (location.pathname === "/uye-ol") return "signup";
  if (location.pathname === "/giris") return "login";
  return "welcome";
}

async function boot() {
  const meta = await api("/api/meta");
  state.mailConfigured = Boolean(meta.data?.mail_configured);
  state.screen = screenFromPath();
  const me = await api("/api/me");
  if (me.ok) {
    state.user = me.data.user;
    const hash = location.hash.replace("#", "");
    state.view = views.includes(hash) ? hash : "stok";
    if (state.user.role === "admin" && state.view !== "mesajlar") state.view = "kullanicilar";
    await loadAll();
  }
  paint();
}

window.addEventListener("popstate", () => {
  if (state.user) return;
  state.screen = screenFromPath();
  state.flash = null;
  paint();
});

boot();
