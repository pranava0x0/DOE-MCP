"""Serve power technology inputs from GCAM."""
from .gcam import serve


def main() -> int:
    return serve("power")


if __name__ == "__main__":
    raise SystemExit(main())
