"""Единый логгер пакета.

По умолчанию пишет в stdout (локальный запуск). В Apify-Actor вход
подменяется на `Actor.log.info` — он умеет цензурировать секреты.
"""

from __future__ import annotations

_impl = None


def set_logger(fn) -> None:
    """Подменить бэкенд логирования (например, на Actor.log.info)."""
    global _impl
    _impl = fn


def _safe_print(msg) -> None:
    """Печать, устойчивая к консолям без UTF-8 (Windows cp1251 и т.п.)."""
    try:
        print(msg, flush=True)
    except UnicodeEncodeError:
        safe = str(msg).encode("ascii", "replace").decode("ascii")
        print(safe, flush=True)


def log(msg) -> None:
    if _impl is None:
        _safe_print(msg)
        return
    try:
        _impl(msg)
    except Exception:
        _safe_print(msg)
