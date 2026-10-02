"""Движок чтения глав: быстрый скролл до конца + отметка «прочитано».

Портировано из рабочего бота (remanga_bot.py): цель скролла — маркер
`data-reader-block="chapter-end"`, который IntersectionObserver сайта
объявляет «глава дочитана» и после чего уходит POST /api/activity/views/.
Плюс проверка/догонка флага через API и перезапуск браузера при вылете.
"""

from __future__ import annotations

import os
import random
import time

from .log import log as _log

SITE = "https://remanga.org"

DEFAULT_SCROLL = {
    "step_px": 2500,
    "delay_ms": 16,
    "settle_ms": 4200,
    "ready_timeout_s": 20,
    "max_steps": 6000,
    "max_scroll_s": 120,
}


def scroll_cfg_from_env() -> dict:
    cfg = dict(DEFAULT_SCROLL)

    def grab(name, key, cast):
        v = os.getenv(name)
        if v not in (None, ""):
            try:
                cfg[key] = cast(v)
            except (TypeError, ValueError):
                pass

    grab("READER_STEP_PX", "step_px", int)
    grab("READER_DELAY_MS", "delay_ms", int)
    grab("READER_SETTLE_MS", "settle_ms", int)
    grab("READER_READY_TIMEOUT_S", "ready_timeout_s", int)
    grab("READER_MAX_SCROLL_S", "max_scroll_s", int)
    return cfg


# ---------------------------------------------------------------- состояние

def view_is_read(value) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value > 0
    if isinstance(value, str):
        return value.lower() not in ("", "false", "none", "null", "0")
    if isinstance(value, dict):
        for k in ("is_read", "read", "viewed"):
            if k in value:
                return bool(value[k])
        return bool(value)
    return bool(value)


def wait_reader_ready(page, timeout_s: int = 20) -> bool:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            ready = page.evaluate("""() => {
                const imgs = document.querySelectorAll('img[src*="reimg"], img[src*="/images/"]');
                if (imgs.length > 0) return true;
                const rc = document.querySelector('.reader-container-width');
                return !!(rc && rc.getBoundingClientRect().height > 200);
            }""")
            if ready:
                return True
        except Exception:
            pass
        page.wait_for_timeout(200)
    return False


def scroll_state(page, chapter_id=None) -> dict:
    """Позиция/высота/низ главы в читалке (низ — по маркеру chapter-end)."""
    return page.evaluate("""(cid) => {
        const y = window.scrollY || document.documentElement.scrollTop || 0;
        const vh = window.innerHeight || document.documentElement.clientHeight || 0;
        const h = Math.max(document.body.scrollHeight, document.documentElement.scrollHeight);
        let end = null, src = null;
        const sel = '[data-reader-block="chapter-end"][data-chapter-id="' + cid + '"]';
        const marker = document.querySelector(sel);
        if (marker) {
            end = marker.getBoundingClientRect().top + y;
            src = 'marker';
        }
        if (end === null) {
            const mine = Array.from(document.querySelectorAll('[data-chapter-id]'))
                .filter(e => e.dataset.chapterId === String(cid));
            if (mine.length) {
                end = 0;
                for (const e of mine) {
                    const b = e.getBoundingClientRect().bottom + y;
                    if (b > end) end = b;
                }
                src = 'block';
            }
        }
        if (end === null) { end = h; src = 'doc'; }
        return {y: y, vh: vh, h: h, end: end, src: src,
                inView: end >= y && end <= y + vh,
                paged: h <= vh * 1.5 + 40};
    }""", chapter_id)


def reader_type(page) -> str:
    """'slider' (вертикальный скролл) или 'pager' (листание по страницам)."""
    try:
        return page.evaluate("""() => {
            try {
                const row = document.cookie.split('; ')
                    .find(c => c.indexOf('reader:settings=') === 0);
                if (!row) return 'slider';
                const raw = row.slice('reader:settings='.length);
                const j = JSON.parse(decodeURIComponent(raw));
                return (j.manga && j.manga.readerType) || 'slider';
            } catch (e) { return 'slider'; }
        }""")
    except Exception:
        return 'slider'


def _wheel(page, dy: int) -> None:
    try:
        page.mouse.wheel(0, int(dy))
    except Exception:
        page.evaluate(f"window.scrollBy(0, {int(dy)})")


