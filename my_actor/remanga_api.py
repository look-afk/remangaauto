"""Тонкий клиент API remanga.org для фармов.

Куки (JSON / Netscape) -> токен, ретраи на 429/5xx, доменные методы:
балансы, локации, рейды, дневные задания, главы и отметка «прочитано».
"""

from __future__ import annotations

import json
import os
import random
import time
import urllib.parse
from pathlib import Path

import requests

from .log import log as _log

API = "https://api.remanga.org"
SITE = "https://remanga.org"
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)


# ------------------------------------------------------------------ куки

def _norm_cookie(c: dict) -> dict | None:
    name = c.get("name") or c.get("key")
    if not name:
        return None
    value = c.get("value")
    if value is None:
        value = c.get("content") or ""
    out = {
        "name": name,
        "value": str(value),
        "domain": c.get("domain") or "remanga.org",
        "path": c.get("path") or "/",
    }
    expires = c.get("expires") if c.get("expires") is not None else c.get("expirationDate")
    try:
        if expires and float(expires) > 0:
            out["expires"] = float(expires)
    except (TypeError, ValueError):
        pass
    return out


def parse_netscape(raw: str) -> list[dict]:
    items = []
    for line in raw.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) < 7:
            continue
        domain, _flag, path, _secure, expiry, name, value = parts[:7]
        items.append({
            "name": name,
            "value": value,
            "domain": domain.lstrip("."),
            "path": path,
            "expires": float(expiry) if expiry.isdigit() else 0,
        })
    return items


def parse_cookies_text(raw: str) -> list[dict]:
    """Разбирает текст кук: JSON (storage_state / список) либо Netscape cookies.txt."""
    raw = (raw or "").strip()
    if not raw:
        raise ValueError("пустой текст кук")

    if raw[0] in "[{":
        data = json.loads(raw)
        if isinstance(data, dict) and "cookies" in data:
            items = data["cookies"]
        elif isinstance(data, list):
            items = data
        else:
            raise ValueError("в JSON кук не найден список cookies")
    else:
        items = parse_netscape(raw)

    cookies = [n for n in (_norm_cookie(c) for c in items) if n and n["name"]]
    if not cookies:
        raise ValueError("не удалось разобрать куки")
    return cookies


def load_cookies(path) -> list[dict]:
    """Читает cookies.json (storage_state) либо cookies.txt (Netscape)."""
    path = Path(path)
    try:
        return parse_cookies_text(path.read_text(encoding="utf-8-sig"))
    except ValueError as exc:
        raise ValueError(f"Не удалось разобрать куки из {path}: {exc}") from exc


def find_cookies_file(explicit=None) -> Path | None:
    """Ищем файл кук: аргумент -> REMANGA_COOKIES_JSON -> REMANGA_COOKIES_FILE -> локальные имена."""
    candidates = []
    if explicit:
        candidates.append(Path(explicit))
    for env in ("REMANGA_COOKIES_JSON", "REMANGA_COOKIES_FILE"):
        v = os.getenv(env)
        if v:
            candidates.append(Path(v))

    here = Path(__file__).resolve().parent.parent
    for name in ("cookies.json", "cookies.txt", "storage_state.json"):
        candidates.append(here / name)
        candidates.append(Path.cwd() / name)

    for c in candidates:
        try:
            if c.is_file() and c.stat().st_size > 0:
                return c
        except OSError:
            continue
    return None


def pick_token(cookies: list[dict]) -> str | None:
    for key in ("auth:token", "auth:server-token", "token", "access_token"):
        for c in cookies:
            if c.get("name") == key and c.get("value"):
                return urllib.parse.unquote(c["value"])
    return None


# ------------------------------------------------------------------ клиент

