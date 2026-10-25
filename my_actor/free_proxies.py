"""Free proxy discovery and validation for ReManga.

A proxy is not considered usable merely because requests can reach the
homepage: Chromium must also be able to open the actual Murim Cards page
AND reach the API host (api.remanga.org), because all farming goes there.
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
API_CHECK_URL = "https://api.remanga.org/api/users/current/"
_ALLOWED_SCHEMES = {"http", "socks4", "socks5"}
_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"


def _parse_countries(countries: str | None) -> set[str]:
    raw = (countries or "").strip() or DEFAULT_COUNTRIES
    return {c.strip().upper() for c in raw.split(",") if c.strip()}


PROXYSCRAPE_URL = (
    "https://cdn.jsdelivr.net/gh/proxyscrape/free-proxy-list@main/"
    "proxies/countries/{cc}/{proto}/data.txt"
)
GPROXY_URL = (
    "https://raw.githubusercontent.com/gproxynet/free-proxy-list/"
    "main/proxies.json"
)
DEFAULT_SOURCES = "proxifly,proxyscrape,gproxynet"
FAST_MS = 5000  # быстрее этого считаем прокси «быстрым» и проверяем первым


def _norm_scheme(value) -> str | None:
    scheme = str(value or "http").strip().lower()
    if scheme == "https":
        scheme = "http"
    return scheme if scheme in _ALLOWED_SCHEMES else None


def _make_url(scheme, hostport) -> str | None:
    """Собирает scheme://host:port из разных форматов списков."""
    text = str(hostport or "").strip()
    if not text:
        return None
    text = text.split()[0]
    if "://" in text:
        scheme, text = text.split("://", 1)
    scheme = _norm_scheme(scheme)
    text = text.rstrip("/")
    host, _, port = text.rpartition(":")
    if not scheme or not host or not port.isdigit():
        return None
    return f"{scheme}://{host}:{port}"


def _src_proxifly(codes: set[str]) -> list[tuple[str, int | None]]:
    response = requests.get(LIST_URL, timeout=20, headers={"User-Agent": _UA})
    response.raise_for_status()
    data = response.json()
    out: list[tuple[str, int | None]] = []
    for item in data if isinstance(data, list) else []:
        geo = item.get("geolocation") or {}
        if geo.get("country") not in codes:
            continue
        url = _make_url(item.get("protocol"), f"{item.get('ip')}:{item.get('port')}")
        if url:
            out.append((url, None))
    return out


def _src_proxyscrape(codes: set[str]) -> list[tuple[str, int | None]]:
    jobs = [(cc.lower(), proto) for cc in sorted(codes) for proto in ("http", "socks5")]

    def load(job):
        cc, proto = job
        try:
            r = requests.get(
                PROXYSCRAPE_URL.format(cc=cc, proto=proto),
                timeout=10, headers={"User-Agent": _UA},
            )
            if r.status_code != 200:
                return []
            return [u for u in (_make_url(proto, ln) for ln in r.text.splitlines()) if u]
        except Exception:
            return []

    out: list[tuple[str, int | None]] = []
    with ThreadPoolExecutor(max_workers=8) as pool:
        for urls in pool.map(load, jobs):
            out.extend((u, None) for u in urls)
    return out


