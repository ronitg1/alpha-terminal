from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

import httpx

from app.backend.services.api_key_validation import ROBINHOOD
from app.backend.services.key_resolver import MissingUserKey, require_key

_DEFAULT_ROBINHOOD_MCP_URL = "https://agent.robinhood.com/mcp/trading"
_ROBINHOOD_MCP_HOST = "agent.robinhood.com"

_SAFE_TOOL_NAMES = frozenset(
    {
        "portfolio",
        "get_portfolio",
        "read_portfolio",
        "view_portfolio",
        "portfolio_summary",
        "list_holdings",
        "get_holdings",
        "read_holdings",
        "list_positions",
        "get_positions",
        "read_positions",
        "get_account_positions",
        "get_account_holdings",
        "list_balances",
        "get_balances",
        "read_balances",
        "cash_balances",
        "get_cash_balances",
        "portfolio_snapshot",
        "get_portfolio_snapshot",
    }
)

_SENSITIVE_RESULT_FIELDS = frozenset(
    {
        "account_number",
        "accountnumber",
        "account_id",
        "accountid",
        "routing_number",
        "routingnumber",
        "ssn",
        "tax_id",
        "taxid",
        "email",
        "username",
        "token",
        "access_token",
        "refresh_token",
    }
)


class RobinhoodMcpError(RuntimeError):
    pass


class RobinhoodMcpAuthRequired(RobinhoodMcpError):
    pass


class RobinhoodMcpToolNotFound(RobinhoodMcpError):
    def __init__(self, tool_names: list[str]):
        self.tool_names = tool_names
        super().__init__("No read-only Robinhood portfolio MCP tool was found.")


def _tool_name(tool: Any) -> str:
    if isinstance(tool, dict):
        return str(tool.get("name") or "").strip()
    return str(getattr(tool, "name", "") or "").strip()


def is_safe_portfolio_tool(tool: Any) -> bool:
    name = _tool_name(tool).lower()
    return name in _SAFE_TOOL_NAMES


def robinhood_mcp_url() -> str:
    raw = os.environ.get("ROBINHOOD_MCP_URL", _DEFAULT_ROBINHOOD_MCP_URL).strip() or _DEFAULT_ROBINHOOD_MCP_URL
    parsed = urlparse(raw)
    if parsed.scheme != "https" or parsed.hostname != _ROBINHOOD_MCP_HOST or parsed.username or parsed.password:
        raise RobinhoodMcpError("Robinhood MCP endpoint must be an HTTPS URL on agent.robinhood.com.")
    return raw


def select_portfolio_tools(tools: list[Any]) -> list[Any]:
    safe = [tool for tool in tools if is_safe_portfolio_tool(tool)]
    portfolio = [tool for tool in safe if "portfolio" in _tool_name(tool).lower()]
    if portfolio:
        return portfolio[:1]
    return safe[:3]


def _decode_content_item(item: Any) -> Any:
    if isinstance(item, dict):
        if "structuredContent" in item:
            return item["structuredContent"]
        text = item.get("text")
        if isinstance(text, str):
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                return text
        return item
    structured = getattr(item, "structuredContent", None)
    if structured is not None:
        return structured
    text = getattr(item, "text", None)
    if isinstance(text, str):
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return text
    if hasattr(item, "model_dump"):
        return item.model_dump()
    return item


def _decode_tool_result(result: Any) -> Any:
    if isinstance(result, dict):
        if "structuredContent" in result:
            return result["structuredContent"]
        content = result.get("content")
        if not isinstance(content, list):
            return result
        decoded = [_decode_content_item(item) for item in content]
        return decoded[0] if len(decoded) == 1 else decoded
    structured = getattr(result, "structuredContent", None)
    if structured is not None:
        return structured
    content = getattr(result, "content", None)
    if not isinstance(content, list):
        return result.model_dump() if hasattr(result, "model_dump") else result
    decoded: list[Any] = []
    for item in content:
        decoded.append(_decode_content_item(item))
    return decoded[0] if len(decoded) == 1 else decoded


def _redact_sensitive_result(value: Any) -> Any:
    if isinstance(value, dict):
        redacted: dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key)
            normalized = key_text.replace("-", "_").lower()
            redacted[key_text] = "[redacted]" if normalized in _SENSITIVE_RESULT_FIELDS else _redact_sensitive_result(item)
        return redacted
    if isinstance(value, list):
        return [_redact_sensitive_result(item) for item in value]
    return value


