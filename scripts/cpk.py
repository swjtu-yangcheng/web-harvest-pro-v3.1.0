#!/usr/bin/env python3
"""Compatibility entry point: the v3 portable CLI replaces environment-bound cpk."""
from harvest import main

if __name__ == "__main__":
    raise SystemExit(main())
