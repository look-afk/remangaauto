"""Фарм молний: чтение глав с реальным скроллом (движок reader.py).

Что изменилось относительно старой версии:
  * главы и их статус «прочитано» берутся из API (`/api/titles/chapters/?user_data=1`),
    а не вырезаются из DOM ссылками `a[href*="/manga/"]`;
  * скролл идёт до маркера `chapter-end` (тот самый, который сайт считает
    концом главы) — быстрее и надёжнее ручного колеса «пока высота не вылезла»;
  * флаг `viewed` проверяется и догоняется через API, а не «прочитал = ок»;
  * уже прочитанные на сайте главы пропускаются — не тратим энергию лимита впустую;
  * браузер перезапускается сам, если Chromium вылетел;
  * после чтения забираются дневные задания (серебро + молнии).
"""

from __future__ import annotations

import os
import random
import time

from . import reader as rdr
from .log import log as _log
from .remanga_api import SITE, api_from_env
from .silver_farm import claim_dailies

LIGHTNING_STATE_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "lightning_state.json",
)


def _today() -> str:
    return time.strftime("%Y-%m-%d", time.gmtime(time.time() + 3 * 3600))


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, "") or default)
    except ValueError:
        return default


def _env_bool(name: str, default: bool) -> bool:
    v = os.getenv(name)
    if v is None or v == "":
        return default
    return v.strip().lower() not in ("0", "false", "no", "off")


def load_state() -> dict:
    import json

    try:
        with open(LIGHTNING_STATE_FILE, "r", encoding="utf-8") as f:
            state = json.load(f)
    except Exception:
        state = {}

    if state.get("date") != _today():
        state = {"date": _today(), "count": 0, "read": state.get("read", [])}
    state.setdefault("count", 0)
    state.setdefault("read", [])
    return state


def save_state(state: dict) -> None:
    import json

    state["read"] = state.get("read", [])[-2000:]
    try:
        with open(LIGHTNING_STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False)
    except Exception as exc:
        _log(f"[lightning] не удалось сохранить состояние: {exc}")


def slug_from_url(value: str) -> str:
    v = (value or "").strip().rstrip("/")
    if "://" in v:
        from urllib.parse import urlparse

        parts = [p for p in urlparse(v).path.split("/") if p]
        if not parts:
            return ""
        if parts[0] == "manga" and len(parts) > 1:
            return parts[1]
        return parts[-1]
    if v.startswith("manga/"):
        v = v[6:]
    return v.split("/")[0]


# ---------------------------------------------------------------- сбор тайтлов