class RemangaApi:
    """Сессия API с ретраями. Все методы возвращают (code, data)."""

    def __init__(self, cookies: list[dict], log=_log, retries: int = 3):
        self.log = log
        self.retries = max(1, int(retries))
        self.s = requests.Session()
        self.s.headers.update({
            "User-Agent": UA,
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
            "Referer": f"{SITE}/",
            "Origin": SITE,
        })
        for c in cookies:
            domain = c.get("domain") or ""
            if "remanga" not in domain:
                continue
            try:
                self.s.cookies.set(
                    c["name"], c["value"],
                    domain=c.get("domain"), path=c.get("path") or "/",
                )
            except Exception:
                pass
        self.token = pick_token(cookies)
        if self.token:
            self.s.headers["Authorization"] = f"Bearer {self.token}"
        self.token_is_set = bool(self.token)

    # -- низкоуровневое
    @staticmethod
    def _url(path: str) -> str:
        return path if path.startswith("http") else f"{API}{path}"

    @staticmethod
    def _safe_json(r):
        try:
            return r.json()
        except Exception:
            return {"_text": (r.text or "")[:300]}

    def request(self, method: str, path: str, body: dict | None = None, **params):
        url = self._url(path)
        last = (0, None)
        for attempt in range(self.retries):
            try:
                r = self.s.request(
                    method.upper(), url,
                    json=body, params=params or None, timeout=30,
                )
            except requests.RequestException as exc:
                last = (0, {"_error": str(exc)})
                time.sleep(1.0 + attempt + random.random())
                continue

            if r.status_code in (429,) or 500 <= r.status_code < 600:
                last = (r.status_code, self._safe_json(r))
                time.sleep(1.5 * (attempt + 1) + random.random())
                continue

            if r.status_code == 401:
                return 401, None
            return r.status_code, self._safe_json(r)
        return last

    def get(self, path: str, **params):
        return self.request("GET", path, None, **params)

    def post(self, path: str, body: dict | None = None):
        return self.request("POST", path, body)

    # -- пользователь
    def current_user(self) -> dict | None:
        code, data = self.get("/api/users/current/")
        if code != 200 or not data:
            return None
        user = data.get("content") if isinstance(data.get("content"), dict) else data
        if not isinstance(user, dict) or not user.get("id"):
            return None
        return user

    # -- балансы события (серебро = event points)
    def event_points(self) -> int:
        code, data = self.get("/api/v2/events/eventpoint-balance/")
        return int(data.get("balance") or 0) if code == 200 and isinstance(data, dict) else 0

    def lightning_balance(self) -> int:
        code, data = self.get("/api/v2/billing/lightning-balance/")
        if code != 200 or not isinstance(data, dict):
            return 0
        return int(data.get("balance_free") or 0) + int(data.get("balance_paid") or 0)

    def profile(self) -> dict:
        code, data = self.get("/api/v2/events/card-battle/profile/")
        return data if code == 200 and isinstance(data, dict) else {}

    def squad(self) -> dict:
        code, data = self.get("/api/v2/events/card-battle/squad/")
        return data if code == 200 and isinstance(data, dict) else {}

    # -- локации и рейды
    def locations(self) -> list[dict]:
        code, data = self.get("/api/v2/events/card-battle/locations/")
        if code != 200:
            return []
        if isinstance(data, list):
            return data
        return data.get("results") or []

    def raid(self, location_id: int) -> tuple[int, dict]:
        """Один проход локации: тратит энергию, даёт серебро."""
        return self.post(
            f"/api/v2/events/card-battle/locations/{int(location_id)}/raid/",
            {},
        )

    # -- дневные задания
    def daily(self) -> list[dict]:
        code, data = self.get("/api/v2/events/card-battle/daily/")
        if code != 200:
            return []
        if isinstance(data, list):
            return data
        return data.get("results") or []

    def claim_daily(self, task_id: int) -> tuple[int, dict]:
        return self.post(f"/api/v2/events/card-battle/daily/{int(task_id)}/claim/")

    def buy_event_points(self, amount) -> tuple[int, dict]:
        """Обмен молний на серебро. amount — сколько молний тратим."""
        return self.post(
            "/api/v2/events/card-battle/event-points/buy/",
            {"amount": int(amount)},
        )

    # -- тайтлы и главы
    def title_info(self, slug: str) -> dict | None:
        code, data = self.get(f"/api/titles/{slug.strip('/')}/")
        if code != 200 or not data:
            return None
        info = data.get("content") if isinstance(data.get("content"), dict) else data
        return info if isinstance(info, dict) else None

    def chapters_page(self, branch_id: int, page: int = 1, count: int = 100) -> list[dict]:
        code, data = self.get(
            "/api/titles/chapters/",
            branch_id=branch_id, count=count, page=page,
            ordering="index", user_data=1,
        )
        if code != 200 or not data:
            return []
        items = data.get("content")
        if items is None:
            items = data.get("results") or []
        return [c for c in items if isinstance(c, dict)]

    def all_chapters(self, branch_id: int) -> list[dict]:
        out, page = [], 1
        while page <= 400:
            batch = self.chapters_page(branch_id, page)
            if not batch:
                break
            out.extend(batch)
            if len(batch) < 100:
                break
            page += 1
        return out

    def branch_page_of(self, chapter: dict, count: int = 100) -> int:
        """Страница списка глав, на которой лежит глава (ordering=index)."""
        try:
            idx = int(chapter.get("index") or 0)
        except (TypeError, ValueError):
            idx = 0
        return max(1, idx // max(1, count) + 1)

    # -- отметка «прочитано»
    @staticmethod
    def view_candidates(chapter_id: int, title_id) -> list[tuple[str, dict]]:
        return [
            ("/api/activity/views/", {"chapter": chapter_id}),
            ("/api/v2/titles/chapters/", {"chapter_ids": [chapter_id]}),
            (f"/api/v2/titles/{title_id}/my-progress/",
             {"chapter_id": chapter_id, "progress": 100, "device": "desktop"}),
            ("/api/v2/titles/chapters/", {"chapter": chapter_id}),
            ("/api/titles/chapters/", {"chapter": chapter_id}),
        ]

    def mark_viewed(self, chapter_id: int, title_id, verify=None) -> bool:
        """Перебираем известные варианты «отметить прочитанным»."""
        for path, body in self.view_candidates(chapter_id, title_id):
            try:
                code, _ = self.post(path, body)
            except Exception:
                continue
            if code not in (200, 201, 204, 409):
                continue
            if verify is None:
                return True
            time.sleep(0.4)
            if verify() is True:
                return True
        return False

    def comment_chapter(self, chapter_id: int, text: str) -> tuple[bool, str]:
        code, data = self.post(
            "/api/activity/comments/",
            {"chapter": int(chapter_id), "text": text},
        )
        if code in (200, 201):
            return True, "ok"
        detail = ""
        if isinstance(data, dict):
            detail = str(data.get("detail") or data.get("msg") or data)[:200]
        return False, f"HTTP {code} {detail}".strip()


def api_from_env(explicit=None, log=_log) -> RemangaApi | None:
    """Собирает клиент из кук окружения/проекта. None, если кук нет.

    Источники по приоритету: REMANGA_COOKIES_RAW (содержимое файла),
    файлы из REMANGA_COOKIES_JSON / REMANGA_COOKIES_FILE,
    cookies.json / cookies.txt рядом с пакетом или в cwd.
    """
    raw = os.getenv("REMANGA_COOKIES_RAW")
    if raw and raw.strip():
        try:
            return RemangaApi(parse_cookies_text(raw), log=log)
        except Exception as exc:
            log(f"[api] не удалось разобрать REMANGA_COOKIES_RAW: {exc}")

    path = find_cookies_file(explicit)
    if not path:
        return None
    try:
        return RemangaApi(load_cookies(path), log=log)
    except Exception as exc:
        log(f"[api] не удалось загрузить куки {path}: {exc}")
        return None
