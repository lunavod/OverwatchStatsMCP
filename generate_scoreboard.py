"""Generate an Overwatch-style scoreboard image from match data.

Usage:
    uv run python generate_scoreboard.py <match_id> [--out scoreboard.png]

Requires MCP_SERVER_URL env var (or in .env) pointing to the production MCP server.
Example: MCP_SERVER_URL=http://your-server:8000/mcp/
"""

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

# Add src/ to path so we can import the shared scoreboard module
sys.path.insert(0, str(Path(__file__).parent / "src"))

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client
from scoreboard import render_scoreboard


# ---------------------------------------------------------------------------
# MCP client
# ---------------------------------------------------------------------------

async def fetch_match(server_url: str, match_id: str) -> dict:
    async with streamablehttp_client(server_url) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool("get_match", {"match_id": match_id})
            for block in result.content:
                if hasattr(block, "text"):
                    return json.loads(block.text)
            raise ValueError("No text content in MCP response")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Generate Overwatch scoreboard image")
    parser.add_argument("match_id", help="UUID of the match to render")
    parser.add_argument("--out", "-o", default="scoreboard.png", help="Output file path")
    parser.add_argument("--server", "-s", help="MCP server URL (overrides MCP_SERVER_URL env)")
    parser.add_argument("--json", "-j", help="Load match data from a JSON file instead of MCP")
    args = parser.parse_args()

    if args.json:
        with open(args.json) as f:
            match_data = json.load(f)
    else:
        server_url = args.server or os.getenv("MCP_SERVER_URL")
        if not server_url:
            print("Error: MCP_SERVER_URL env var or --server flag required", file=sys.stderr)
            sys.exit(1)
        match_data = asyncio.run(fetch_match(server_url, args.match_id))

    if "error" in match_data:
        print(f"Error from server: {match_data['error']}", file=sys.stderr)
        sys.exit(1)

    outputs = render_scoreboard(match_data, args.out)
    for out in outputs:
        print(f"Saved: {out}")


if __name__ == "__main__":
    main()
