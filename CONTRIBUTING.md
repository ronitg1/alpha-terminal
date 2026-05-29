# Contributing

Pull requests welcome. This file is short on purpose — read [ARCHITECTURE.md](ARCHITECTURE.md) for the deep dive.

## Before you start

1. **Open an issue first** for any non-trivial change. Saves you a wasted PR if the change conflicts with planned work.
2. **One concern per PR.** A PR that touches the dashboard *and* the backtest engine *and* the agent prompts is too big to review.
3. **Tests stay green.** `poetry run pytest tests/` + `npx tsc --noEmit` from the frontend must both pass.

## Local setup

See the [Quick start in README.md](README.md#quick-start-5-minutes). You need Python 3.12 + Poetry + Node 18+ + a `.env` with at minimum `DEEPSEEK_API_KEY` and `MASSIVE_API_KEY`.

## Coding conventions

Both languages have a few non-negotiable rules. New code that violates these creates drift.

### Python

- `from __future__ import annotations` at the top of every module.
- Type hints on every public function.
- Docstrings on every public function and module. Modules explain the **why**; functions cover non-obvious behavior.
- No silently swallowed exceptions. Either re-raise, log with `logger.warning` / `logger.exception`, or convert to a domain error (see `MassiveError`).
- Retries are explicit, not hidden. Exponential backoff with jitter for external APIs. See [`tools/massive/client.py`](src/tools/massive/client.py).
- Validate at import time. Config files call their validators at module load so a bad edit fails loudly instead of two hours into a scan.
- No emojis in code or docs (the project doesn't use them).

### TypeScript

- `tsc --noEmit` must pass. No `@ts-ignore`, no `@ts-expect-error`. If a type is fighting you, the fix is to model the data more precisely, not silence the compiler.
- Components live in `app/frontend/src/components/`. Each major feature gets its own subfolder.
- Hooks live in `app/frontend/src/components/<feature>/hooks/` (close to the feature that uses them) OR `app/frontend/src/hooks/` (cross-cutting).
- No new chart library. Inline SVG only — see [`mini-spark.tsx`](app/frontend/src/components/sleeves/mini-spark.tsx).
- `useEffect` dependencies must be correct. If you add a value to the closure, add it to the deps.

### Commit messages

```
type(scope): short summary, 50 chars max

Longer explanation of *why*. Wrap at 72 chars. Skip if obvious.

Closes #123
```

Types: `feat`, `fix`, `refactor`, `docs`, `test`, `chore`. Scope is the feature area: `sleeves`, `options`, `backtest`, `agents`, `api`, `frontend`.

## Adding a new agent

See [ARCHITECTURE.md → Adding a new agent](ARCHITECTURE.md#adding-a-new-agent).

## Adding a new options strategy

1. Edit the `_STRATEGY_REGISTRY` dict in [`app/backend/routes/sleeves.py`](app/backend/routes/sleeves.py). Add an entry with `key`, `label`, `description`, `scorer` (a callable taking `(bars, qqq_bars)` and returning `{signals, conviction, recommendation}`).
2. The scorer must return three "signals" each with `{label, value_text, fired, tooltip}` for the dashboard chips.
3. The recommendation block carries `{direction, strike_offset_pct, expiry_days, reason}`.
4. The frontend picks up new strategies automatically via `GET /sleeves/options/strategies`.

## Adding a new backend route

1. Add it to [`app/backend/routes/sleeves.py`](app/backend/routes/sleeves.py) (or a new routes file if the surface area justifies one).
2. Wrap long operations in SSE — see `/sleeves/scan/run` for the pattern.
3. Add a client method in [`app/frontend/src/services/sleeves-api.ts`](app/frontend/src/services/sleeves-api.ts).
4. If the route mutates state, add `await refresh()` in the calling component so the dashboard reflects the change.

## Reviewing PRs

If you review someone else's PR, check:

- [ ] Tests pass locally (`poetry run pytest tests/` + `npx tsc --noEmit`)
- [ ] Browser smoke-test on the affected tab
- [ ] No secrets in the diff (search for `key=`, `token=`, `api_key`)
- [ ] No `console.log` in `app/frontend/src/components/sleeves/` or `app/frontend/src/components/stocks/`
- [ ] No `print()` in `src/` or `app/backend/` (use `logger`)
- [ ] Conventions enforced (see above)

## Communication

- **Bugs**: [GitHub issues](https://github.com/ronitg1/alpha-terminal/issues), tagged `bug`. Include repro steps and what you expected vs. what happened.
- **Feature ideas**: Issues tagged `enhancement`. Sketch what you'd build before writing code.
- **Questions**: Discussions tab (once enabled), or open an issue tagged `question`.

## License

By contributing you agree your contributions are licensed under the project's [MIT license](LICENSE).
