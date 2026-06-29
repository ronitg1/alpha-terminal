# Deploying Alpha Terminal

This is the **Phase 1** deployment guide: get the app live on the internet on an
always-on host. It deploys the current app **as-is** (shared state, no login
yet) — a working checkpoint. Multi-user (accounts, per-user data, background
jobs) is Phases 2–4 in the project plan; this guide will grow as those land.

## Architecture

| Piece | Host | Notes |
| --- | --- | --- |
| Frontend (Vite/React) | **Vercel** | Static build, global CDN. Reads the backend URL from `VITE_API_URL`. |
| Backend (FastAPI) | **Railway** | Always-on server (the long scans/SSE need it — they can't run on serverless). Built from `docker/Dockerfile.web`. |
| Database | **Railway Postgres** | The app uses it when `DATABASE_URL` is set; falls back to local SQLite otherwise. Full file→DB migration is Phase 2. |

> Why not Vercel for the backend? The scans/backtests stream for minutes
> (90–600s+), past any serverless timeout, and the app keeps state in memory /
> on disk. It needs a persistent process. Railway/Render/Fly all fit.

## 1. Deploy the backend to Railway

1. Create a Railway account → **New Project → Deploy from GitHub repo** → pick
   `ronitg1/alpha-terminal`. Railway reads [`railway.toml`](railway.toml) and
   builds [`docker/Dockerfile.web`](docker/Dockerfile.web) automatically.
2. **Add a database:** in the project, **New → Database → PostgreSQL**. Railway
   injects `DATABASE_URL` into the backend service automatically.
3. **Set environment variables** on the backend service (Variables tab) — see
   the table below. At minimum: `MASSIVE_API_KEY`, `DEEPSEEK_API_KEY`,
   `DATA_PROVIDER=massive`, and `ALLOWED_ORIGINS` (you'll fill the Vercel URL in
   step 3).
4. Deploy. Railway gives the service a public URL like
   `https://alpha-terminal-api.up.railway.app`. Confirm it's up:
   open `<that-url>/patterns/patterns` — you should see the pattern list JSON.

## 2. Deploy the frontend to Vercel

1. Create a Vercel account → **Add New → Project** → import
   `ronitg1/alpha-terminal`.
2. **Set the Root Directory to `app/frontend`** (Vercel detects Vite from
   there; build settings come from [`app/frontend/vercel.json`](app/frontend/vercel.json)).
3. Add an environment variable: `VITE_API_URL` = the Railway backend URL from
   step 1 (e.g. `https://alpha-terminal-api.up.railway.app`, no trailing slash).
4. Deploy. Vercel gives a URL like `https://alpha-terminal.vercel.app`.

## 3. Connect the two (CORS)

1. Back in Railway, set `ALLOWED_ORIGINS` to your Vercel URL(s), comma-separated
   — e.g. `https://alpha-terminal.vercel.app`. Add a custom domain here too if
   you attach one in Vercel.
2. Redeploy the backend. The frontend can now call it without CORS errors.

Open the Vercel URL → the dashboard loads and a scan runs against the Railway
backend. Done with Phase 1.

## Environment variables

### Backend (Railway)

| Var | Required | Purpose |
| --- | --- | --- |
| `MASSIVE_API_KEY` | yes* | Polygon/Massive market data. |
| `MASSIVE_BASE_URL` | no | Defaults to `https://api.polygon.io`. |
| `DATA_PROVIDER` | recommended | `massive` (default path) or `fds`. |
| `DEEPSEEK_API_KEY` | yes | LLM agents, theses, chat. |
| `FINNHUB_API_KEY` | optional | Market News + insider/ratio fallback. |
| `FINANCIAL_DATASETS_API_KEY` | only if `DATA_PROVIDER=fds` | Alt market data. |
| `ALLOWED_ORIGINS` | yes (prod) | Comma-separated frontend origins. Defaults to localhost for dev. |
| `API_KEY_ENCRYPTION_KEY` | yes (when `AUTH_ENABLED`) | Fernet key encrypting users' stored BYOK keys at rest. Generate with `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`. Comma-separate multiple keys to rotate (first encrypts, all decrypt). **Set this in the env BEFORE the deploy that turns auth on** — the backend refuses to boot with `AUTH_ENABLED` on and this unset. |
| `OWNER_USER_ID` | no | The owner's Clerk user id (`sub`). On first login this account claims the pre-auth `default` data. Unspoofable — preferred. Bootstrap: enable auth, log in once, read the id from the logs / `users` table, set this var. |
| `OWNER_EMAIL` | no | Alternative to `OWNER_USER_ID`: the account whose **verified** email matches claims the `default` data. Requires the Clerk JWT template to emit `email` + `email_verified`; an unverified email never claims. |
| `DATABASE_URL` | auto | Set by Railway Postgres. Unset → local SQLite. |
| `SKIP_OLLAMA_CHECK` | recommended | Set to `1` on the server — skips the local-model probe so startup is fast. |
| `LOG_LEVEL` | no | Logging verbosity. |
| `PORT` | auto | Set by Railway; the server binds to it. |

**Database migrations run automatically** on each deploy: `railway.toml` has a
`preDeployCommand` that runs `alembic upgrade head` before the new release goes
live, so the Postgres schema is always current. (Locally with SQLite the app
still auto-creates tables, no Alembic needed.) Health checks hit `/health` (a
trivial no-dependency route).

\* Either `MASSIVE_API_KEY` or `FINANCIAL_DATASETS_API_KEY` must be set.

### Frontend (Vercel)

| Var | Required | Purpose |
| --- | --- | --- |
| `VITE_API_URL` | yes | Backend base URL (the Railway URL). No trailing slash. |

## Test the backend image locally (optional)

```bash
docker build -f docker/Dockerfile.web -t alpha-terminal-api .
docker run -p 8000:8000 --env-file .env alpha-terminal-api
# then: curl http://localhost:8000/patterns/patterns
```

## Known limitations of Phase 1 (addressed in later phases)

- **Shared state / no login.** Everyone who visits sees and edits the same
  portfolio, watchlists, and P&L. Phase 3 adds Clerk auth + per-user isolation.
- **State still partly file-based.** Some stores (portfolio config, watchlists,
  P&L, theses) write local files that won't persist across Railway redeploys
  until Phase 2 moves them to Postgres. A Railway **volume** can bridge this in
  the interim if you need persistence before Phase 2.
- **Long scans run in-request.** They work (Railway has no hard request
  timeout), but a big scan ties up a worker. Phase 4 moves them to a background
  job queue.
