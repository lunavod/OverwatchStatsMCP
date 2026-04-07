"""Test OAuth authentication against a local MCP server.

Usage:
    python scripts/test_auth.py [--server http://localhost:8000]

Flow:
    1. Script discovers OAuth metadata and registers as a client
    2. Prints an authorization URL — open it in your browser
    3. Sign in with Google, get redirected to localhost:3000/callback
    4. The script catches the callback automatically and exchanges the code
    5. Connects to the MCP server and lists available tools
"""

import argparse
import asyncio
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse

import httpx
from pydantic import AnyUrl

from mcp import ClientSession
from mcp.client.auth import OAuthClientProvider, TokenStorage
from mcp.client.streamable_http import streamable_http_client
from mcp.shared.auth import OAuthClientInformationFull, OAuthClientMetadata, OAuthToken

CALLBACK_PORT = 3000
CALLBACK_URL = f"http://localhost:{CALLBACK_PORT}/callback"


class InMemoryTokenStorage(TokenStorage):
    def __init__(self):
        self.tokens: OAuthToken | None = None
        self.client_info: OAuthClientInformationFull | None = None

    async def get_tokens(self) -> OAuthToken | None:
        return self.tokens

    async def set_tokens(self, tokens: OAuthToken) -> None:
        self.tokens = tokens

    async def get_client_info(self) -> OAuthClientInformationFull | None:
        return self.client_info

    async def set_client_info(self, client_info: OAuthClientInformationFull) -> None:
        self.client_info = client_info


# Shared state for the callback server
_callback_result: asyncio.Future | None = None
_loop: asyncio.AbstractEventLoop | None = None


class CallbackHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path.startswith("/callback"):
            params = parse_qs(urlparse(self.path).query)
            code = params.get("code", [None])[0]
            state = params.get("state", [None])[0]

            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(
                b"<html><body><h1>Authentication successful!</h1>"
                b"<p>You can close this tab.</p></body></html>"
            )

            if _callback_result and _loop:
                _loop.call_soon_threadsafe(_callback_result.set_result, (code, state))
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        pass  # Suppress HTTP logs


def _start_callback_server():
    server = HTTPServer(("localhost", CALLBACK_PORT), CallbackHandler)
    server.handle_request()  # Handle exactly one request then stop
    server.server_close()


async def redirect_handler(auth_url: str) -> None:
    print(f"\nOpen this URL in your browser to sign in:\n")
    print(f"  {auth_url}\n")
    print(f"Waiting for callback on http://localhost:{CALLBACK_PORT}/callback ...")


async def callback_handler() -> tuple[str, str | None]:
    global _callback_result, _loop
    _loop = asyncio.get_running_loop()
    _callback_result = _loop.create_future()

    # Start the callback server in a background thread
    thread = threading.Thread(target=_start_callback_server, daemon=True)
    thread.start()

    code, state = await _callback_result
    return code, state


async def main(server_url: str):
    print(f"Connecting to {server_url} ...")

    oauth = OAuthClientProvider(
        server_url=server_url,
        client_metadata=OAuthClientMetadata(
            client_name="OverwatchStats Test Client",
            redirect_uris=[AnyUrl(CALLBACK_URL)],
            grant_types=["authorization_code", "refresh_token"],
            response_types=["code"],
        ),
        storage=InMemoryTokenStorage(),
        redirect_handler=redirect_handler,
        callback_handler=callback_handler,
    )

    async with httpx.AsyncClient(auth=oauth) as http:
        async with streamable_http_client(
            f"{server_url}/mcp", http_client=http
        ) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()

                print("\nAuthenticated successfully!\n")

                tools = await session.list_tools()
                print(f"Available tools ({len(tools.tools)}):")
                for tool in tools.tools:
                    print(f"  - {tool.name}")

                print("\nCalling ping ...")
                result = await session.call_tool("ping", {})
                print(f"  Result: {result.content[0].text}")

                print("\nDone. Auth is working.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Test OAuth against the MCP server")
    parser.add_argument("--server", default="http://localhost:8000", help="MCP server URL")
    args = parser.parse_args()

    asyncio.run(main(args.server))
