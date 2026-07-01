from __future__ import annotations

import anyio
import httpx
import pytest
from fastapi.testclient import TestClient

from app.backend.main import app
from app.backend.routes import robinhood as robinhood_route
from app.backend.services import api_key_validation as key_validation
from app.backend.services import robinhood_mcp

client = TestClient(app)


def test_select_portfolio_tools_prefers_portfolio_and_blocks_trading_tools() -> None:
    tools = [
        {"name": "place_trade"},
        {"name": "get_orders"},
        {"name": "get_account"},
        {"name": "get_portfolio"},
        {"name": "cancel_order"},
    ]

    selected = robinhood_mcp.select_portfolio_tools(tools)

    assert selected == [{"name": "get_portfolio"}]


def test_select_portfolio_tools_rejects_mutating_portfolio_names() -> None:
    tools = [
        {"name": "sell_position"},
        {"name": "close_position"},
        {"name": "liquidate_portfolio"},
        {"name": "withdraw_balance"},
    ]

    selected = robinhood_mcp.select_portfolio_tools(tools)

    assert selected == []


def test_select_portfolio_tools_rejects_mutating_names_after_safe_words() -> None:
    tools = [
        {"name": "get_portfolio_orders"},
        {"name": "get_portfolio_transactions"},
        {"name": "get_portfolio_transfer_history"},
        {"name": "get_portfolio_and_trade"},
        {"name": "list_positions_to_liquidate"},
        {"name": "get_positions_and_sell"},
    ]

    selected = robinhood_mcp.select_portfolio_tools(tools)

    assert selected == []


def test_select_portfolio_tools_falls_back_to_position_and_balance_tools() -> None:
    tools = [
        {"name": "get_transactions"},
        {"name": "list_positions"},
        {"name": "cash_balances"},
        {"name": "holdings"},
        {"name": "account_profile"},
    ]

    selected = robinhood_mcp.select_portfolio_tools(tools)

    assert selected == [
        {"name": "list_positions"},
        {"name": "cash_balances"},
    ]


def test_decode_tool_result_reads_json_text_content() -> None:
    result = {"content": [{"type": "text", "text": '{"positions":[{"symbol":"AAPL","qty":2}]}'}]}

    decoded = robinhood_mcp._decode_tool_result(result)

    assert decoded == {"positions": [{"symbol": "AAPL", "qty": 2}]}


def test_redact_sensitive_result_fields() -> None:
    result = {
        "account_number": "123456789",
        "positions": [{"symbol": "AAPL", "account_id": "acct-1"}],
        "cash": 10.0,
    }

    redacted = robinhood_mcp._redact_sensitive_result(result)

    assert redacted == {
        "account_number": "[redacted]",
        "positions": [{"symbol": "AAPL", "account_id": "[redacted]"}],
        "cash": 10.0,
    }


def test_parse_mcp_payload_reads_sse_data() -> None:
    response = httpx.Response(
        200,
        headers={"content-type": "text/event-stream"},
        content=(
            b'event: message\ndata: {"jsonrpc":"2.0","method":"notifications/progress","params":{}}\n\n'
            b'event: message\ndata: {"jsonrpc":"2.0","id":7,"result":{"ok":true}}\n\n'
        ),
    )

    payload = robinhood_mcp.parse_mcp_payload(response, expected_id=7)

    assert payload == {"jsonrpc": "2.0", "id": 7, "result": {"ok": True}}


def test_parse_mcp_payload_reads_multiline_sse_data() -> None:
    response = httpx.Response(
        200,
        headers={"content-type": "text/event-stream"},
        content=(
            b"event: message\n"
            b'data: {"jsonrpc":"2.0",\n'
            b'data: "id":8,\n'
            b'data: "result":{"ok":true}}\n\n'
        ),
    )

    payload = robinhood_mcp.parse_mcp_payload(response, expected_id=8)

    assert payload == {"jsonrpc": "2.0", "id": 8, "result": {"ok": True}}


