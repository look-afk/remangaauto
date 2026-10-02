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


def log(msg) -> None:
    if _impl is None:
        print(msg, flush=True)
        return
    try:
        _impl(msg)
    except Exception:
        print(msg, flush=True)
