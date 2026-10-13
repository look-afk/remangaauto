"""Best-effort collection of already-unlocked Re:Pass rewards via the visible UI.

This deliberately does not call undocumented reward endpoints or click purchase/
subscription controls. If the current site UI changes, it logs and safely skips.
"""
from __future__ import annotations

import re
from urllib.parse import urlparse

from .log import log as _log
from .remanga_api import SITE


_REPASS = re.compile(r"^\s*re\s*:?\s*pass\s*$", re.IGNORECASE)
_CLAIM = re.compile(
    r"^\s*(?:забрать награду|получить награду|забрать|получить)\s*$",
    re.IGNORECASE,
)


def claim_repass_rewards(page, log=_log, max_claims: int = 50) -> dict:
    """Open Re:Pass and click only visible, enabled reward-claim controls."""
    claimed = 0
    try:
        page.goto(SITE, wait_until="domcontentloaded", timeout=45000)
        page.wait_for_timeout(1200)

        repass = page.get_by_text(_REPASS).first
        if not repass.is_visible(timeout=2500):
            # Some layouts put the link inside the collapsed menu.
            menu = page.get_by_role(
                "button", name=re.compile(r"^\s*(меню|menu)\s*$", re.I)
            ).first
            try:
                if menu.is_visible(timeout=700):
                    menu.click(timeout=2500)
                    page.wait_for_timeout(300)
            except Exception:
                pass
            repass = page.get_by_text(_REPASS).first

        if not repass.is_visible(timeout=1500):
            log("[repass] пункт Re:Pass не найден в интерфейсе — пропускаю.")
            return {"ok": False, "claimed": 0, "reason": "menu_not_found"}

        start_url = page.url
        repass.click(timeout=5000)
        page.wait_for_timeout(1400)

        parsed = urlparse(page.url)
        hostname = (parsed.hostname or "").lower()
        if hostname and hostname != "remanga.org" and not hostname.endswith(".remanga.org"):
            log("[repass] переход за пределы remanga.org — остановлен.")
            return {"ok": False, "claimed": 0, "reason": "external_redirect"}

        path = parsed.path.lower()
        heading_found = False
        try:
            heading_found = page.get_by_role("heading", name=_REPASS).count() > 0
        except Exception:
            pass
        route_changed = page.url != start_url
        route_matches = "repass" in path or "re:pass" in path or "ре-пасс" in path
        if not (route_changed or route_matches or heading_found):
            log("[repass] страница Re:Pass не подтверждена — ничего не нажимаю.")
            return {"ok": False, "claimed": 0, "reason": "page_not_confirmed"}

        for _ in range(max(0, int(max_claims))):
            controls = page.get_by_role("button", name=_CLAIM)
            candidate = None
            try:
                count = min(controls.count(), 30)
            except Exception:
                count = 0

            for index in range(count):
                button = controls.nth(index)
                try:
                    if button.is_visible(timeout=250) and button.is_enabled():
                        candidate = button
                        break
                except Exception:
                    continue

            if candidate is None:
                break

            try:
                candidate.click(timeout=2500)
                page.wait_for_timeout(650)
                claimed += 1
                log(f"[repass] получена доступная награда ({claimed}).")
            except Exception as exc:
                log(f"[repass] не удалось забрать награду: {str(exc)[:140]}")
                break

        log(f"[repass] готово: собрано наград — {claimed}.")
        return {"ok": True, "claimed": claimed}
    except Exception as exc:
        log(f"[repass] проверка наград не удалась: {str(exc)[:180]}")
        return {"ok": False, "claimed": claimed, "reason": str(exc)[:180]}
