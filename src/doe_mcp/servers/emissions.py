"""Serve emissions inputs from GCAM."""
from .gcam import serve


def main() -> int:
    return serve("emissions")


if __name__ == "__main__":
    raise SystemExit(main())
