#!/usr/bin/env python3
"""Патчит корневой main.py (ротация прокси при падении API).

Запуск из корня репозитория:   python apply_patch.py
Делает резервную копию main.py.bak. Если какой-то якорь не найден или найден
несколько раз — ничего не пишет и сообщает, какой именно.
"""
import sys
from pathlib import Path

path = Path("main.py")
src = path.read_text(encoding="utf-8")

BLOCK = '''
        # ---- failover прокси (добавлено патчем) ----
        MAP_URL = "https://remanga.org/murim-cards#/map"
        failed_proxies = set()

        def _apply_api_proxy(url):
            """Переключает requests-сессию API на тот же прокси, что у браузера."""
            if api is None or not url:
                return
            full = url if "://" in url else "http://" + url
            api.proxy = {"http": full, "https": full}
            api.s.proxies.clear()
            api.s.proxies.update(api.proxy)

        def _hook_session(s):
            """После любого перезапуска браузера заново привязываем контекст к API."""
            orig = s.restart

            def restart_and_rebind():
                pg = orig()
                if api is not None:
                    api.bind_context(s.context)
                return pg

            s.restart = restart_and_rebind

        def ensure_network(stage=""):
            """Если API недоступен через прокси — берём новый и перезапускаем браузер."""
            nonlocal browser, context, page, proxy, proxy_url, launch_kwargs
            if api is None or not proxy_url:
                return True
            try:
                code, _ = api.get("/api/users/current/")
            except Exception:
                code = 0
            if code != 0:
                return True
            if os.getenv("USE_FREE_PROXY", "1") != "1":
                log(f"[proxy] {stage}: API недоступен, ротация выключена (USE_FREE_PROXY=0).")
                return False

            from my_actor.free_proxies import find_working_proxy

            max_rot = int(os.getenv("PROXY_MAX_ROTATIONS", "3") or 3)
            for n in range(1, max_rot + 1):
                failed_proxies.add(proxy_url)
                log(f"[proxy] {stage}: API недоступен, ротация {n}/{max_rot}...")
                new_url = find_working_proxy(exclude=failed_proxies)
                if not new_url:
                    log("[proxy] запасной прокси не найден.")
                    return False
                proxy_url = new_url
                proxy = parse_proxy(new_url)
                os.environ["PROXY_URL"] = new_url
                launch_kwargs = {**launch_kwargs, "proxy": proxy}
                session.launch_kwargs = launch_kwargs
                try:
                    session.restart()
                except Exception as exc:
                    log(f"[proxy] не удалось перезапустить браузер: {exc}")
                    continue
                browser, context, page = session.browser, session.context, session._page
                _apply_api_proxy(new_url)
                try:
                    goto_with_retries(page, MAP_URL, attempts=2, timeout_ms=45000)
                except Exception as exc:
                    log(f"[proxy] карта не открылась: {str(exc)[:120]}")
                try:
                    code, _ = api.get("/api/users/current/")
                except Exception:
                    code = 0
                if code != 0:
                    log("[proxy] ✅ API снова доступен.")
                    return True
            return False

        _apply_api_proxy(proxy_url)
        _hook_session(session)
        # ---- конец failover ----
'''

# (якорь, замена, где вставлять)
EDITS = [
    (
        "        api = api_from_env()\n        if api is not None:\n            api.bind_context(context)\n",
        None,  # заполним ниже: якорь + BLOCK
    ),
    (
        "            # 💬 Комментарии отдельно от чтения глав:",
        '            ensure_network("старт")\n\n            # 💬 Комментарии отдельно от чтения глав:',
    ),
    (
        "            silver_res = None\n            li_res = None\n",
        '            ensure_network("серебро")\n            silver_res = None\n            li_res = None\n',
    ),
    (
        '            if os.getenv("FARM_LIGHTNING", "1") == "1":\n',
        '            ensure_network("молнии")\n            if os.getenv("FARM_LIGHTNING", "1") == "1":\n',
    ),
    (
        "            repass_res = None\n",
        '            ensure_network("repass")\n            repass_res = None\n',
    ),
    (
        "replacement_url = find_working_proxy()",
        "replacement_url = find_working_proxy(exclude={proxy_url})",
    ),
    (
        "                if api is not None:\n                    api.bind_context(context)\n",
        "                if api is not None:\n                    api.bind_context(context)\n"
        "                _hook_session(session)\n                _apply_api_proxy(proxy_url)\n",
    ),
]
EDITS[0] = (EDITS[0][0], EDITS[0][0] + BLOCK)

if "ensure_network(" in src:
    sys.exit("Похоже, патч уже применён (найден ensure_network). Ничего не делаю.")

out = src
for anchor, repl in EDITS:
    n = out.count(anchor)
    if n != 1:
        sys.exit(f"Якорь найден {n} раз (нужно ровно 1), патч не применён:\n{anchor!r}")
    out = out.replace(anchor, repl)

Path("main.py.bak").write_text(src, encoding="utf-8")
path.write_text(out, encoding="utf-8")
print("Готово: main.py обновлён, копия в main.py.bak")