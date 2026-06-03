# Alpha Terminal — web app

This directory holds the dashboard that ships with Alpha Terminal:

- **`backend/`** — FastAPI app (`app.backend.main:app`). Serves the sleeves, screener, backtest, pattern, news, transcript, and chat endpoints.
- **`frontend/`** — React + Vite + Shadcn/UI single-page dashboard (Market / Screening / Portfolio / News / Calls).

## Setup and run

There is **one** source of truth for setup, API keys, and run commands: the
[**root README**](../README.md#quick-start-5-minutes). Follow it — it covers the
correct providers (DeepSeek + Polygon/Massive, optional Finnhub), the Python
3.12 + Poetry toolchain, and the `.env` file.

Once dependencies are installed (per the root README), start both servers from
the **repository root** (not this directory):

```bash
# Terminal 1 — backend (hot reload)
poetry run uvicorn app.backend.main:app --host 127.0.0.1 --port 8000 --reload

# Terminal 2 — frontend dev server
cd app/frontend && npm run dev
```

Then open http://localhost:5173. The backend serves its OpenAPI docs at
http://localhost:8000/docs.

### One-line helper scripts

`run.sh` (macOS/Linux) and `run.bat` (Windows) check prerequisites, install
dependencies, and launch both servers. Run them from inside `app/`:

```bash
cd app && ./run.sh      # or: run.bat on Windows
```
