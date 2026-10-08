# Architecture

## Components

```
Dashboard (Next.js) ──/api proxy──▶ FastAPI ──▶ Postgres (RLS, audit log, task queue)
                                                   ▲
Worker processes ── claim tasks (SKIP LOCKED) ─────┘
   └─ Agent runtime ─▶ Claude API (tool use) ─▶ Policy engine ─▶ tools / approval queue / human to-dos
```

| Path | What it is |
|---|---|
| `backend/app/agents/templates/*.yaml` | Role templates: purpose, tools, scopes, success criteria, prompt |
| `backend/app/agents/registry.py` | Agent creation (agent cap), metrics, performance-based allocation |
| `backend/app/agents/runner.py` | The LLM tool-use loop for one agent on one task |
| `backend/app/policy/engine.py` | The one gate every tool call goes through |
| `backend/app/tools/` | Tools, each with a typed input, a risk tier and the scopes it needs |
| `backend/app/orchestrator/` | Task lifecycle, approvals, reports, goals/chat services |
| `backend/app/workers/worker.py` | Task claiming plus periodic jobs (check-ins, daily report, expiry, stuck tasks) |
| `backend/app/llm/` | Claude gateway (+ offline mock) |
| `backend/alembic/versions/` | Schema, RLS policies, audit triggers, seed services |

## Agent hierarchy

The owner creates only the **Manager**, using `app.cli setup`. The Manager proposes every other
agent with `propose_agent` (Level B, needs approval). It can only propose agents from a template,
and the template sets the most tools and scopes that role can ever hold. The number of agents is
capped by `max_agents`, and pending proposals count toward the cap.

Templates in this stage: `manager`, `lead_research`, `sales`, `project_manager`, `website`,
`content`, `design`, `social`, `support`, `fiverr`. Each template lists the tools it has now and the
`planned_tools` arriving in later phases.

## Message format

Every delegation, result, review and event is stored in `agent_messages` using one envelope:

```json
{ "id": "...", "correlation_id": "<task id>", "type": "task|response|review|event",
  "from_agent": "manager", "to_agent": "lead_research", "task_id": "...",
  "client_id": null, "project_id": null, "payload": { ... }, "schema_version": 1 }
```

`submit_result` (the only way a run finishes) is validated as:
`{status: success|partial|failed|needs_human, summary, output, evidence[], confidence}`.

## Task lifecycle

```
queued → running → in_review → completed            (the Manager reviews specialist work)
                             ↘ queued (revision, with feedback; at most N revisions) → failed
                 → blocked   (waiting on you / missing agent role / failed dependency)
                 → queued    (error: retry with backoff) → failed after max attempts
```

There are four task kinds:

- `work`: specialist work, reviewed by the Manager.
- `plan`: turns a goal into a strategy, agents and tasks.
- `review`: the Manager checking a specialist's result.
- `chat` / `checkin` / `report`: the Manager talking to you or reacting to events.

The system never marks a task done without a validated result. LLM errors, refusals, running out
of turns and "finished without submitting" all count as failed attempts and are logged.

## Autonomy levels

| Level | Enforcement | Examples now |
|---|---|---|
| A, automatic | runs immediately, audited | research, KB, leads, tasks, reviews, chat |
| B, approval | queued in `approvals` with a SHA-256 snapshot; approval runs exactly that snapshot (or your edit, re-validated) | new agents (later: outreach, proposals, publishing, spend, integrations) |
| C, never | the policy engine refuses; the agent must use `request_human_action` | payments, contracts, account creation, CAPTCHA/verification |

Agent `autonomy_overrides` can only make a tool stricter, never looser.

## Security

- **Secrets** live only in environment variables. Audit data is redacted, and the dashboard never
  asks you to paste keys.
- **Client isolation**: Postgres Row-Level Security (with `FORCE`) on every client-scoped table.
  Each DB transaction carries its scope, so a specialist working on client A can't read or write
  client B's rows, even if there's a bug in the code. Only the Manager (`clients:all`) and dashboard
  users see across clients.
- **Least privilege**: each agent holds only its template's tools and scopes. Every call is checked
  again at execution time, including approved ones.
- **Audit log**: append-only (a DB trigger blocks UPDATE/DELETE/TRUNCATE) and hash-chained. You can
  verify it from Logs.
- **Rate limits**: per agent and per tool each day, login throttling, daily AI budgets (agency-wide
  and per agent) and a global kill switch.
- **Input validation**: Pydantic on every API body and every tool input. Unknown fields are rejected.
- **Web**: an httpOnly SameSite=Strict session cookie, a CSRF header on every change, security
  headers, and argon2 password hashing.
- **Prompt injection**: outside content is labelled as data in every prompt. More importantly,
  permissions are enforced by code, so a manipulated model still can't skip an approval.

## Phases

1. **Done**: Foundation and MVP core. This is what this document describes.
2. **Lead Research connectors**: Google Places, a web search API, PageSpeed Insights and a page
   fetcher (with an SSRF guard). Lead quality scoring.
3. **Sales**: email sending and receiving, outreach drafting (Level B), the compliance engine
   (suppression, daily caps, opt-out links, per-country rules), reply classification, follow-ups
   and qualified-lead hand-off.
4. **Delivery agents**: Project Manager, Website (generate, preview, publish after approval),
   Content, Design and Social.
5. **Growth**: Support, Fiverr assistant, proposals and quotes, read-only Stripe revenue sync, and
   shifting effort toward what earns.
6. **Hardening**: agent evals, observability, backups and a production deploy.