def test_parse_mcp_payload_rejects_mismatched_json_id() -> None:
    response = httpx.Response(
        200,
        headers={"content-type": "application/json"},
        json={"jsonrpc": "2.0", "id": 4, "result": {"ok": True}},
    )

    with pytest.raises(robinhood_mcp.RobinhoodMcpError):
        robinhood_mcp.parse_mcp_payload(response, expected_id=5)


def test_parse_mcp_payload_rejects_invalid_json() -> None:
    response = httpx.Response(
        200,
        headers={"content-type": "application/json"},
        content=b"not-json",
    )

    with pytest.raises(robinhood_mcp.RobinhoodMcpError):
        robinhood_mcp.parse_mcp_payload(response)


def test_mcp_http_client_sends_auth_headers_and_tracks_session(monkeypatch: pytest.MonkeyPatch) -> None:
    requests: list[dict] = []

    class _FakeAsyncClient:
        def __init__(self, timeout: float, follow_redirects: bool):
            self.timeout = timeout
            self.follow_redirects = follow_redirects

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def post(self, endpoint: str, headers: dict, json: dict) -> httpx.Response:
            requests.append({"endpoint": endpoint, "headers": dict(headers), "json": dict(json)})
            return httpx.Response(
                200,
                request=httpx.Request("POST", endpoint),
                headers={"content-type": "application/json", "Mcp-Session-Id": "session-1"},
                json={"jsonrpc": "2.0", "id": json["id"], "result": {"tools": []}},
            )

    monkeypatch.setattr(robinhood_mcp.httpx, "AsyncClient", _FakeAsyncClient)

    async def _run() -> None:
        mcp = robinhood_mcp._McpHttpClient("https://example.test/mcp", "rh-token")
        await mcp.request("tools/list")
        await mcp.request("tools/list")

    anyio.run(_run)

    assert requests[0]["headers"]["Authorization"] == "Bearer rh-token"
    assert requests[0]["headers"]["Accept"] == "application/json, text/event-stream"
    assert "Mcp-Session-Id" not in requests[0]["headers"]
    assert requests[1]["headers"]["Mcp-Session-Id"] == "session-1"
    assert [req["json"]["id"] for req in requests] == [1, 2]


def test_fetch_portfolio_calls_only_selected_read_only_tool(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, dict | None]] = []

    class _FakeClient:
        def __init__(self, endpoint: str, bearer_token: str):
            self.endpoint = endpoint
            self.bearer_token = bearer_token
            self.next_id = 1

        async def request(self, method: str, params: dict | None = None) -> dict:
            calls.append((method, params))
            if method == "initialize":
                return {}
            if method == "tools/list":
                return {
                    "tools": [
                        {"name": "place_trade"},
                        {"name": "get_orders"},
                        {"name": "get_portfolio"},
                    ]
                }
            if method == "tools/call":
                return {"content": [{"type": "text", "text": '{"positions":[{"symbol":"AAPL"}]}'}]}
            raise AssertionError(method)

        async def notify_initialized(self) -> None:
            calls.append(("notifications/initialized", None))

    monkeypatch.setattr(robinhood_mcp, "require_key", lambda provider: "rh-token")
    monkeypatch.setattr(robinhood_mcp, "_McpHttpClient", _FakeClient)

    payload = anyio.run(robinhood_mcp.fetch_portfolio)

    assert payload["status"] == "ok"
    assert payload["tools"] == [{"tool": "get_portfolio", "data": {"positions": [{"symbol": "AAPL"}]}}]
    assert ("tools/call", {"name": "get_portfolio", "arguments": {}}) in calls
    assert all(params is None or params.get("name") != "place_trade" for _method, params in calls)