def _src_gproxynet(codes: set[str]) -> list[tuple[str, int | None]]:
    response = requests.get(GPROXY_URL, timeout=20, headers={"User-Agent": _UA})
    response.raise_for_status()
    data = response.json()
    items = data if isinstance(data, list) else (data.get("proxies") or [])
    out: list[tuple[str, int | None]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        if str(item.get("country") or "").upper() not in codes:
            continue
        url = _make_url(item.get("protocol"), item.get("proxy"))
        if not url:
            continue
        lat = item.get("latency_ms")
        out.append((url, int(lat) if isinstance(lat, (int, float)) else None))
    return out


_SOURCES = {
    "proxifly": _src_proxifly,
    "proxyscrape": _src_proxyscrape,
    "gproxynet": _src_gproxynet,
}


def fetch_candidates(countries: str | None = None, limit: int = 100,
                     exclude: set[str] | None = None) -> list[str]:
    """Собирает кандидатов из нескольких публичных списков.

    Источники: env PROXY_SOURCES (по умолчанию proxifly,proxyscrape,gproxynet).
    Из каждого берём быстрые первыми (если известна задержка), остальные
    перемешиваем; затем чередуем источники, чтобы не зависеть от одного.
    exclude - прокси, которые уже отвалились в этом прогоне.
    """
    codes = _parse_countries(countries)
    skip = set(exclude or ())
    names = [n.strip().lower()
             for n in (os.getenv("PROXY_SOURCES") or DEFAULT_SOURCES).split(",")
             if n.strip()]

    per_source: list[list[str]] = []
    for name in names:
        fn = _SOURCES.get(name)
        if fn is None:
            log(f"[proxy] неизвестный источник «{name}» — пропускаю")
            continue
        try:
            items = fn(codes)
        except Exception as exc:
            log(f"[proxy] источник {name} недоступен: {str(exc)[:100]}")
            continue

        seen: set[str] = set()
        fast: list[tuple[str, int]] = []
        rest: list[str] = []
        for url, lat in items:
            if url in skip or url in seen:
                continue
            seen.add(url)
            if lat is not None and lat <= FAST_MS:
                fast.append((url, lat))
            else:
                rest.append(url)
        fast.sort(key=lambda x: x[1])
        random.shuffle(rest)
        ordered = [u for u, _ in fast] + rest
        log(f"[proxy] источник {name}: {len(ordered)} кандидатов")
        if ordered:
            per_source.append(ordered)

    # round-robin по источникам, без дублей
    result: list[str] = []
    seen_all: set[str] = set()
    idx = 0
    while len(result) < limit and any(idx < len(lst) for lst in per_source):
        for lst in per_source:
            if idx < len(lst) and lst[idx] not in seen_all:
                seen_all.add(lst[idx])
                result.append(lst[idx])
                if len(result) >= limit:
                    break
        idx += 1
    return result


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
    """Validate with Chromium against the exact page the actor needs,
    plus reachability of the API host through the same proxy."""
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
                if (
                    "ddos-guard" in content
                    or "ddos guard" in content
                    or "checking your browser" in content
                    or len(body.strip()) <= 20
                ):
                    return False

                # Farming goes to api.remanga.org (a separate host); many free
                # proxies open the site but cannot reach the API. Any HTTP
                # answer (even 401) is fine; a connection error is not.
                api_resp = page.context.request.get(
                    API_CHECK_URL,
                    timeout=timeout_ms,
                    fail_on_status_code=False,
                )
                api_body = (api_resp.text() or "")[:3000].lower()
                if (
                    api_resp.status >= 500
                    or "ddos-guard" in api_body
                    or "ddos guard" in api_body
                ):
                    return False
                return True
            finally:
                browser.close()
    except Exception:
        return False


def find_working_proxy(countries: str | None = None,
                       exclude: set[str] | None = None) -> str | None:
    """Find a proxy that opens ReManga (site + API) in Chromium.

    exclude - proxies that already failed in this run.
    """
    if countries is None:
        countries = os.getenv("PROXY_COUNTRIES")

    try:
        candidates = fetch_candidates(countries, limit=120, exclude=exclude)
    except Exception as exc:
        log(f"[proxy] Не удалось скачать список прокси: {exc}")
        return None

    if not candidates:
        log(f"[proxy] В списке нет прокси для стран: {countries or DEFAULT_COUNTRIES}")
        return None

    log(
        f"[proxy] Найдено {len(candidates)} кандидатов "
        f"(страны: {countries or DEFAULT_COUNTRIES}); "
        "проверяю ReManga и API через Chromium..."
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
                log(f"[proxy] ✅ Chromium открыл ReManga и API через {candidate}")
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