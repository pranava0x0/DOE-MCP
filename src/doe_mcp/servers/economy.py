"""Serve regional economics inputs from GCAM."""
from .gcam import serve


def main() -> int:
    return serve("economy")


if __name__ == "__main__":
    raise SystemExit(main())