def test_fetch_portfolio_rejects_invalid_mcp_endpoint_before_token_lookup(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ROBINHOOD_MCP_URL", "https://evil.example/mcp")
    monkeypatch.setattr(robinhood_mcp, "require_key", lambda provider: (_ for _ in ()).throw(AssertionError("token looked up")))

    with pytest.raises(robinhood_mcp.RobinhoodMcpError):
        anyio.run(robinhood_mcp.fetch_portfolio)


def test_fetch_portfolio_fails_on_mcp_tool_error(monkeypatch: pytest.MonkeyPatch) -> None:
    class _FakeClient:
        def __init__(self, endpoint: str, bearer_token: str):
            self.next_id = 1

        async def request(self, method: str, params: dict | None = None) -> dict:
            if method == "initialize":
                return {}
            if method == "tools/list":
                return {"tools": [{"name": "get_portfolio"}]}
            if method == "tools/call":
                return {"isError": True, "content": [{"type": "text", "text": "denied"}]}
            raise AssertionError(method)

        async def notify_initialized(self) -> None:
            return None

    monkeypatch.setattr(robinhood_mcp, "require_key", lambda provider: "rh-token")
    monkeypatch.setattr(robinhood_mcp, "_McpHttpClient", _FakeClient)

    with pytest.raises(robinhood_mcp.RobinhoodMcpError):
        anyio.run(robinhood_mcp.fetch_portfolio)


def test_robinhood_route_returns_portfolio(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _fake_fetch() -> dict:
        return {
            "status": "ok",
            "endpoint": "https://example.test/mcp",
            "asof": "2026-06-30T00:00:00+00:00",
            "tools": [{"tool": "get_portfolio", "data": {"positions": []}}],
        }

    monkeypatch.setattr(robinhood_route, "fetch_portfolio", _fake_fetch)

    response = client.get("/robinhood/portfolio")

    assert response.status_code == 200
    assert response.json()["tools"] == [{"tool": "get_portfolio", "data": {"positions": []}}]


def test_robinhood_route_maps_missing_token(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _missing() -> dict:
        raise robinhood_mcp.RobinhoodMcpAuthRequired("Add a Robinhood MCP token.")

    monkeypatch.setattr(robinhood_route, "fetch_portfolio", _missing)

    response = client.get("/robinhood/portfolio")

    assert response.status_code == 400
    assert "Robinhood MCP token" in response.json()["detail"]


def test_robinhood_route_sanitizes_mcp_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _failure() -> dict:
        raise robinhood_mcp.RobinhoodMcpError("raw downstream account error")

    monkeypatch.setattr(robinhood_route, "fetch_portfolio", _failure)

    response = client.get("/robinhood/portfolio")

    assert response.status_code == 502
    assert response.json()["detail"] == "Robinhood MCP request failed. Check your token and try again."


def test_robinhood_key_validation_classifies_auth_rejection(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Resp:
        status_code = 401

    monkeypatch.setattr(key_validation.httpx, "post", lambda *args, **kwargs: _Resp())

    with pytest.raises(key_validation.KeyValidationError):
        key_validation.validate_provider_key("robinhood", "bad-token")


def test_robinhood_key_validation_rejects_empty_initialize_status(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Resp:
        status_code = 202
        content = b""

    monkeypatch.setattr(key_validation.httpx, "post", lambda *args, **kwargs: _Resp())

    with pytest.raises(key_validation.KeyValidationUnavailable):
        key_validation.validate_provider_key("robinhood", "rh-token")


def test_robinhood_key_validation_rejects_jsonrpc_error(monkeypatch: pytest.MonkeyPatch) -> None:
    response = httpx.Response(
        200,
        headers={"content-type": "application/json"},
        json={"jsonrpc": "2.0", "id": 1, "error": {"code": -32000, "message": "invalid token"}},
    )

    monkeypatch.setattr(key_validation.httpx, "post", lambda *args, **kwargs: response)

    with pytest.raises(key_validation.KeyValidationError):
        key_validation.validate_provider_key("robinhood", "bad-token")


def test_robinhood_key_validation_rejects_non_robinhood_endpoint_without_posting(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ROBINHOOD_MCP_URL", "https://evil.example/mcp")
    monkeypatch.setattr(key_validation.httpx, "post", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("posted token")))

    with pytest.raises(key_validation.KeyValidationUnavailable):
        key_validation.validate_provider_key("robinhood", "rh-token")
