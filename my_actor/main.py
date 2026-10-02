"""Apify entrypoint for ReManga Auto."""

import asyncio
import importlib.util
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ROOT_MAIN = ROOT / "main.py"

if not ROOT_MAIN.exists():
    raise FileNotFoundError(f"Root bot implementation not found: {ROOT_MAIN}")

spec = importlib.util.spec_from_file_location(
    "remangaauto_root_main",
    ROOT_MAIN,
)

if spec is None or spec.loader is None:
    raise ImportError(f"Could not load bot implementation: {ROOT_MAIN}")

root_main = importlib.util.module_from_spec(spec)
spec.loader.exec_module(root_main)

from apify import Actor

from .lightning_farm import farm_lightning
from .log import set_logger

# Логи актора идут через Actor.log — он цензурирует токены/куки.
set_logger(Actor.log.info)

root_main.farm_lightning = farm_lightning

_BOOL_INPUT = {
    "farmSilver": "FARM_SILVER",
    "farmLightning": "FARM_LIGHTNING",
    "dungeonUiLoop": "FARM_DUNGEON_UI",
}
_INT_INPUT = {
    "silverMaxRaids": "SILVER_MAX_RAIDS",
    "maxChaptersPerDay": "LIGHTNING_MAX_CHAPTERS",
}
_STR_INPUT = {
    "readCollectionUrl": "READ_COLLECTION_URL",
    "proxyUrl": "PROXY_URL",
}


def apply_input(data: dict | None) -> None:
    """Переносит input Актора в переменные окружения бота."""
    if not isinstance(data, dict):
        return

    cookies = data.get("cookies")
    if isinstance(cookies, str) and cookies.strip():
        os.environ["REMANGA_COOKIES_RAW"] = cookies

    for key, env in _BOOL_INPUT.items():
        if isinstance(data.get(key), bool):
            os.environ[env] = "1" if data[key] else "0"

    for key, env in _INT_INPUT.items():
        value = data.get(key)
        if isinstance(value, (int, float)) and int(value) >= 0:
            os.environ[env] = str(int(value))

    for key, env in _STR_INPUT.items():
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            os.environ[env] = value.strip()

    titles = data.get("readTitleUrls")
    if isinstance(titles, list) and titles:
        urls = [str(t).strip() for t in titles if str(t).strip()]
        if urls:
            os.environ["READ_TITLE_URLS"] = ",".join(urls)


async def _on_aborting() -> None:
    """Быстрый выход при остановке Actor (экономим compute units)."""
    await asyncio.sleep(1)
    await Actor.exit()


Actor.on("aborting", _on_aborting)


async def async_main() -> None:
    await Actor.init()
    apply_input(await Actor.get_input())

    proxy_url = os.getenv("CUSTOM_PROXY") or os.getenv("PROXY_URL")
    root_main.run_dungeon_bot(proxy_url=proxy_url)

    await Actor.exit()


def main() -> None:
    asyncio.run(async_main())


if __name__ == "__main__":
    main()
