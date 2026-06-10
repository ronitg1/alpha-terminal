"""Fidelity CSV importer: symbol decoding, both file flavors, FIFO closes,
and idempotent re-import. All fixtures are synthetic — no real account data.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from app.backend.services import fidelity_import, pnl_service


@pytest.fixture(autouse=True)
def isolated_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(pnl_service, "_DATA_PATH", tmp_path / "pnl_positions.json")
    yield


# ─── Option-symbol decoding ──────────────────────────────────────────────────


def test_option_symbol_parsing():
    parsed = fidelity_import._parse_option_symbol("-NVDA260717C200")
    assert parsed == {
        "ticker": "NVDA", "type": "call", "strike": 200.0, "expiration": "2026-07-17",
    }
    fractional = fidelity_import._parse_option_symbol(" -TSLA261218P302.5")
    assert fractional["type"] == "put"
    assert fractional["strike"] == 302.5
    assert fractional["expiration"] == "2026-12-18"
    assert fidelity_import._parse_option_symbol("AAPL") is None


def test_number_parsing_handles_fidelity_formats():
    assert fidelity_import._to_float("$1,234.56") == pytest.approx(1234.56)
    assert fidelity_import._to_float("(123.45)") == pytest.approx(-123.45)
    assert fidelity_import._to_float("--") is None
    assert fidelity_import._to_float("") is None


# ─── Positions-export flavor ─────────────────────────────────────────────────

POSITIONS_CSV = """\
Account information as of Jun-09-2026

Account Number,Account Name,Symbol,Description,Quantity,Last Price,Last Price Change,Current Value,Cost Basis Total,Average Cost Basis,Type
X12345678,INDIVIDUAL,NVDA,NVIDIA CORP,10,$206.57,+$1.20,$2065.70,$1800.00,$180.00,Cash
X12345678,INDIVIDUAL,-NVDA260717C200,CALL (NVDA) NVIDIA CORP JUL 17 26 $200,2,$8.10,+$0.55,$1620.00,$1080.00,$540.00,Cash
X12345678,INDIVIDUAL,Pending Activity,,,,,$0.00,,,

"Brokerage services are provided by Fidelity Brokerage Services LLC"
"""


def test_positions_import_creates_open_positions():
    result = fidelity_import.import_csv(POSITIONS_CSV)
    assert result["flavor"] == "positions"
    assert result["imported"] == 2
    stored = pnl_service.get_all()
    by_kind = {p["kind"]: p for p in stored}

    stock = by_kind["stock"]
    assert stock["ticker"] == "NVDA"
    assert stock["qty"] == 10
    assert stock["entry_price"] == pytest.approx(180.0)  # 1800 / 10
    assert stock["status"] == "open"
    assert stock["real"] is True and stock["source"] == "fidelity"

    option = by_kind["option"]
    assert option["option"]["strike"] == 200.0
    # Cost Basis Total 1080 / (2 contracts * 100) = 5.40/share
    assert option["entry_price"] == pytest.approx(5.40)


def test_positions_reimport_is_idempotent():
    first = fidelity_import.import_csv(POSITIONS_CSV)
    second = fidelity_import.import_csv(POSITIONS_CSV)
    assert first["imported"] == 2
    assert second["imported"] == 0
    assert second["skipped"] >= 2
    assert len(pnl_service.get_all()) == 2


# ─── Transactions-export flavor ──────────────────────────────────────────────

TRANSACTIONS_CSV = """\
Brokerage

Run Date,Account,Action,Symbol,Description,Type,Quantity,Price ($),Commission ($),Fees ($),Accrued Interest ($),Amount ($),Settlement Date
06/08/2026,X12345678, YOU BOUGHT OPENING TRANSACTION,-NVDA260717C200,CALL (NVDA) NVIDIA CORP JUL 17 26 $200,Margin,2,5.40,0.00,0.04,,-1080.04,06/09/2026
06/09/2026,X12345678, YOU SOLD CLOSING TRANSACTION,-NVDA260717C200,CALL (NVDA) NVIDIA CORP JUL 17 26 $200,Margin,-1,8.10,0.65,0.04,,809.31,06/10/2026
06/05/2026,X12345678, YOU BOUGHT,AAPL,APPLE INC,Cash,10,150.00,0.00,0.00,,-1500.00,06/06/2026

"The data provided is for informational purposes"
"""


def test_transactions_import_fifo_partial_close():
    result = fidelity_import.import_csv(TRANSACTIONS_CSV)
    assert result["flavor"] == "transactions"
    stored = pnl_service.get_all()

    options = [p for p in stored if p["kind"] == "option"]
    stocks = [p for p in stored if p["kind"] == "stock"]

    # Stock buy → one open long.
    assert len(stocks) == 1
    assert stocks[0]["status"] == "open"
    assert stocks[0]["entry_date"] == "2026-06-05"
    assert stocks[0]["entry_price"] == pytest.approx(150.0)

    # 2-lot open, 1-lot close → split into 1 open + 1 closed slice.
    assert len(options) == 2
    open_slice = next(p for p in options if p["status"] == "open")
    closed_slice = next(p for p in options if p["status"] == "closed")
    assert open_slice["qty"] == 1
    assert closed_slice["qty"] == 1
    assert closed_slice["exit_price"] == pytest.approx(8.10)
    # (8.10 - 5.40) * 1 * 100
    assert pnl_service.realized_pnl(closed_slice) == pytest.approx(270.0)


def test_transactions_reimport_is_idempotent():
    fidelity_import.import_csv(TRANSACTIONS_CSV)
    n_after_first = len(pnl_service.get_all())
    second = fidelity_import.import_csv(TRANSACTIONS_CSV)
    assert second["imported"] == 0
    assert len(pnl_service.get_all()) == n_after_first


def test_unrecognizable_file_raises():
    with pytest.raises(ValueError):
        fidelity_import.import_csv("just,some,random\ncsv,data,here\n")
