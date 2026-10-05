"""Бесплатные прокси стран СНГ: подбор и быстрая проверка.

Источник — открытый ежедневный список proxifly (GitHub). Список
фильтруется по кодам стран, несколько кандидатов проверяются
параллельно запросом к remanga.org, возвращается первый живой
прокси в формате scheme://ip:port.

Проверка живости: любой HTTP-ответ (200/403/404) означает, что
прокси пропускает трафик до сайта; отказ соединения/таймаут —
прокси мёртв.
"""

from __future__ import annotations

import os
import random
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests

from .log import log

LIST_URL = (
    "https://raw.githubusercontent.com/proxifly/"
    "free-proxy-list/main/proxies/all/data.json"
)

# По умолчанию — все страны СНГ.
DEFAULT_COUNTRIES = "RU,KZ,BY,UZ,AM,GE,AZ,MD,UA,TJ,KG"

# Любая HTTP-статистика от remanga.org доказывает, что прокси доходит до сайта.
CHECK_URL = "https://remanga.org/"

# Схемы, которые понимают и requests (с requests[socks]), и Playwright.
_ALLOWED_SCHEMES = {"http", "socks4", "socks5"}

_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"


def _parse_countries(countries: str | None) -> set[str]:
    raw = (countries or "").strip() or DEFAULT_COUNTRIES
    return {c.strip().upper() for c in raw.split(",") if c.strip()}


def fetch_candidates(countries: str | None = None, limit: int = 100) -> list[str]:
    """Скачивает список и возвращает до limit прокси нужных стран."""
    codes = _parse_countries(countries)

    response = requests.get(LIST_URL, timeout=15, headers={"User-Agent": _UA})
    response.raise_for_status()
    data = response.json()

    found: list[str] = []
    for item in data if isinstance(data, list) else []:
        geo = item.get("geolocation") or {}
        if geo.get("country") not in codes:
            continue

        scheme = str(item.get("protocol") or "http").lower()
        if scheme == "https":
            # requests/Playwright для адреса прокси используют http://
            scheme = "http"
        if scheme not in _ALLOWED_SCHEMES:
            continue

        ip = item.get("ip")
        port = item.get("port")
        if not ip or not port:
            continue

        found.append(f"{scheme}://{ip}:{port}")

    random.shuffle(found)
    return found[:limit]


def _is_alive(proxy_url: str, timeout: float = 10.0) -> bool:
    try:
        requests.get(
            CHECK_URL,
            proxies={"http": proxy_url, "https": proxy_url},
            timeout=timeout,
            headers={"User-Agent": _UA},
        )
        return True
    except requests.exceptions.RequestException:
        return False


def find_working_proxy(countries: str | None = None) -> str | None:
    """Возвращает первый живой прокси из списка или None.

    countries — коды стран через запятую ("RU,KZ,...");
    из env читается PROXY_COUNTRIES, если аргумент не передан.
    """
    if countries is None:
        countries = os.getenv("PROXY_COUNTRIES")

    try:
        candidates = fetch_candidates(countries)
    except Exception as exc:
        log(f"[proxy] Не удалось скачать список прокси: {exc}")
        return None

    if not candidates:
        log(f"[proxy] В списке нет прокси для стран: {countries or DEFAULT_COUNTRIES}")
        return None

    log(f"[proxy] Проверяю {len(candidates)} бесплатных прокси "
        f"(страны: {countries or DEFAULT_COUNTRIES})...")

    try:
        with ThreadPoolExecutor(max_workers=20) as pool:
            futures = {pool.submit(_is_alive, p): p for p in candidates}
            for future in as_completed(futures):
                try:
                    alive = future.result()
                except Exception:
                    alive = False
                if alive:
                    proxy_url = futures[future]
                    # Отменяем ещё не начатые проверки — ответ уже найден.
                    pool.shutdown(wait=False, cancel_futures=True)
                    log(f"[proxy] ✅ Рабочий прокси: {proxy_url}")
                    return proxy_url
    except Exception as exc:
        log(f"[proxy] Ошибка при проверке прокси: {exc}")
        return None

    log("[proxy] Живых прокси не нашлось — пойдём напрямую.")
    return None
