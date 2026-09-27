from __future__ import annotations

import base64
import hashlib
import json
import os


def seal(payload: dict, key: str) -> str:
    raw = json.dumps(payload, ensure_ascii=False).encode()
    nonce = os.urandom(16)
    hidden = bytes(byte ^ mask for byte, mask in zip(raw, _stream(key, nonce, len(raw))))
    return base64.urlsafe_b64encode(nonce + hidden).decode()


def open_sealed(token: str, key: str) -> dict:
    blob = base64.urlsafe_b64decode(token.encode())
    nonce, hidden = blob[:16], blob[16:]
    raw = bytes(byte ^ mask for byte, mask in zip(hidden, _stream(key, nonce, len(hidden))))
    data = json.loads(raw.decode())
    if not isinstance(data, dict):
        raise ValueError("bağlantı bilgisi bozuk")
    return data


def _stream(key: str, nonce: bytes, size: int) -> bytes:
    chunks = []
    counter = 0
    while sum(len(part) for part in chunks) < size:
        chunks.append(hashlib.sha256(f"{key}:{nonce.hex()}:{counter}".encode()).digest())
        counter += 1
    return b"".join(chunks)[:size]
