# Third-Party Notices

This project includes code derived from third-party open-source software.

## HKUDS/Vibe-Trading

Source: https://github.com/HKUDS/Vibe-Trading

The following files are derived from HKUDS/Vibe-Trading (originally under
`agent/backtest/`), adapted for this repository (import paths, removal of
China-specific data enrichment, service-friendly error handling):

- `src/backtesting/vibe_engine/models.py` (from `backtest/models.py`)
- `src/backtesting/vibe_engine/metrics.py` (from `backtest/metrics.py`)
- `src/backtesting/vibe_engine/validation.py` (from `backtest/validation.py`)
- `src/backtesting/vibe_engine/run_card.py` (from `backtest/run_card.py`)
- `src/backtesting/vibe_engine/base.py` (from `backtest/engines/base.py`)
- `src/backtesting/vibe_engine/equity.py` (from `backtest/engines/global_equity.py`)

License:

```
MIT License

Copyright (c) 2026 Vibe-Trading Contributors (HKUDS)

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```
