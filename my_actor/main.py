"""Apify entrypoint for ReManga Auto."""

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

from .lightning_farm import farm_lightning


def main() -> None:
    # Используем старый браузерный режим lightning farm:
    # открыть главу -> реально проскроллить reader до конца -> следующая глава.
    root_main.farm_lightning = farm_lightning

    proxy_url = os.getenv("CUSTOM_PROXY") or os.getenv("PROXY_URL")
    root_main.run_dungeon_bot(proxy_url=proxy_url)


if __name__ == "__main__":
    main()
