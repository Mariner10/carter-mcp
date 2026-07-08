#!/usr/bin/env python3
"""CAR-TER MCP server — stdio entry point.

Kept at the repo root so existing MCP registrations (`python server.py`) keep
working. The implementation lives in the `carter_mcp` package beside this file —
see `carter_mcp/__init__.py` for the package map. Equivalent to
`python -m carter_mcp`.
"""

from carter_mcp.app import main

if __name__ == "__main__":
    main()
