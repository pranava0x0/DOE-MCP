"""Serve land and agriculture inputs from GCAM."""
from .gcam import serve


def main() -> int:
    return serve("land")


if __name__ == "__main__":
    raise SystemExit(main())