def collect_collection_titles(page, collection_url: str) -> list[str]:
    """Запасной путь: тайтлы коллекции со страницы сайта (если нужен API — дайте слаги)."""
    page.goto(collection_url, wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(3000)

    titles: list[str] = []
    stale = 0

    for _ in range(30):
        hrefs = page.eval_on_selector_all(
            'a[href*="/manga/"]', "els => els.map(e => e.href)")

        before = len(titles)
        for href in hrefs:
            slug = slug_from_url(href)
            if slug:
                url = f"{SITE}/manga/{slug}"
                if url not in titles:
                    titles.append(url)
        stale = stale + 1 if len(titles) == before else 0
        if stale >= 3:
            break

        page.mouse.wheel(0, 3000)
        page.wait_for_timeout(900)

    _log(f"[lightning] коллекция: {len(titles)} тайтлов")
    return titles


def _titles_from_env() -> list[str]:
    out = []
    for name in ("READ_TITLE_URLS", "READ_TITLE_SLUGS"):
        raw = os.getenv(name) or ""
        for part in raw.split(","):
            part = part.strip()
            if part:
                out.append(f"{SITE}/manga/{slug_from_url(part)}" if "://" not in part
                           else part)
    seen, uniq = set(), []
    for u in out:
        if u not in seen:
            seen.add(u)
            uniq.append(u)
    return uniq


# ---------------------------------------------------------------- выбор глав

def chapters_to_read(api, slug: str, read_set: set[str], skip_paid: bool = True):
    """Главы тайтла, которые ещё не отмечены прочитанными (API + state)."""
    info = api.title_info(slug)
    if not info:
        return None, []
    branches = info.get("branches") or []
    if not branches:
        return info, []

    todo = []
    for branch in (branches[:1] if os.getenv("READ_BRANCHES", "first") != "all" else branches):
        chapters = api.all_chapters(branch["id"])
        for ch in chapters:
            cid = ch.get("id")
            if not cid:
                continue
            if rdr.view_is_read(ch.get("viewed")):
                continue
            if str(cid) in read_set:
                continue
            if skip_paid and ch.get("is_paid") and not ch.get("is_bought") \
                    and not ch.get("is_free_today"):
                continue
            ch["_branch_id"] = branch["id"]
            todo.append(ch)

    todo.sort(key=lambda c: (int(c.get("index") or 0), int(c.get("id") or 0)))
    return info, todo


# ---------------------------------------------------------------- основной фарм

def farm_lightning(page=None, session=None, api=None, log=_log) -> dict:
    """Читает главы до дневного лимита, отмечает прочитанным, забирает задания.

    Env:
      READ_TITLE_URLS / READ_TITLE_SLUGS — конкретные тайтлы (через запятую)
      READ_COLLECTION_URL                — если тайтлы не заданы (DOM-сбор)
      LIGHTNING_MAX_CHAPTERS             — дневной лимит глав (96)
      LIGHTNING_SKIP_PAID                — 1 (по умолчанию) пропускать платные
      LIGHTNING_PAUSE_MS                 — пауза между главами, '300-1200'
    """
    started = time.time()
    max_chapters = _env_int("LIGHTNING_MAX_CHAPTERS", 96)
    skip_paid = _env_bool("LIGHTNING_SKIP_PAID", True)

    if api is None:
        api = api_from_env(log=log)
    if api is None:
        log("[lightning] куки не найдены — фарм пропущен")
        return {"ok": False, "reason": "no_cookies"}

    auth_ok, auth_detail = api.authorized()
    if not auth_ok:
        log(f"[lightning] API не авторизован ({auth_detail}) — фарм пропущен")
        return {"ok": False, "reason": "unauthorized", "detail": auth_detail}

    state = load_state()
    if state["count"] >= max_chapters:
        log(f"[lightning] дневной лимит уже достигнут: {state['count']}")
        return {"ok": True, "reason": "limit", "count": state["count"]}

    if page is None and session is None:
        log("[lightning] нет страницы/сессии")
        return {"ok": False, "reason": "no_page"}

    # --- список тайтлов
    titles = _titles_from_env()
    if not titles:
        env_collection = os.getenv("READ_COLLECTION_URL") or f"{SITE}/collections/2197"
        try:
            titles = collect_collection_titles(session.get() if session else page,
                                               env_collection)
        except Exception as exc:
            log(f"[lightning] сбор коллекции не удался: {exc}")
            titles = []
    if not titles:
        log("[lightning] тайтлы не найдены")
        return {"ok": False, "reason": "no_titles"}

    read_set = {str(x) for x in state.get("read", [])}
    random.shuffle(titles)

    read_cfg = rdr.scroll_cfg_from_env()
    pause_raw = os.getenv("LIGHTNING_PAUSE_MS") or "400-1600"
    try:
        lo_s, hi_s = pause_raw.split("-", 1)
        pause_lo, pause_hi = int(lo_s), int(hi_s)
    except ValueError:
        pause_lo = pause_hi = int(pause_raw) if pause_raw.isdigit() else 800

    comment_raw = os.getenv("CHAPTER_COMMENT") or ""
    comment_texts = [t.strip() for t in comment_raw.replace("\\n", "\n").split("|")
                     if t.strip()]
    comment_once = _env_bool("CHAPTER_COMMENT_ONCE", False)
    comment_burst = max(1, _env_int("CHAPTER_COMMENT_BURST", 4))
    comment_pause = _env_int("CHAPTER_COMMENT_BURST_PAUSE_MIN", 3) * 60
    comment_gap_lo = _env_int("CHAPTER_COMMENT_GAP_MIN_S", 20)
    comment_gap_hi = _env_int("CHAPTER_COMMENT_GAP_MAX_S", 30)

    read_done = 0
    commented = 0
    claimed_after = False
    dailies_claimed = 0
    errors = 0
    not_found = 0
    note = ""

    for title_url in titles:
        if state["count"] >= max_chapters:
            note = "достигнут LIGHTNING_MAX_CHAPTERS"
            break

        slug = slug_from_url(title_url)
        if not slug:
            continue

        try:
            info, todo = chapters_to_read(api, slug, read_set, skip_paid)
        except Exception as exc:
            log(f"[lightning] не удалось получить главы {slug}: {exc}")
            errors += 1
            continue
        if not info:
            not_found += 1
            # не заливаем лог: первые 3 поимённо, дальше каждые 25-й
            if not_found <= 3 or not_found % 25 == 0:
                log(f"[lightning] тайтл не найден: {slug}")
            continue
        if not todo:
            continue

        title_id = info.get("id")
        name = info.get("main_name") or info.get("rus_name") or slug
        log(f"[lightning] ▶ {name}: непрочитанных глав {len(todo)}")

        for ch in todo:
            if state["count"] >= max_chapters:
                break

            cid = int(ch["id"])
            url = f"{SITE}/manga/{slug}/{cid}?page=1"
            attempts = 0
            ok = False

            while attempts < 3 and not ok:
                attempts += 1
                try:
                    pg = session.get() if session else page
                    res = rdr.read_chapter(pg, url, cid, read_cfg)
                    status = rdr.mark_viewed(api, ch, title_id, ch.get("_branch_id"),
                                             read_cfg, log=log)
                    ok = True
                    log(f"[lightning]   глава {cid}: скролл "
                        f"{'конец в кадре' if res.get('at_end') else 'НЕ в кадре'}, "
                        f"отметка: {status}")
                except Exception as exc:
                    if rdr.looks_dead(exc) and session is not None:
                        log(f"[lightning]   браузер вылетел ({exc}) — перезапуск")
                        session.restart()
                        errors += 1
                        continue
                    log(f"[lightning]   ошибка чтения главы {cid}: {exc}")
                    errors += 1
                    break

            if not ok:
                continue

            state["count"] += 1
            state.setdefault("read", []).append(cid)
            read_set.add(str(cid))
            read_done += 1
            save_state(state)

            if comment_texts and (not comment_once or commented < 1):
                text = comment_texts[commented % len(comment_texts)]
                try:
                    sent, detail = api.comment_chapter(cid, text)
                    commented += 1
                    state.setdefault("comments", []).append(cid)
                    save_state(state)
                    log(f"[lightning]   💬 комментарий {commented} к главе "
                        f"{cid}: {'отправлен' if sent else 'не отправлен — ' + detail}")
                    if (comment_burst > 1 and comment_pause
                            and commented % comment_burst == 0):
                        log(f"[lightning]   ⏸ пауза {comment_pause // 60} мин "
                            f"после пачки из {comment_burst} комментариев")
                        time.sleep(comment_pause)
                    else:
                        gap = random.uniform(comment_gap_lo,
                                             comment_gap_hi)
                        log(f"[lightning]   ⏸ пауза {gap:.0f} с "
                            f"перед следующим комментарием")
                        time.sleep(gap)
                except Exception as exc:
                    log(f"[lightning]   💬 комментарий к главе {cid} "
                        f"не удался: {exc}")

            if not claimed_after and state["count"] >= 10:
                dailies_claimed += len(claim_dailies(api, log=log))
                claimed_after = True

            time.sleep(random.uniform(pause_lo, pause_hi) / 1000.0)

    if not claimed_after:
        dailies_claimed += len(claim_dailies(api, log=log))

    if not_found:
        log(f"[lightning] не найдено тайтлов: {not_found} из {len(titles)} "
            f"(проверь API/куки — подробности выше)")

    save_state(state)
    result = {
        "ok": True,
        "read": read_done,
        "comments": commented,
        "total_today": state["count"],
        "max_today": max_chapters,
        "dailies_claimed": dailies_claimed,
        "errors": errors,
        "note": note,
        "seconds": round(time.time() - started, 1),
    }
    log(f"[lightning] готово: прочитано {read_done} (сегодня {state['count']}"
        f"/{max_chapters}), ошибок {errors}"
        + (f", {note}" if note else ""))
    return result


__all__ = [
    "farm_lightning",
    "collect_collection_titles",
    "load_state",
    "save_state",
    "slug_from_url",
    "chapters_to_read",
]
