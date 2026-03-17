import sys
from urllib.error import URLError
from urllib.request import urlopen

from config_validator import AppConfig


def main() -> int:
    try:
        cfg = AppConfig.load_with_defaults("config/config.yaml")
        url = f"http://127.0.0.1:{cfg.health.port}/live"
        with urlopen(url, timeout=5) as response:
            return 0 if 200 <= response.status < 400 else 1
    except (OSError, URLError):
        return 1


if __name__ == "__main__":
    sys.exit(main())
