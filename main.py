from mcp.server.fastmcp import FastMCP

mcp = FastMCP("OverwatchStats", json_response=True)


@mcp.tool()
def ping() -> str:
    """Health check - returns pong."""
    return "pong"


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
