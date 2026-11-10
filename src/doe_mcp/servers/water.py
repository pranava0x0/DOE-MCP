"""Serve water use inputs from GCAM."""
from .gcam import serve


def main() -> int:
    return serve("water")


if __name__ == "__main__":
    raise SystemExit(main())
