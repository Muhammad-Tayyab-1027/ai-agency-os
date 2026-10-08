# Agency OS

An AI agency operating system. You create one agent, the **Manager**. The Manager turns your
revenue goal into a strategy, proposes the specialist agents it needs (with your approval),
delegates and reviews their work, and talks to you in a chat. It also writes your daily reports
in its own voice.

Risky actions wait for you. Payments, contracts, account creation and anything like that are
never automatic.

> **Status: Stage 1 (MVP core) is done.** It includes the Manager, the agent registry, the database,
> the approval system, the audit log, the dashboard, and Lead Research and Sales agents created by
> the Manager. The Lead Research and Sales agents can't do real research or send email yet. Those
> connectors arrive in Phases 2 and 3 (see [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)).

## Quick start (local)

Requirements: Python 3.12+, [uv](https://docs.astral.sh/uv/), Node 22+, PostgreSQL 16.

```bash
# 1. Database (the app role must not be a superuser, or client isolation is skipped)
sudo -u postgres psql -f docker/postgres-init.sql

# 2. Backend
cp .env.example backend/.env          # then edit SECRET_KEY (and ANTHROPIC_API_KEY when ready)
cd backend
uv sync
uv run alembic upgrade head
uv run python -m app.cli setup --email you@example.com   # prompts for a password
uv run uvicorn app.main:app --port 8000                  # API
uv run python -m app.cli worker                          # in a second terminal: agents run here

# 3. Dashboard
cd ../frontend
npm install
npm run dev                                              # http://localhost:3000
```

Or with Docker: `cp .env.example .env`, edit it, then run `docker compose up --build`. After that,
run `docker compose exec api python -m app.cli setup --email you@example.com`.

### Offline mode vs real agents

With `LLM_PROVIDER=mock` (the default), everything runs end to end without any keys. Agents say
clearly that they did no real work. To switch to real agents:

1. Create an API key at https://console.anthropic.com → **API keys**.
2. Put it in `.env` as `ANTHROPIC_API_KEY=...` and set `LLM_PROVIDER=anthropic`.
3. Restart the API and the worker.

## Using it

1. **Goals**: set a revenue goal, e.g. "$5,000/month, websites first". The Manager starts planning.
2. **Approvals**: approve, edit or reject what the Manager asks for, starting with new agents.
3. **Manager chat**: ask questions, give instructions, discuss reports.
4. **Your to-dos**: steps only you can do (sign-ups, API keys, verification, closing deals),
   with exact instructions.
5. **Settings**: limits, the daily AI budget and the **kill switch** that pauses everything.

## Tests

```bash
cd backend
createdb -O agency agency_test    # once
uv run pytest
```

There are 32 tests. They cover the policy engine, approvals, the full goal → plan → agents → work → review →
report cycle, retries and honest failure, budgets, client isolation (RLS), the append-only audit
log and the API.