def fast_scroll_to_end(page, chapter_id, cfg: dict, from_top: bool = True) -> dict:
    """Реальный скролл до конца главы (маркер должен попасть в вьюпорт)."""
    step = max(200, int(cfg.get("step_px", 2500)))
    delay = max(0, int(cfg.get("delay_ms", 16)))
    max_steps = int(cfg.get("max_steps", 6000))
    budget = float(cfg.get("max_scroll_s", 120))

    if from_top:
        page.evaluate("window.scrollTo(0, 0)")
        page.wait_for_timeout(80)

    st = scroll_state(page, chapter_id)
    if reader_type(page) == "pager":
        return _read_paged(page, chapter_id, cfg, st)

    deadline = time.time() + budget
    steps = 0
    stuck = 0
    prev_y = -1.0

    while steps < max_steps and time.time() < deadline:
        if st["inView"]:
            break
        steps += 1
        remaining = st["end"] - (st["y"] + st["vh"])
        if remaining > step * 2:
            dy = step
        else:
            # не даём перепрыгнуть маркер: y < end
            dy = max(120, min(step // 3, int(remaining) + 60))
        _wheel(page, dy)
        if delay:
            page.wait_for_timeout(delay)
        st = scroll_state(page, chapter_id)
        if st["y"] <= prev_y + 0.5:
            stuck += 1
            if stuck > 60:
                break
        else:
            stuck = 0
        prev_y = st["y"]

    if not st["inView"]:
        target = max(0.0, st["end"] - st["vh"] / 2.0)
        delta = int(target - st["y"])
        if abs(delta) >= 5:
            page.evaluate(f"window.scrollBy(0, {delta})")
            page.wait_for_timeout(150)
            st = scroll_state(page, chapter_id)

    if st["inView"]:
        # лёгкие качки настоящим колесом — событие scroll для слушателей
        _wheel(page, 40)
        page.wait_for_timeout(50)
        _wheel(page, -40)
        page.wait_for_timeout(80)
        st = scroll_state(page, chapter_id)
        if not st["inView"]:
            target = max(0.0, st["end"] - st["vh"] / 2.0)
            page.evaluate(f"window.scrollBy(0, {int(target - st['y'])})")
            page.wait_for_timeout(120)
            st = scroll_state(page, chapter_id)

    return {"at_end": bool(st["inView"]), "mode": "scroll", "steps": steps,
            "y": st["y"], "end": st["end"], "h": st["h"], "src": st["src"]}


def _read_paged(page, chapter_id, cfg: dict, st: dict) -> dict:
    """Режим pager: листаем кликом по правой половине экрана."""
    delay = max(120, int(cfg.get("delay_ms", 16)) * 8)
    cap = min(max(100, int(cfg.get("max_steps", 6000))), 300)
    vw = int((page.viewport_size or {}).get("width", 1440))
    vh = int(st.get("vh") or 900)
    start = page.url.split("?")[0]
    clicks = 0
    advanced = False
    while clicks < cap:
        try:
            page.mouse.click(int(vw * 0.75), int(vh * 0.5))
        except Exception:
            break
        clicks += 1
        page.wait_for_timeout(delay)
        if page.url.split("?")[0] != start:
            advanced = True
            break
        cur = scroll_state(page, chapter_id)
        if cur["h"] > cur["vh"] * 1.5 + 40:
            break
    st = scroll_state(page, chapter_id)
    return {"at_end": bool(advanced or st["inView"]), "mode": "pager",
            "steps": clicks, "y": st["y"], "end": st["end"],
            "h": st["h"], "src": st["src"]}


def read_chapter(page, url: str, chapter_id, cfg: dict | None = None) -> dict:
    """Открывает главу, доскролливает до конца, ждёт отрисовку view."""
    cfg = cfg or scroll_cfg_from_env()
    ready_timeout = int(cfg.get("ready_timeout_s", 20))
    page.goto(url, wait_until="domcontentloaded", timeout=ready_timeout * 1000 + 15000)
    ready = wait_reader_ready(page, ready_timeout)
    page.wait_for_timeout(300)

    res = fast_scroll_to_end(page, chapter_id, cfg)
    res["ready"] = ready

    settle = int(cfg.get("settle_ms", 4200))
    page.wait_for_timeout(settle)

    # после паузы контент мог дорасти — доводим маркер обратно в окно
    st = scroll_state(page, chapter_id)
    if not st["inView"]:
        res2 = fast_scroll_to_end(page, chapter_id, cfg, from_top=False)
        res.update({k: res2[k] for k in ("at_end", "mode", "steps")})
        page.wait_for_timeout(settle)

    if res.get("mode") == "scroll":
        try:
            res["progress"] = page.evaluate(
                "() => new URL(location.href).searchParams.get('progress')")
        except Exception:
            res["progress"] = None
    else:
        res["progress"] = None
    return res


# ---------------------------------------------------------------- отметка прочтения

def mark_viewed(api, chapter: dict, title_id, branch_id, cfg: dict | None = None,
                log=_log) -> str:
    """Проверяем, проставился ли viewed; если нет — догоняем через API.

    Возвращает 'site' / 'api' / 'failed' / 'unverified'.
    """
    cfg = cfg or {}
    if not cfg.get("verify_mark", True):
        return "unchecked"

    chapter_id = int(chapter.get("id"))
    page_no = api.branch_page_of(chapter) if hasattr(api, "branch_page_of") else 1

    def viewed_now():
        items = api.chapters_page(branch_id, page_no)
        for it in items:
            if it.get("id") == chapter_id and "viewed" in it:
                v = it.get("viewed")
                return None if v is None else view_is_read(v)
        return None

    status = viewed_now()
    if status is True:
        return "site"
    api.mark_viewed(chapter_id, title_id, verify=viewed_now)
    if viewed_now() is True:
        return "api"
    return "failed" if status is False else "unverified"


# ---------------------------------------------------------------- браузер

def looks_dead(exc: Exception) -> bool:
    text = str(exc).lower()
    return any(s in text for s in (
        "target closed", "target page, context or browser has been closed",
        "browser has been closed", "target crashed", "connection closed",
        "protocol error", "disconnected", "crashed",
    ))


class BrowserSession:
    """Владелец браузера: умеет перезапускаться, если Chromium вылетел."""

    def __init__(self, playwright, launch_kwargs: dict, context_kwargs: dict,
                 cookies: list[dict], log=_log):
        self.p = playwright
        self.launch_kwargs = launch_kwargs or {}
        self.context_kwargs = context_kwargs or {}
        self.cookies = cookies or []
        self.log = log
        self.browser = None
        self.context = None
        self._page = None

    @classmethod
    def from_existing(cls, playwright, browser, context, page,
                      launch_kwargs=None, context_kwargs=None, cookies=None, log=_log):
        s = cls(playwright, launch_kwargs, context_kwargs, cookies or [], log=log)
        s.browser, s.context, s._page = browser, context, page
        return s

    def _launch(self):
        browser = self.p.chromium.launch(**self.launch_kwargs)
        context = browser.new_context(**self.context_kwargs)
        if self.cookies:
            try:
                context.add_cookies(self.cookies)
            except Exception as exc:
                self.log(f"[reader] не удалось применить куки: {exc}")
        page = context.new_page()
        try:
            from playwright_stealth import Stealth
            Stealth().apply_stealth_sync(page)
        except Exception:
            pass
        return browser, context, page

    def start(self):
        if self._page is None or self._page.is_closed():
            self.restart()
        return self._page

    @property
    def page(self):
        return self.start()

    def is_dead(self) -> bool:
        try:
            if self._page is None or self._page.is_closed():
                return True
            if self.context is None or getattr(self.context, "pages", None) is None:
                return True
            if self.browser is None or not self.browser.is_connected():
                return True
            self._page.evaluate("1")
            return False
        except Exception:
            return True

    def restart(self):
        self.log("[reader] перезапуск браузера…")
        for obj in (self._page, self.context, self.browser):
            try:
                if obj is not None:
                    obj.close()
            except Exception:
                pass
        self._page = None
        self.context = None
        self.browser = None
        for attempt in range(3):
            try:
                self.browser, self.context, self._page = self._launch()
                self.log("[reader] браузер запущен")
                return self._page
            except Exception as exc:
                self.log(f"[reader] запуск браузера не удался ({attempt + 1}/3): {exc}")
                time.sleep(2 + attempt * 2)
        raise RuntimeError("не удалось запустить браузер")

    def get(self, restart_on_dead: bool = True):
        """Страница; при вылете — перезапуск."""
        if self.is_dead():
            if not restart_on_dead:
                raise RuntimeError("браузер закрыт")
            return self.restart()
        return self._page

    def close(self):
        for obj in (self._page, self.context, self.browser):
            try:
                if obj is not None:
                    obj.close()
            except Exception:
                pass