def initialize_payload(request_id: int) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "alpha-terminal", "version": "1.7.6"},
        },
    }


def parse_mcp_payload(response: httpx.Response, expected_id: int | None = None) -> dict[str, Any]:
    content_type = response.headers.get("content-type", "")
    if "text/event-stream" not in content_type:
        try:
            payload = response.json()
        except json.JSONDecodeError as exc:
            raise RobinhoodMcpError("MCP response was not valid JSON.") from exc
        if isinstance(payload, dict):
            if expected_id is not None and payload.get("id") != expected_id:
                raise RobinhoodMcpError("MCP response id did not match the request.")
            return payload
        raise RobinhoodMcpError("MCP response was not a JSON object.")

    event_data: list[str] = []
    for line in [*response.text.splitlines(), ""]:
        if line == "":
            if not event_data:
                continue
            data = "\n".join(event_data).strip()
            event_data = []
        elif line.startswith("data:"):
            event_data.append(line[5:].lstrip())
            continue
        else:
            continue
        if not data or data == "[DONE]":
            continue
        try:
            payload = json.loads(data)
        except json.JSONDecodeError as exc:
            raise RobinhoodMcpError("MCP event stream included invalid JSON.") from exc
        if isinstance(payload, dict) and (expected_id is None or payload.get("id") == expected_id):
            return payload
    raise RobinhoodMcpError("MCP event stream did not include a JSON payload.")


class _McpHttpClient:
    def __init__(self, endpoint: str, bearer_token: str):
        self.endpoint = endpoint
        self.bearer_token = bearer_token
        self.session_id: str | None = None
        self.next_id = 1

    def _headers(self) -> dict[str, str]:
        headers = {
            "Authorization": f"Bearer {self.bearer_token}",
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
            "MCP-Protocol-Version": "2025-06-18",
        }
        if self.session_id:
            headers["Mcp-Session-Id"] = self.session_id
        return headers

    async def request(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        request_id = self.next_id
        self.next_id += 1
        payload = {"jsonrpc": "2.0", "id": request_id, "method": method}
        if params is not None:
            payload["params"] = params
        async with httpx.AsyncClient(timeout=30.0, follow_redirects=False) as client:
            response = await client.post(self.endpoint, headers=self._headers(), json=payload)
        if response.status_code in (401, 403):
            raise RobinhoodMcpAuthRequired("Robinhood MCP authorization is required.")
        response.raise_for_status()
        session_id = response.headers.get("Mcp-Session-Id") or response.headers.get("mcp-session-id")
        if session_id:
            self.session_id = session_id
        data = parse_mcp_payload(response, expected_id=request_id)
        if data.get("error"):
            raise RobinhoodMcpError(str(data["error"]))
        result = data.get("result")
        return result if isinstance(result, dict) else {}

    async def notify_initialized(self) -> None:
        async with httpx.AsyncClient(timeout=10.0, follow_redirects=False) as client:
            response = await client.post(
                self.endpoint,
                headers=self._headers(),
                json={"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}},
            )
        if response.status_code in (401, 403):
            raise RobinhoodMcpAuthRequired("Robinhood MCP authorization is required.")
        if response.status_code not in (200, 202, 204):
            response.raise_for_status()


async def fetch_portfolio() -> dict[str, Any]:
    endpoint = robinhood_mcp_url()
    try:
        token = require_key(ROBINHOOD)
    except MissingUserKey as exc:
        raise RobinhoodMcpAuthRequired("Add a Robinhood MCP token in Settings before pulling the portfolio.") from exc

    client = _McpHttpClient(endpoint, token)
    await client.request("initialize", initialize_payload(client.next_id)["params"])
    await client.notify_initialized()
    tools_result = await client.request("tools/list")
    tools = tools_result.get("tools") if isinstance(tools_result.get("tools"), list) else []
    selected = select_portfolio_tools(tools)
    if not selected:
        raise RobinhoodMcpToolNotFound([_tool_name(tool) for tool in tools])

    pulled = []
    for tool in selected:
        name = _tool_name(tool)
        result = await client.request("tools/call", {"name": name, "arguments": {}})
        if result.get("isError") is True:
            raise RobinhoodMcpError("Robinhood portfolio tool returned an error.")
        pulled.append({"tool": name, "data": _redact_sensitive_result(_decode_tool_result(result))})
    return {
        "status": "ok",
        "endpoint": endpoint,
        "asof": datetime.now(timezone.utc).isoformat(),
        "tools": pulled,
    }
