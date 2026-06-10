# Fidelity account integration — plan

Goal: get your real Fidelity positions and fills into Alpha Terminal's P&L
tracker, so paper ideas and actual trades live side by side.

This document explains the approach in three parts: what we explicitly
rejected and why, what ships now (Phase 1, CSV import), and the auto-sync
upgrade path (Phase 2, SnapTrade via Fidelity Access/Akoya).

---

## Rejected: stored credentials + automated login

The obvious-seeming approach — keep your Fidelity username/password in a
secrets file and have a headless browser log in and scrape positions — is
rejected, deliberately:

1. **Terms of service.** Fidelity's Electronic Services agreement prohibits
   automated access with stored credentials. Accounts flagged for bot logins
   can be locked, and unlocking a brokerage account is a phone-tree ordeal.
2. **It breaks constantly.** Fidelity runs MFA challenges and bot detection
   (device fingerprinting, behavioral signals) precisely to stop this. Every
   layout change or challenge variant breaks the scraper silently — usually
   the day you most want fresh numbers.
3. **Security.** A plaintext (or even encrypted-at-rest) credential file for
   a brokerage account is the single most valuable artifact on the machine.
   A read-only OAuth grant can be revoked from Fidelity's side in one click;
   a leaked password cannot.

The legitimate version of "it logs in for you" exists and is Phase 2.

---

## Phase 1 — CSV import (shipped with the P&L tracker)

Fidelity exports clean CSVs today, no API required:

- **Positions**: Accounts & Trade → Portfolio → Positions → the download icon
  (`Portfolio_Positions_MMM-DD-YYYY.csv`). One row per holding, including
  options with symbols like `-AAPL250620C200`.
- **Transactions/fills**: Accounts & Trade → Activity & Orders → Download.
  History up to 5 years, one row per execution.

Alpha Terminal's importer (`POST /pnl/import/fidelity`) accepts either file:

- Parses stock rows and option rows (OCC-style and Fidelity's
  `-TICKERyymmdd[C|P]strike` compact format).
- Maps each to a P&L position tagged `source: "fidelity"`, `real: true`.
- Dedupes on (symbol, open date, quantity) so re-importing the same file is
  idempotent.
- Never persists the raw CSV; parsed positions land in `app/data/` (which is
  gitignored — account data can never be committed).

Workflow: export from Fidelity → drag the file onto the P&L tab → review the
parsed rows → confirm import. Refresh whenever you want updated holdings
(marks update automatically from market data regardless; the import is only
needed when your *positions* change).

---

## Phase 2 — auto-sync via SnapTrade (Fidelity Access / Akoya)

Fidelity officially supports third-party **read-only** data access through
its **Fidelity Access** program, built on the **Akoya** data-access network
(Fidelity is a co-founder). The flow is OAuth-style: you authenticate on
*Fidelity's own page*, grant data permissions, and the third party receives
tokens — never your credentials.

[SnapTrade](https://snaptrade.com/) is a brokerage-aggregation API that rides
this rail and supports Fidelity read-only connections. Plaid Investments is
the alternative; SnapTrade's free developer tier and trading-data focus make
it the better fit here.

### Integration shape

1. **You** create a SnapTrade developer account → get `clientId` +
   `consumerKey` → drop them in `.env` as `SNAPTRADE_CLIENT_ID` /
   `SNAPTRADE_CONSUMER_KEY` (already in `.gitignore`'s protected set).
2. Backend registers a SnapTrade *user* for you (one-time), then generates a
   **connection portal URL**.
3. The P&L tab shows a "Connect Fidelity" button → opens that portal → you
   log in on Fidelity's hosted page, approve read-only access → done. No
   credentials touch this repo or machine.
4. A new `app/backend/services/snaptrade_service.py` polls (or on-demand
   fetches) `accounts`, `positions`, and `activities`, and upserts them into
   the same `pnl_service` store with `source: "fidelity"` — identical shape
   to the CSV path, so the UI doesn't care which rail the data came from.
5. Token refresh is handled by the SDK; revocation is done on Fidelity's
   side (Security Center → Connected apps) or SnapTrade's dashboard.

### Cost and limits

- SnapTrade developer tier: free for personal use at this scale (verify
  current terms at signup — pricing applies to commercial multi-user apps).
- Sync cadence: positions/balances are not realtime ticks; expect
  on-connect + periodic refresh. Fine for P&L (marks come from Massive
  anyway — the brokerage link only needs to deliver *what you hold*).

### Verification checklist before building Phase 2

- [ ] SnapTrade signup; confirm Fidelity appears in their connection portal
      and is enabled for new developer accounts (connector availability
      changes; verify before writing code).
- [ ] Confirm activities (fills) granularity covers options with
      strike/expiry detail, not just net amounts.
- [ ] Decide sync trigger: manual "Refresh from Fidelity" button (simplest,
      recommended) vs. background poll.

### Effort estimate

Roughly a day of work once the SnapTrade account exists: service module +
two routes (`/pnl/connect`, `/pnl/sync`) + a connect button and sync status
chip in the P&L tab. The CSV importer's mapping layer is reused as-is.

---

## Security model summary

| | Credentials stored locally | Revocable | ToS-clean | Survives MFA |
|---|---|---|---|---|
| Cred file + headless login | **Yes (bad)** | No | **No** | **No** |
| CSV export/import | No | n/a | Yes | Yes |
| SnapTrade (Akoya OAuth) | No (tokens only) | Yes, one click | Yes | Yes |

Execution stays out of scope permanently: Alpha Terminal is signals-only and
the brokerage link is **read-only by design** — the OAuth grant requested is
data access, not trading authority.
