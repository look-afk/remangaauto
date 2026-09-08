import os

from main import run_dungeon_bot


if __name__ == "__main__":
    proxy_url = os.getenv("CUSTOM_PROXY") or os.getenv("PROXY_URL")
    run_dungeon_bot(proxy_url=proxy_url)
