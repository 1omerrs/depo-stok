from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Result:
    ok: bool
    code: str
    message: str
    status_code: int = 200
    data: dict | None = None

    def json(self) -> dict:
        return {
            "ok": self.ok,
            "code": self.code,
            "message": self.message,
            "data": self.data or {},
        }


class Leave(Exception):
    """İş kuralı yazmadan çıkış. Açık transaction geri alınır."""

    def __init__(self, result: Result):
        super().__init__(result.message)
        self.result = result
