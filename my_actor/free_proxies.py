"""Бесплатные прокси стран СНГ: подбор и быстрая проверка.

Источник — открытый ежедневный список proxifly (GitHub). Список
фильтруется по кодам стран, несколько кандидатов проверяются
параллельно запросом к remanga.org, возвращается первый живой
прокси в формате scheme://ip:port.

Проверка живости: ответ 2xx/3xx без страницы DDoS-Guard —
прокси реально открывает сайт; 403/404/5xx, DDoS-Guard-
challenge, отказ соединения или таймаут — прокси отбракован.
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

# remanga.org должен открываться (2xx/3xx); 403 от DDoS-Guard не считается живым.
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


def is_alive(proxy_url: str, timeout: float = 10.0) -> bool:
    """True, если remanga.org реально открывается через прокси (2xx/3xx, без DDoS-Guard)."""
    proxy_url = (proxy_url or "").strip()
    if not proxy_url:
        return False
    if "://" not in proxy_url:
        proxy_url = "http://" + proxy_url
    try:
        response = requests.get(
            CHECK_URL,
            proxies={"http": proxy_url, "https": proxy_url},
            timeout=timeout,
            headers={"User-Agent": _UA},
        )
    except requests.exceptions.RequestException:
        return False
    if response.status_code >= 400:
        return False
    head = response.content[:4000].lower()
    return b"ddos-guard" not in head and b"ddos_guard" not in head


# Совместимость со старым именем.
_is_alive = is_alive


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
                if not alive:
                    continue
                proxy_url = futures[future]
                # Перепроверка: бесплатные прокси умирают за секунды,
                # подтверждаем кандидата сразу после нахождения.
                if not _is_alive(proxy_url, timeout=8.0):
                    log(f"[proxy] Кандидат отвалился при перепроверке: {proxy_url}")
                    continue
                # Отменяем ещё не начатые проверки — ответ уже найден.
                pool.shutdown(wait=False, cancel_futures=True)
                log(f"[proxy] ✅ Рабочий прокси: {proxy_url}")
                return proxy_url
    except Exception as exc:
        log(f"[proxy] Ошибка при проверке прокси: {exc}")
        return None

    log("[proxy] Живых прокси не нашлось — пойдём напрямую.")
    return None


def resolve_proxy(explicit: str | None = None) -> str | None:
    """Выбирает прокси: сначала проверяет прокси из env, потом бесплатный СНГ.

    - прокси из env (explicit / CUSTOM_PROXY / PROXY_URL) жив -> используем его;
    - мёртвый -> убираем его из env и подбираем бесплатный (USE_FREE_PROXY);
    - бесплатный не нужен или не найден -> None (прямое подключение).

    Никогда не бросает исключений — при сбоях возвращает None или env-прокси.
    """
    try:
        env_proxy = (
            (explicit or "").strip()
            or (os.getenv("CUSTOM_PROXY") or "").strip()
            or (os.getenv("PROXY_URL") or "").strip()
        )

        if env_proxy:
            log(f"[proxy] Прокси из env: проверяю ({env_proxy})...")
            if is_alive(env_proxy):
                log("[proxy] Прокси из env работает — используем его.")
                if "://" not in env_proxy:
                    env_proxy = "http://" + env_proxy
                return env_proxy
            log("[proxy] Прокси из env НЕ работает — убираю его.")
            # Чистим env, чтобы requests (remanga_api) не шёл через мёртвый прокси.
            os.environ.pop("PROXY_URL", None)
            os.environ.pop("CUSTOM_PROXY", None)

        if os.getenv("USE_FREE_PROXY", "1") == "1":
            free = find_working_proxy()
            if free:
                # PROXY_URL имеет приоритет в proxy_from_env — requests пойдёт
                # через этот прокси, а не через (удалённый) env.
                os.environ["PROXY_URL"] = free
                return free

        log("[proxy] Рабочего прокси нет — прямое подключение.")
        return None

    except Exception as exc:
        log(f"[proxy] Ошибка выбора прокси: {exc}")
        return None
