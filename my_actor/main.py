"""Apify entrypoint compatibility layer.

The actual bot implementation lives in the repository root ``main.py``.
Apify starts this package with ``python -m my_actor``. Keeping a single
implementation prevents the old actor copy from diverging from the bot code.
"""

import importlib.util
import os
from pathlib import Path


ROOT_MAIN = Path(__file__).resolve().parent.parent / "main.py"

if not ROOT_MAIN.exists():
    raise FileNotFoundError(f"Root bot implementation not found: {ROOT_MAIN}")

spec = importlib.util.spec_from_file_location("remangaauto_root_main", ROOT_MAIN)
if spec is None or spec.loader is None:
    raise ImportError(f"Could not load bot implementation: {ROOT_MAIN}")

root_main = importlib.util.module_from_spec(spec)
spec.loader.exec_module(root_main)

run_dungeon_bot = root_main.run_dungeon_bot


def main() -> None:
    proxy_url = os.getenv("CUSTOM_PROXY") or os.getenv("PROXY_URL")
    run_dungeon_bot(proxy_url=proxy_url)


if __name__ == "__main__":
    main()
