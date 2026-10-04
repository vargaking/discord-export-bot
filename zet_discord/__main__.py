import sys

from zet_discord import __version__


def main() -> int:
    print(f"zet-discord {__version__}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
