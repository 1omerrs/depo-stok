from __future__ import annotations

from html import escape


def render_page(*, title: str, message: str, tone: str, detail: str = "", action: str | None = None, action_label: str = "") -> str:
    button = ""
    if action and action_label:
        button = f'<form method="post" action="{escape(action, quote=True)}"><button type="submit">{escape(action_label)}</button></form>'
    extra = f'<p class="detail">{escape(detail)}</p>' if detail else ""
    return f"""<!DOCTYPE html>
<html lang="tr">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="referrer" content="no-referrer">
  <title>{escape(title)}</title>
  <link rel="stylesheet" href="/static/styles.css">
</head>
<body class="solo">
  <main class="sheet">
    <p class="mark">Depo Stok</p>
    <h1>{escape(title)}</h1>
    <p class="tone tone-{escape(tone)}">{escape(message)}</p>
    {extra}
    {button}
  </main>
</body>
</html>"""
