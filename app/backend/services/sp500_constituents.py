"""Curated S&P 500 constituents for the market heatmap.

The finviz/unusualwhales-style map is driven by a STATIC classification (GICS sector +
sub-industry) and a market-cap weight — these change slowly, so we hardcode the large
names that dominate the index (roughly the top ~110 by weight, ~90% of the index). The
heatmap route enriches these with live performance for the tile colour. Market caps are
approximate ($B) and only used for relative tile size. Edit as the index shifts.

Shape: ``sector -> { ticker: (sub_industry, market_cap_billions) }``.
"""
from __future__ import annotations

SP500: dict[str, dict[str, tuple[str, float]]] = {
    "Technology": {
        "AAPL": ("Consumer Electronics", 3200), "MSFT": ("Software", 2800),
        "NVDA": ("Semiconductors", 3300), "AVGO": ("Semiconductors", 1100),
        "ORCL": ("Software", 480), "CRM": ("Software", 260), "AMD": ("Semiconductors", 260),
        "ADBE": ("Software", 230), "QCOM": ("Semiconductors", 190), "TXN": ("Semiconductors", 175),
        "AMAT": ("Semiconductors", 160), "MU": ("Semiconductors", 120), "INTC": ("Semiconductors", 110),
        "LRCX": ("Semiconductors", 110), "KLAC": ("Semiconductors", 100), "ADI": ("Semiconductors", 110),
        "PANW": ("Software", 120), "CRWD": ("Software", 95), "SNPS": ("Software", 90),
        "CDNS": ("Software", 85), "MRVL": ("Semiconductors", 75), "CSCO": ("Communication Equipment", 240),
        "IBM": ("IT Services", 210), "NOW": ("Software", 190), "INTU": ("Software", 180),
        "ACN": ("IT Services", 210), "DELL": ("Computer Hardware", 90), "APH": ("Electronics", 90),
        "PLTR": ("Software", 130),
    },
    "Communication Services": {
        "GOOGL": ("Internet Content", 2100), "GOOG": ("Internet Content", 2100),
        "META": ("Internet Content", 1500), "NFLX": ("Entertainment", 350),
        "DIS": ("Entertainment", 200), "TMUS": ("Telecom", 260), "VZ": ("Telecom", 170),
        "T": ("Telecom", 160), "CMCSA": ("Entertainment", 150),
    },
    "Consumer Cyclical": {
        "AMZN": ("Internet Retail", 2000), "TSLA": ("Automobiles", 800),
        "HD": ("Home Improvement", 380), "MCD": ("Restaurants", 210), "LOW": ("Home Improvement", 150),
        "BKNG": ("Travel", 160), "TJX": ("Apparel Retail", 130), "NKE": ("Apparel", 120),
        "SBUX": ("Restaurants", 110), "ABNB": ("Travel", 90),
    },
    "Financial Services": {
        "JPM": ("Banks", 620), "BAC": ("Banks", 320), "WFC": ("Banks", 240),
        "V": ("Credit Services", 560), "MA": ("Credit Services", 440), "MS": ("Capital Markets", 180),
        "GS": ("Capital Markets", 160), "BRK.B": ("Insurance", 900), "SPGI": ("Capital Markets", 150),
        "AXP": ("Credit Services", 190), "C": ("Banks", 130), "SCHW": ("Capital Markets", 130),
        "BLK": ("Asset Management", 150), "CB": ("Insurance", 110), "PGR": ("Insurance", 150),
    },
    "Healthcare": {
        "LLY": ("Drug Manufacturers", 800), "JNJ": ("Drug Manufacturers", 380),
        "UNH": ("Healthcare Plans", 500), "ABBV": ("Drug Manufacturers", 330),
        "MRK": ("Drug Manufacturers", 250), "TMO": ("Diagnostics", 210), "ABT": ("Medical Devices", 200),
        "AMGN": ("Drug Manufacturers", 160), "DHR": ("Diagnostics", 190), "PFE": ("Drug Manufacturers", 150),
        "ISRG": ("Medical Devices", 160), "BSX": ("Medical Devices", 120), "SYK": ("Medical Devices", 130),
        "VRTX": ("Biotechnology", 120), "GILD": ("Drug Manufacturers", 110), "CI": ("Healthcare Plans", 90),
        "MDT": ("Medical Devices", 110),
    },
    "Consumer Defensive": {
        "WMT": ("Discount Stores", 600), "COST": ("Discount Stores", 400), "PG": ("Household Products", 380),
        "KO": ("Beverages", 280), "PEP": ("Beverages", 230), "PM": ("Tobacco", 180),
        "MO": ("Tobacco", 100), "MDLZ": ("Food", 95), "CL": ("Household Products", 75),
    },
    "Industrials": {
        "GE": ("Aerospace & Defense", 210), "CAT": ("Machinery", 180), "RTX": ("Aerospace & Defense", 170),
        "HON": ("Industrial Machinery", 140), "UNP": ("Railroads", 140), "BA": ("Aerospace & Defense", 130),
        "LMT": ("Aerospace & Defense", 110), "DE": ("Machinery", 120), "ETN": ("Industrial Machinery", 130),
        "UPS": ("Freight & Logistics", 110), "ADP": ("Staffing", 110),
    },
    "Energy": {
        "XOM": ("Oil & Gas", 480), "CVX": ("Oil & Gas", 290), "COP": ("Oil & Gas", 130),
        "EOG": ("Oil & Gas", 75), "SLB": ("Oil & Gas Equipment", 65), "WMB": ("Oil & Gas Midstream", 60),
    },
    "Utilities": {
        "NEE": ("Utilities - Regulated", 160), "SO": ("Utilities - Regulated", 90),
        "DUK": ("Utilities - Regulated", 85), "GEV": ("Utilities - Renewable", 90),
    },
    "Consumer Electronics": {},  # placeholder to keep sector ordering stable
    "Basic Materials": {
        "LIN": ("Chemicals", 220), "SHW": ("Chemicals", 90), "APD": ("Chemicals", 65),
    },
    "Real Estate": {
        "PLD": ("REIT", 110), "AMT": ("REIT", 90), "EQIX": ("REIT", 85), "WELL": ("REIT", 80),
    },
}


def flat_constituents() -> list[dict[str, object]]:
    """[{ticker, sector, industry, market_cap}] for every curated name."""
    out: list[dict[str, object]] = []
    for sector, names in SP500.items():
        for ticker, (industry, mcap) in names.items():
            out.append({"ticker": ticker, "sector": sector, "industry": industry, "market_cap": mcap * 1000.0})
    return out  # market_cap in $millions to match the /heatmap tiles
