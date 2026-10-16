"""Free proxy discovery and validation for ReManga.

A proxy is not considered usable merely because requests can reach the
homepage: Chromium must also be able to open the actual Murim Cards page.
"""
from __future__ import annotations

import os
import random
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urlparse

import requests
from playwright.sync_api import sync_playwright

from .log import log

LIST_URL = (
    "https://raw.githubusercontent.com/proxifly/"
    "free-proxy-list/main/proxies/all/data.json"
)
DEFAULT_COUNTRIES = "RU,KZ,BY,UZ,AM,GE,AZ,MD,UA,TJ,KG"
CHECK_URL = "https://remanga.org/murim-cards#/map"
_ALLOWED_SCHEMES = {"http", "socks4", "socks5"}
_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"


def _parse_countries(countries: str | None) -> set[str]:
    raw = (countries or "").strip() or DEFAULT_COUNTRIES
    return {c.strip().upper() for c in raw.split(",") if c.strip()}


def fetch_candidates(countries: str | None = None, limit: int = 100) -> list[str]:
    """Download a current list and return candidate proxies."""
    codes = _parse_countries(countries)
    response = requests.get(LIST_URL, timeout=20, headers={"User-Agent": _UA})
    response.raise_for_status()
    data = response.json()

    found: list[str] = []
    for item in data if isinstance(data, list) else []:
        geo = item.get("geolocation") or {}
        if geo.get("country") not in codes:
            continue
        scheme = str(item.get("protocol") or "http").lower()
        if scheme == "https":
            scheme = "http"
        if scheme not in _ALLOWED_SCHEMES:
            continue
        ip, port = item.get("ip"), item.get("port")
        if ip and port:
            found.append(f"{scheme}://{ip}:{port}")

    random.shuffle(found)
    return found[:limit]


def is_alive(proxy_url: str, timeout: float = 10.0) -> bool:
    """Quick network check; browser validation is performed separately."""
    proxy_url = (proxy_url or "").strip()
    if not proxy_url:
        return False
    if "://" not in proxy_url:
        proxy_url = "http://" + proxy_url
    try:
        response = requests.get(
            "https://remanga.org/",
            proxies={"http": proxy_url, "https": proxy_url},
            timeout=timeout,
            headers={"User-Agent": _UA},
        )
    except requests.exceptions.RequestException:
        return False
    if response.status_code >= 400:
        return False
    body = response.content[:6000].lower()
    return b"ddos-guard" not in body and b"ddos_guard" not in body


_is_alive = is_alive


def _browser_alive(proxy_url: str, timeout_ms: int = 12000) -> bool:
    """Validate with Chromium against the exact page the actor needs."""
    parsed = urlparse(proxy_url)
    proxy = {"server": f"{parsed.scheme}://{parsed.hostname}:{parsed.port}"}
    if parsed.username:
        proxy["username"] = parsed.username
    if parsed.password:
        proxy["password"] = parsed.password

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True,
                proxy=proxy,
                args=["--disable-blink-features=AutomationControlled"],
            )
            try:
                page = browser.new_page(
                    user_agent=_UA,
                    viewport={"width": 1280, "height": 800},
                )
                response = page.goto(
                    CHECK_URL,
                    wait_until="domcontentloaded",
                    timeout=timeout_ms,
                )
                if response is None or response.status >= 400:
                    return False
                page.wait_for_timeout(1200)
                title = (page.title() or "").lower()
                body = (page.locator("body").inner_text(timeout=3000) or "").lower()
                content = f"{title}\n{body}"
                return (
                    "ddos-guard" not in content
                    and "ddos guard" not in content
                    and "checking your browser" not in content
                    and len(body.strip()) > 20
                )
            finally:
                browser.close()
    except Exception:
        return False


def find_working_proxy(countries: str | None = None, exclude: set[str] | None = None) -> str | None:
    """Find a proxy that opens ReManga in Chromium, excluding failed routes."""
    if countries is None:
        countries = os.getenv("PROXY_COUNTRIES")

    try:
        candidates = fetch_candidates(countries, limit=100)
    except Exception as exc:
        log(f"[proxy] Не удалось скачать список прокси: {exc}")
        return None

    excluded = {str(value).strip().rstrip("/") for value in (exclude or set()) if value}
    candidates = [candidate for candidate in candidates if candidate.rstrip("/") not in excluded]
    if not candidates:
        log(f"[proxy] Нет новых прокси для стран: {countries or DEFAULT_COUNTRIES}")
        return None

    log(
        f"[proxy] Найдено {len(candidates)} кандидатов "
        f"(страны: {countries or DEFAULT_COUNTRIES}); "
        "проверяю ReManga через Chromium..."
    )

    # A small pool avoids launching dozens of Chromium processes at once.
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = {pool.submit(_browser_alive, candidate): candidate
                   for candidate in candidates}
        for future in as_completed(futures):
            candidate = futures[future]
            try:
                alive = future.result()
            except Exception:
                alive = False
            if alive:
                log(f"[proxy] ✅ Chromium открыл ReManga через {candidate}")
                for pending in futures:
                    pending.cancel()
                return candidate

    log("[proxy] Ни один проверенный прокси не открыл ReManga в Chromium.")
    return None


def resolve_proxy(explicit: str | None = None) -> str | None:
    """Prefer an explicitly configured proxy, otherwise discover a free one."""
    try:
        env_proxy = (
            (explicit or "").strip()
            or (os.getenv("CUSTOM_PROXY") or "").strip()
            or (os.getenv("PROXY_URL") or "").strip()
        )

        if env_proxy:
            log(f"[proxy] Прокси из env: проверяю ({env_proxy})...")
            if is_alive(env_proxy):
                if "://" not in env_proxy:
                    env_proxy = "http://" + env_proxy
                log("[proxy] Прокси из env отвечает на сеточную проверку.")
                return env_proxy
            log("[proxy] Прокси из env не прошёл сеточную проверку.")
            os.environ.pop("PROXY_URL", None)
            os.environ.pop("CUSTOM_PROXY", None)

        if os.getenv("USE_FREE_PROXY", "1") == "1":
            free = find_working_proxy()
            if free:
                os.environ["PROXY_URL"] = free
                return free

        log("[proxy] Подходящий прокси не найден.")
        return None
    except Exception as exc:
        log(f"[proxy] Ошибка выбора прокси: {exc}")
        return None
