"""The MCP tool surface, grouped by concern. Importing this package registers
every tool on the shared FastMCP instance (`carter_mcp.app.mcp`)."""

from carter_mcp.tools import (  # noqa: F401 — imported for their @mcp.tool() side effects
    buffer,
    device,
    docs,
    generators,
    session,
    wiring,
)
