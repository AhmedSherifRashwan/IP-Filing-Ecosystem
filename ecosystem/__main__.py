"""`python -m ecosystem` - the same entry point as the `ipfe` console script."""

from __future__ import annotations

from ecosystem.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
