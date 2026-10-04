# Personal Intelligence Agent

**An AI assistant that learns who you are over time, and backs every claim it makes about you with evidence.**

![Python](https://img.shields.io/badge/Python-3.12-3776AB?logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-async-009688?logo=fastapi&logoColor=white)
![LangGraph](https://img.shields.io/badge/LangGraph-stateful%20agents-1C3C3C)
![PostgreSQL](https://img.shields.io/badge/PostgreSQL%2017-pgvector-4169E1?logo=postgresql&logoColor=white)
![Redis](https://img.shields.io/badge/Redis-rate%20limiting-DC382D?logo=redis&logoColor=white)
![Flutter](https://img.shields.io/badge/Flutter-Android%20%7C%20iOS-02569B?logo=flutter&logoColor=white)
![Tests](https://img.shields.io/badge/tests-850-brightgreen)

Most chatbots forget you when the conversation ends, or "remember" by pasting
old messages back into the prompt. This system works more like a careful human
assistant:

- It **interviews** you.
- It **builds a structured profile** of your goals, constraints and preferences.
- It **decides what is worth remembering**.
- It **looks up** the right facts when you ask something.
- It **checks its own answer** before you see it.

Questions with exact answers are computed from your records in SQL, never
guessed by the model.

The repository has two parts: a production-style **FastAPI backend** (`app/`)
and a **Flutter mobile app** for Android and iOS (`mobile/`) built on the same
public REST and SSE API.

---

## At a glance

| | |
|---|---|
| **Scope** | 17 planned phases delivered: auth to agents, retrieval, evaluation and hardening |
| **Tests** | 850 automated tests across unit, LangGraph workflow and integration suites |
| **Code** | ~14k lines of application code, ~11.5k lines of tests, 13 database migrations |
| **Clients** | Flutter app for Android and iOS, with live streaming and calendar export |
| **Quality gates** | `pytest`, `ruff`, strict `mypy`, Alembic drift check, retrieval-quality regression gate |

---

## What it does

- **Adaptive onboarding.** A LangGraph interview chooses each next question
  from what it still doesn't know. It scores its own confidence and stops when
  the profile is good enough, not after a fixed script.
- **Long-term memory with a lifecycle.** The system decides which statements
  are worth keeping. It handles duplicates and contradictions ("I moved to
  Pune" replaces "I live in Mumbai") and keeps the history. Users can review,
  correct, archive or delete anything it remembers.
- **Grounded answers.** Every reply is checked for claims about the user that
  its evidence doesn't support. If any are found, the reply is regenerated once
  with those claims named, and the response says whether it is `grounded`.
- **Exact answers for exact questions.** "How many hours did I study last
  week?" is answered with a SQL total in the user's own time zone. The model
  never estimates the figure.
- **Analysis and weekly plans.** The profile analysis labels each point as a
  fact, observation, inference, recommendation or uncertainty, and cites its
  evidence. Weekly plans are checked by deterministic rules before they reach
  the user's calendar.
- **Live progress.** Server-sent events report each step of a turn as it
  happens. The reply is released only after it has been verified and stored.
- **Subscriptions.** Free and Pro plans with monthly allowances on every
  feature that calls a model. A unit is reserved atomically before the work and
  refunded if the request fails.

---

## How a chat turn works

```mermaid
flowchart LR
    Q[understand_query] --> R[retrieve_context]
    R -->|memories found| G[grade_evidence]
    R -->|nothing to grade| C[build_context]
    G -->|insufficient| RR[retry_retrieval<br/>rewritten query]
    RR --> G
    G -->|sufficient| C
    C --> A[generate_reply]
    A --> V[verify_answer]
    V -->|unsupported claims,<br/>budget left| A
    V -->|grounded| E((reply))
```

1. **Plan the retrieval.** The query is turned into a plan: semantic memories,
   a timeline, or exact SQL totals.
2. **Retrieve and rank.** Vector search (pgvector) and keyword search run
   together. Their results are fused by rank and reranked.
3. **Grade and retry.** A model grades whether the evidence can answer the
   question. If it can't, memory is searched again with a rewritten query.
4. **Answer and verify.** The reply is generated from the evidence, then
   checked against it.

Every loop has a fixed attempt budget, so a turn always ends. Every step is
stored as a trace, with its timing, model calls and token usage, and the trace
can be read back through `/api/v1/runs/{id}/trace`.

---

## Engineering highlights

Each fix below started from a measurement or a reproduction, and each is pinned
by a test that fails if the fix is undone.

| Problem found | Before | After |
|---|---|---|
| A chat turn held a database connection during every model call | 15 of 45 concurrent chats failed | 45 of 45 succeed; median idle connections held: 0 |
| Password hashing ran on the event loop | 477 ms freeze under 8 concurrent logins | 63 ms worst case (dedicated thread pool) |
| Oversized request bodies were read in full before validation | ~157 MB allocated for a 50 MB body | Rejected as it arrives, ~2 MB peak |
| Two messages sent at once to one conversation | `[201, 500]` on a unique constraint | Stored in order under a short row lock |

Other design choices:

- **Retrieval that degrades gracefully.** Hybrid search keeps working when the
  embedding provider is down. In the regression gate, fusion and reranking
  reach nDCG@6 **0.716**, against 0.440 for vector-only and 0.665 for
  keyword-only. These figures use deterministic test embeddings; see
  [Evaluation](#evaluation).
- **Exactly-once background work.** Each memory-extraction job is written in
  the same transaction as the exchange it belongs to. A separate worker runs it
  with leases, bounded retries and backoff.
- **Security.** Argon2id password hashing. Login timing doesn't reveal which
  emails are registered. Refresh tokens rotate, and reusing an old one revokes
  the whole token family. Every query is scoped to its owner. Retrieved memories
  are fenced off as untrusted data, never treated as instructions.
- **Cost control.** Redis sliding-window rate limits stop bursts. Plan
  allowances in PostgreSQL bound what each account costs per month.
- **Clean architecture.** Thin API routes, application services that own
  transactions, framework-free domain rules, and infrastructure behind clear
  boundaries. LangChain types do not leak past the infrastructure layer.
- **Evaluation as part of development.** A versioned dataset of personas and
  labelled questions. Recall@K, MRR and nDCG for each retrieval strategy.
  End-to-end answer checks with an optional model judge. Baseline-to-candidate
  comparison.

---

## Tech stack

| Layer | Technology |
|---|---|
| API | FastAPI, Pydantic v2, server-sent events, JWT access tokens and rotating refresh tokens |
| Agents | LangGraph workflows for onboarding, conversation, memory and analysis; LangChain model adapters |
| Data | PostgreSQL 17, SQLAlchemy 2 (async), asyncpg, Alembic, pgvector, full-text search |
| Infrastructure | Redis (rate limiting), a PostgreSQL-backed job queue and worker, structured JSON logs with correlation ids |
| Models | OpenAI chat and embeddings behind a provider interface, with bounded retries and timeouts |
| Mobile | Flutter (Android and iOS), secure token storage, SSE streaming, calendar export |
| Tooling | uv, pytest, ruff, mypy (strict), Docker Compose |

---

## Quick start

```bash
uv sync
cp .env.example .env
docker compose up -d
uv run alembic upgrade head
uv run uvicorn app.main:app --reload
```

The interactive API docs are then at <http://localhost:8000/docs>. Full
instructions are in [Setup](#setup) and [Run](#run).

## Project documents

| Document | Purpose |
|---|---|
| [PRD.md](PRD.md) | What the product does and why |
| [ENGINEERING_GUIDELINES.md](ENGINEERING_GUIDELINES.md) | How the system is engineered |
| [mobile/README.md](mobile/README.md) | The Flutter client |
| [CLAUDE.md](CLAUDE.md) | Working rules for AI-assisted development |

> **Testing approach.** All 850 tests run against a deterministic fake model,
> so the suite is fast, free and repeatable, and fails only for reasons inside
> this repository. The OpenAI adapter, including its failure paths, is tested
> the same way. Measuring quality against the live model is the next step; see
> [Verifying against a live model](#verifying-against-a-live-model) and
> [Known gaps](#known-gaps).

<details>
<summary><b>Full feature list</b></summary>

- configuration via environment variables with production safety checks
- structured (JSON) logging with per-request correlation ids
- a consistent response envelope and error contract
- async PostgreSQL engine, connection pool and request-scoped sessions
- Alembic migrations with pgvector enabled
- liveness and readiness endpoints, with readiness checking the database
- registration, login, refresh-token rotation with reuse detection, logout
- the structured user profile (goals, constraints, preferences) behind
  per-user authorization
- adaptive onboarding: a LangGraph interview that decides what to ask next,
  scores its own confidence, and writes the resulting profile
- initial analysis: evidence-grounded claims with declared claim types, verified
  against the evidence they cite and retried when they fail
- rate limiting on authentication and on every endpoint that calls a model
- conversations: persisted exchanges with bounded context, ownership checks and
  paginated reads
- memory: selective extraction from conversations, with deduplication,
  contradiction handling, lifecycle history and user control
- semantic retrieval: memories are embedded with pgvector and recalled by
  meaning, so a conversation can draw on what was said in earlier ones
- temporal and structured retrieval: countable events, timelines, and exact
  totals computed in SQL rather than estimated by a model
- hybrid retrieval: semantic and keyword search fused by rank and reranked, so
  recall survives an embedding outage
- agentic retrieval: recalled memories are graded before they are used, and
  memory is searched again with a rewritten query when they cannot answer the
  question, within a fixed attempt budget
- answer verification: every reply is checked for statements about the user its
  context does not support, regenerated once with those statements named, and
  reported as `grounded` (or not) in the response
- streaming: server-sent progress events while a turn runs, with the reply
  released only once it has passed verification and been stored
- execution tracing: every conversation turn is stored as a run, with per-step
  timings, model calls, token usage, retries and the point of failure, readable
  through `/api/v1/runs`
- background processing: memory extraction is queued in PostgreSQL in the same
  transaction as the exchange, and run by a separate worker with leases,
  bounded retries and exactly-once effects
- evaluation: a versioned dataset of personas and labelled questions, retrieval
  metrics per strategy, end-to-end answer checks with an optional model judge,
  and baseline-to-candidate comparison
- subscription plans: Free and Pro, with monthly allowances on every endpoint
  that calls a model, taken atomically before the work and refunded on failure
- hardening driven by measurement: no connection held across model calls,
  concurrent sends to one conversation, password hashing off the event loop,
  request size limits, retention, and a dependency audit

Response caching was measured and deliberately not added; see
[Caching](#caching).

</details>

---

## Mobile app

A Flutter client for Android and iOS lives in [`mobile/`](mobile/README.md). It
consumes the same REST API and SSE stream as any client would; the backend
holds no mobile-specific logic.

## Requirements

- Python 3.12 (pinned in `.python-version`)
- [uv](https://docs.astral.sh/uv/) for environment and dependency management
- PostgreSQL 17 with the [pgvector](https://github.com/pgvector/pgvector)
  extension — provided by `docker-compose.yml`
- Redis, for rate limiting — also provided by `docker-compose.yml`
- A Docker runtime. Docker Desktop works; on macOS without it,
  `brew install colima docker docker-compose && colima start`

`uv` provisions the interpreter itself, so a matching system Python is not
required.

<details>
<summary>Homebrew's Compose plugin is not found by the Docker CLI by default</summary>

Add the plugin directory to `~/.docker/config.json`:

```json
{ "cliPluginsExtraDirs": ["/opt/homebrew/lib/docker/cli-plugins"] }
```

</details>

---

## Setup

```bash
uv sync
cp .env.example .env
docker compose up -d
uv run alembic upgrade head
```

`uv sync` creates a project-local `.venv/` and installs the locked dependency
set from `uv.lock`. Dependencies are never installed globally.

Compose publishes PostgreSQL on **5433** and Redis on **6380**, both offset from
their defaults so they do not collide with instances already installed on the
host.

## Run

```bash
uv run uvicorn app.main:app --reload   # the API
uv run python -m app.workers           # the background worker, in a second terminal
```

Without a worker running, chat still works but nothing is learned from it:
extraction jobs wait in the queue until a worker picks them up.

For the mobile app on a physical phone over Wi-Fi, bind to every interface so
the phone can reach the API, and use this machine's LAN IP in the app (see
`mobile/README.md`):

```bash
uv run uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

Only do this on a network you trust: it exposes the development API to every
device on it.

| Endpoint                       | Purpose                                          |
| ------------------------------ | ------------------------------------------------ |
| `GET /health`                  | Liveness. Checks no dependencies by design.      |
| `GET /ready`                   | Readiness. Checks the database; `503` on failure.|
| `GET /docs`                    | OpenAPI UI. Disabled in production.              |
| `POST /api/v1/auth/register`   | Create an account. Returns the user, not tokens. |
| `POST /api/v1/auth/login`      | Exchange credentials for a token pair.           |
| `POST /api/v1/auth/refresh`    | Rotate a refresh token.                          |
| `POST /api/v1/auth/logout`     | Revoke a refresh token and its family.           |
| `DELETE /api/v1/auth/account`  | Delete the account and all its data. Needs the password; `403 INCORRECT_PASSWORD` otherwise. |
| `POST /api/v1/onboarding/start`| Begin, or resume, the interview.                 |
| `POST /api/v1/onboarding/message` | Answer the current question.                  |
| `GET /api/v1/onboarding/status`| Progress and confidence breakdown.               |
| `POST /api/v1/analysis`        | Generate an analysis from known evidence.        |
| `GET /api/v1/analysis/latest`  | The most recent analysis, with its evidence.     |
| `POST /api/v1/plans/week`     | Plan the next seven days from your goals and constraints. `409 INSUFFICIENT_CONTEXT` with nothing to plan from, `503 PLAN_UNAVAILABLE` if the model fails. |
| `GET /api/v1/plans/week/latest` | Your most recent week plan. Timed blocks include `starts_at`/`ends_at` as instants in the plan's time zone. |
| `PATCH /api/v1/plans/{id}/blocks/{block_id}` | Mark a block done or not done.   |
| `POST /api/v1/chat`            | Send a message, receive a reply.                 |
| `POST /api/v1/chat/stream`     | The same, as server-sent progress events.        |
| `GET /api/v1/conversations`    | Your conversations, most recent first.           |
| `GET /api/v1/conversations/{id}` | One conversation and a page of its messages.   |
| `DELETE /api/v1/conversations/{id}` | Delete a conversation and its messages. Memories learned from it are kept. |
| `GET /api/v1/memories`         | What is remembered about you.                    |
| `PATCH /api/v1/memories/{id}`  | Correct or archive a memory.                     |
| `DELETE /api/v1/memories/{id}` | Delete a memory permanently.                     |
| `GET /api/v1/runs/{id}`        | Summary of one of your agent runs.               |
| `GET /api/v1/runs/{id}/trace`  | That run step by step: timings, calls, outcome.  |
| `GET /api/v1/subscription`     | Your plan and this month's usage of each metered feature. |
| `GET /api/v1/profile`          | Read the authenticated user's profile.           |
| `PATCH /api/v1/profile`        | Update it. Absent fields are left unchanged.     |

## Database

Migrations are managed with Alembic. The connection string comes from
application settings, never from `alembic.ini`.

```bash
uv run alembic upgrade head            # apply migrations
uv run alembic downgrade -1            # revert the last one
uv run alembic revision --autogenerate -m "add users table"
uv run alembic current                 # show applied revision
```

Autogenerated migrations must be read before they are committed; Alembic does
not detect every change, and it never infers a safe data migration.

## Checks

```bash
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run mypy
```

No test calls a language model. The suite runs against `tests/fakes.py`, so it
is fast, free, deterministic, and fails only for reasons in this repository.

Tests marked `integration` need a running database and **skip** when none is
reachable, so a green run without a database does not mean the database layer
was exercised. Point them elsewhere with `TEST_DATABASE_URL`:

```bash
uv run pytest -m integration
TEST_DATABASE_URL=postgresql+asyncpg://pia:pia@localhost:5433/pia uv run pytest
```

---

## Configuration

Configuration is read from environment variables, with `.env` as a local
convenience. `.env` is git-ignored; `.env.example` documents the variables that
the current implementation actually consumes.

| Variable                            | Default | Notes                                               |
| ----------------------------------- | ------- | --------------------------------------------------- |
| `ENVIRONMENT`                       | `local` | `local`/`test`/`development`/`staging`/`production`  |
| `DEBUG`                             | `false` | Rejected at startup when `ENVIRONMENT=production`    |
| `LOG_LEVEL`                         | `INFO`  | Standard logging levels                             |
| `LOG_FORMAT`                        | `json`  | `json` for deployments, `console` for local         |
| `DATABASE_URL`                      | —       | **Required.** Must use `postgresql+asyncpg`         |
| `DATABASE_POOL_SIZE`                | `5`     | Persistent pooled connections                       |
| `DATABASE_MAX_OVERFLOW`             | `10`    | Extra connections allowed above the pool            |
| `DATABASE_POOL_TIMEOUT_SECONDS`     | `10`    | Wait for a free connection before failing           |
| `DATABASE_CONNECT_TIMEOUT_SECONDS`  | `5`     | TCP/handshake timeout                               |
| `DATABASE_COMMAND_TIMEOUT_SECONDS`  | `30`    | Default per-statement timeout                       |
| `DATABASE_HEALTH_TIMEOUT_SECONDS`   | `2`     | Bound on the readiness probe's query                |
| `JWT_SECRET`                        | —       | **Required.** At least 32 characters                |
| `JWT_ALGORITHM`                     | `HS256` | `HS256`/`HS384`/`HS512`                             |
| `ACCESS_TOKEN_TTL_SECONDS`          | `900`   | Access tokens cannot be revoked before expiry       |
| `REFRESH_TOKEN_TTL_SECONDS`         | `2592000` | Must exceed the access token TTL                  |
| `OPENAI_API_KEY`                    | —       | Required only in production                         |
| `LLM_MODEL`                         | `gpt-4.1-mini` | Confirm availability on your account         |
| `LLM_TEMPERATURE`                   | `0.2`   | Low: extraction should be reproducible              |
| `LLM_TIMEOUT_SECONDS`               | `30`    | Per attempt                                         |
| `LLM_MAX_ATTEMPTS`                  | `2`     | Bounded; there is no unlimited retry                |
| `REDIS_URL`                         | —       | Required in production                              |
| `RATE_LIMIT_ENABLED`                | `true`  | Must be true in production                          |
| `USAGE_LIMITS_ENABLED`              | `true`  | Plan allowances. Must be true in production         |
| `MEMORY_EXTRACTION_ENABLED`         | `true`  | Off runs the product without memory                 |
| `EMBEDDING_MODEL`                   | `text-embedding-3-small` | Changing it needs a re-embed        |
| `EMBEDDING_TIMEOUT_SECONDS`         | `15`    | Per attempt                                         |
| `MEMORY_RETRIEVAL_LIMIT`            | `6`     | Memories one answer may draw on                     |
| `MEMORY_SIMILARITY_THRESHOLD`       | `0.25`  | **Uncalibrated** — see below                        |
| *(profile)* `timezone`              | `UTC`   | Decides what "last week" means, per user            |
| `RATE_LIMIT_FAIL_OPEN`              | `true`  | Behaviour when Redis is unreachable                 |
| `RATE_LIMIT_TIMEOUT_SECONDS`        | `0.25`  | Limiting never waits longer than this               |
| `TRUST_PROXY_HEADERS`               | `false` | Honour `X-Forwarded-For`. See below                 |

`DATABASE_URL` and `JWT_SECRET` have no defaults on purpose: a service that
silently falls back to another database, or to a guessable signing key, is worse
than one that refuses to start. Rotating `JWT_SECRET` invalidates every
outstanding access token; refresh tokens survive, since they are not JWTs.

---

## Deployment

**Not deployed yet.** There is no Dockerfile or hosting configuration in the
repository. This section records what a deployment needs, so that work starts
from the right list.

A running system is four processes plus one external service:

| Component            | Requirement                                                         |
| -------------------- | ------------------------------------------------------------------- |
| PostgreSQL           | 17, with the `vector` extension available to `alembic upgrade head` |
| Redis                | Rate limiting; `REDIS_URL` is required in production                |
| API                  | `uvicorn app.main:app`, behind HTTPS                                |
| Worker               | `python -m app.workers`; without it nothing is learned from chat    |
| OpenAI               | Chat and embeddings; paid, with no free tier                        |

Before the first start, and on every release, run `alembic upgrade head`
against the target database.

Production settings that differ from `.env.example`:

- `ENVIRONMENT=production`, `DEBUG=false`, `LOG_FORMAT=json`
- a newly generated `JWT_SECRET`, never the one used locally
- `OPENAI_API_KEY` set; startup fails without it in production
- `RATE_LIMIT_FAIL_OPEN=false` once Redis is reliable (see
  [When Redis is down](#when-redis-is-down))
- `TRUST_PROXY_HEADERS=true` only behind a proxy that overwrites
  `X-Forwarded-For` (see [Proxies](#proxies))
- `DATABASE_POOL_SIZE` + `DATABASE_MAX_OVERFLOW` below the database's
  connection limit, counting both the API and the worker. Small hosted plans
  allow few connections.

The mobile app's release build connects only over HTTPS, so the API needs a TLS
certificate before the app can use it.

<details>
<summary>Low-cost hosting options considered</summary>

Free tiers change often, so check current terms before relying on them.

- **Managed services.** Neon or Supabase for PostgreSQL with pgvector, Upstash
  for Redis, and Render or Koyeb for the API. These hosts give you HTTPS. Free
  web services sleep when idle, so the first request after a pause is slow.
  They do not run background workers for free, so the worker would have to
  share the API's container.
- **One VM.** Oracle Cloud's Always Free tier runs `docker-compose.yml` plus a
  TLS proxy such as Caddy on one machine, and nothing sleeps. You maintain the
  operating system, backups and firewall yourself.

Either way, OpenAI usage is billed. Set a monthly spend limit on the account.

</details>

---

## Architecture

Dependencies flow inward. Infrastructure and API sit at the edges.

```text
API            HTTP, validation, authorization, error mapping, SSE
 ↓
Application    use-case orchestration, transaction boundaries
 ↓
Domain         business concepts and rules, framework-independent
 ↑
Infrastructure PostgreSQL, pgvector, Redis, LLM providers, observability
```

Present layout:

```text
app/
├── main.py                              application factory
├── api/
│   ├── dependencies.py                  shared FastAPI dependencies
│   ├── errors.py                        exception → HTTP mapping
│   ├── health.py                        liveness / readiness
│   └── middleware.py                    correlation id + access logging
├── api/
│   └── rate_limiting.py                 limits and the dependencies applying them
├── api/v1/
│   ├── auth.py                          register / login / refresh / logout
│   ├── profile.py                       profile read and update
│   └── router.py                        /api/v1 aggregation
├── retrieval/
│   ├── filters.py                       scoping every memory search applies
│   ├── fusion.py                        reciprocal rank fusion
│   ├── hybrid.py                        keyword + semantic, fused and reranked
│   ├── keyword.py                       Postgres full-text search
│   ├── reranker.py                      relevance, importance and recency
│   ├── semantic.py                      vector search over a user's memories
│   ├── structured.py                    exact totals, computed in SQL
│   └── temporal.py                      what happened over a period
├── application/
│   ├── analysis/                        evidence collection and persistence
│   ├── auth/                            registration, login, token rotation
│   ├── conversation/                    chat turns, listing and pagination
│   ├── memory/                          extraction, lifecycle and user control
│   ├── onboarding/                      interview turns and persistence
│   └── profile/                         profile read and update use cases
├── domain/
│   ├── analysis/                        evidence, claim types, grounding rules
│   ├── conversation/                    context assembly and bounding
│   ├── memory/                          similarity, pairing, decisions, policy
│   ├── onboarding/                      coverage scoring, completion policy
│   ├── retrieval/                       what a question needs, evidence grades
│   └── temporal/                        period resolution, timezone-aware
├── graph/
│   ├── analysis/                        generation and grounding verification
│   ├── conversation/                    retrieval, evidence grading, reply
│   ├── memory/                           extraction, filtering, conflict resolution
│   └── onboarding/                      the interview workflow and its nodes
├── evaluation/
│   ├── dataset.py                       personas and labelled cases, validated
│   ├── retrieval.py / answers.py        the two evaluators
│   └── __main__.py                      python -m app.evaluation
├── workers/
│   ├── __main__.py                      python -m app.workers
│   └── runner.py                        claims and runs background jobs
├── core/
│   ├── config.py                        settings
│   ├── context.py                       request-scoped correlation context
│   ├── exceptions.py                    application error taxonomy
│   └── security.py                      password hashing and token primitives
├── infrastructure/
│   ├── cache/
│   │   ├── rate_limiter.py              sliding window log over Redis
│   │   └── redis.py                     client lifecycle and health
│   ├── database/
│   │   ├── health.py                    connectivity probe
│   │   ├── models/                      persistence models
│   │   ├── repositories/                query access per aggregate
│   │   └── session.py                   engine and session factory
│   ├── llm/
│   │   ├── embeddings.py                embedding provider and adapter
│   │   ├── openai_provider.py           the OpenAI adapter
│   │   ├── prompt.py                    prompt value object, untrusted fencing
│   │   ├── prompts/                     versioned prompt text
│   │   └── provider.py                  the LLMProvider protocol
│   └── observability/
│       ├── logging.py                   structured log formatters
│       └── tracing.py                   per-run step and model-call traces
└── schemas/
    └── common.py                        response envelopes

migrations/                              Alembic environment and revisions
scripts/
└── benchmark_turn.py                    per-step latency of a turn, model excluded
tests/
├── fakes.py                             deterministic model double
├── unit/
├── graph/
└── integration/
```

Directories from `ENGINEERING_GUIDELINES.md` that no feature needs yet
(`workers/`) are created when the corresponding phase begins.

#### A deliberate deviation from the documented layout

`ENGINEERING_GUIDELINES.md` §8 places repository *interfaces* in
`domain/repositories/` with implementations in `infrastructure/`. With a single
persistence implementation, that split would require domain entities and mappers
alongside the ORM models and API schemas — four representations of a user, for
no behavioural gain, against §1.1 ("do not build speculative abstractions") and
§68 ("keep persistence separate from domain behaviour *where the complexity
justifies it*").

So concrete repositories live in `infrastructure/database/repositories/` and
application services depend on them directly. §69 is still honoured: API
contracts are dedicated Pydantic schemas, never ORM models.

Revisit this when a second persistence implementation appears, or when
application services grow logic worth testing without a database.

### Authentication

Access tokens are short-lived HS256 JWTs. Refresh tokens are **opaque random
strings**, stored only as SHA-256 hashes, which is what makes them revocable —
a JWT refresh token could not be invalidated before expiry.

Refreshing **rotates**: the presented token is marked `ROTATED` and a successor
is issued in the same family. Presenting an already-rotated token means it
leaked, so the entire family is revoked as `REUSE_DETECTED` and the legitimate
holder must log in again. Logout revokes the family too.

```text
login ──> token A ──refresh──> token B ──refresh──> token C
                │
                └──replay──> A was already rotated
                             ⇒ revoke A, B, C (REUSE_DETECTED)
```

Passwords are hashed with Argon2id. Login verifies against a dummy hash when the
email is unknown, so response time does not reveal which addresses are
registered, and the error is byte-identical for an unknown email and a wrong
password. Registration necessarily returns `409` for a taken email; that is a
deliberate usability trade-off, mitigated by rate limiting once Redis lands.

### User isolation

Every profile query is keyed by the authenticated user's id, taken from the
token — never from the request body or path. The authenticated user is reloaded
from the database on each request rather than trusted from token claims, so
deactivating an account takes effect immediately instead of at token expiry.

### Rate limiting

Two things need protecting: credentials, and money. Both are enforced with a
**sliding window log** evaluated atomically inside Redis by a Lua script. A
fixed-window counter is cheaper but lets twice the limit through at a window
boundary, which is exactly the burst a credential-stuffing attempt produces;
running the read, the eviction and the write as one script also removes the race
where concurrent requests each see a count below the limit.

| Rule | Limit | Keyed on |
| --- | --- | --- |
| `POST /auth/register` | 5 / hour | address |
| `POST /auth/login` | 10 / 15 min | address |
| `POST /auth/login` | 5 / 15 min | **account** |
| `POST /auth/refresh` | 60 / hour | address |
| `POST /onboarding/message` | 60 / hour | user |
| `POST /analysis` | 10 / hour | user |
| `POST /chat` | 120 / hour | user |

Login carries two rules because password guessing takes two shapes: one address
trying many accounts, and many addresses trying one. The per-account rule is
keyed on the address *being attacked* rather than the attacker, so an attempt
spread across a botnet is still bounded — and is covered by a test that drives
every guess from a different source address.

Rejections answer `429` with `Retry-After`; allowed responses carry
`X-RateLimit-Limit` and `X-RateLimit-Remaining`.

Reads are deliberately unlimited. Throttling a cheap authenticated `GET`
alongside a paid model call would trade real usability for no protection.

#### When Redis is down

Rate limiting **fails open** by default: requests are allowed, and an `ERROR` is
logged. The trade-off is explicit — failing open costs a window with no
enforcement, which is rare and detectable; failing closed costs a total outage of
login and every paid endpoint. Set `RATE_LIMIT_FAIL_OPEN=false` for deployments
that would rather be unavailable than unprotected.

`/ready` reports Redis as a dependency only when rate limiting is actually meant
to be running, so a deployment that has deliberately turned it off is not held
not-ready for something it does not use.

#### Proxies

`X-Forwarded-For` is caller-controlled unless a proxy overwrites it, so honouring
it by default would make every per-address limit optional — anyone could bypass
one by sending a header. It is ignored unless `TRUST_PROXY_HEADERS=true` says a
proxy you control sets it.

### Subscriptions

Rate limits stop bursts; plans bound what an account costs over a month. Every
metered request is a paid model call, so each plan allows a fixed number per
calendar month (UTC):

| Feature                           | Free | Pro   |
| --------------------------------- | ---- | ----- |
| `chat_message` (`/chat`, `/chat/stream`) | 50 | 1,500 |
| `week_plan` (`POST /plans/week`)  | 2    | 20    |
| `analysis` (`POST /analysis`)     | 2    | 20    |

Onboarding is not metered: no one can judge the product before finishing it.
Pro is capped rather than unlimited so that no account can cost more than it
pays. The limits live in `app/domain/subscription/plans.py`, not in
configuration, for the same reason the rate limits do.

When an allowance runs out the API answers `402 QUOTA_EXCEEDED`, with the plan,
feature, limit and `resets_at` in `error.details`. That is deliberately not
`429`: retrying in a minute will not help, so a client should show the reset
date or offer an upgrade.

#### A unit is taken before the work, and handed back on failure

Checking a count and then doing the work lets concurrent requests all pass the
check. Instead, one conditional upsert takes a unit only while the counter is
below the limit, and the row lock that the conflict takes makes concurrent
requests queue on it. The unit is committed at once, before any model call.

If the request then fails, for example with `409`, `404`, `422` or a provider
error, the dependency that took the unit gives it back. A stream fails after its
`200` has been sent, so the dependency never sees the error, and the stream's
error branch gives the unit back instead. A client that disconnects mid-stream
is not refunded, because the model calls have already been paid for.

Usage is a counter (`usage_counters`), not a count of the rows each feature
creates, so deleting a conversation does not give its messages back.

#### Plans

No row in `subscriptions` means Free. A grant can have an end date. The end date
is checked whenever the plan is read, and no job downgrades accounts, so an
expired plan cannot outlast its end date because a job did not run. Until a
payment provider is connected, grant plans by hand:

```bash
uv run python -m scripts.grant_plan ada@example.com pro --until 2026-12-31
uv run python -m scripts.grant_plan ada@example.com free
```

Payment is not built yet. Store rules require in-app purchase for digital
subscriptions in the mobile apps, so the plan is RevenueCat: the app buys
through the App Store or Google Play, and RevenueCat's webhook calls
`SubscriptionService.grant` with `source="revenuecat"` and the period's end as
`expires_at`. Deleting an account deletes its plan along with everything else.
A store subscription that is still active would be restored when the user signs
in again.

### Adaptive onboarding

A LangGraph workflow interviews the user, one exchange per HTTP request:

```text
START → extract_information → assess_context ─┬─ insufficient → generate_question → END
                                              └─ sufficient   → build_profile     → END
```

The loop between questions lives **across** requests, not inside a graph run, so
the graph itself is acyclic and cannot spin. Interview state is persisted between
turns, meaning a turn survives the process that produced it.

**The decision to stop asking is deterministic, not the model's opinion.** Each
dimension (goal, context, constraint, preference) is scored from the confidence
of its evidence: a noisy-OR over the strongest items measures *strength*, scaled
down when fewer items were collected than the dimension requires, measuring
*sufficiency*. A weighted sum gives overall readiness. The interview ends when
confidence clears the threshold and a floor of questions has been asked — or
unconditionally at the question ceiling, which is what guarantees termination
even if the user never says anything usable.

The LLM is used for exactly two things: turning an answer into structured
evidence, and phrasing the next question. Which dimension to ask about, whether
there is enough context, and what reaches the profile are all decided in code.

Failures degrade rather than break: a failed extraction loses that answer's
evidence but the interview continues, and a failed question generation falls
back to a fixed question for the target dimension.

### Exact answers

"How many hours did I study last week?" is not a retrieval question. It is
arithmetic, and a model asked to add up quantities from retrieved sentences
produces something plausible rather than something correct.

```text
START → understand_query → retrieve_context → [grade_evidence ⇄ retry_retrieval]
      → build_context → generate_reply ⇄ verify_answer → END
```

The two loops are described under [Agentic retrieval](#agentic-retrieval) and
[Answer verification](#answer-verification).

`understand_query` asks the model only what *kind* of information the question
needs, and which period it is about. It never computes a date and never computes
a number. Naming a period is a language task; resolving one is arithmetic that
models get quietly wrong, and a wrong range silently changes an answer the user
expects to be exact.

`retrieve_context` then gathers what the plan asked for — memory recall, a
timeline, exact totals. See [Where concurrency pays](#where-concurrency-pays) for
which parts run in parallel, and why the database queries deliberately do not.

Totals are computed by the database, grouped by unit as well as activity,
because adding hours to kilometres produces a number with no meaning. They reach
the prompt as figures the model is told not to recompute, round, or add to.
Occurrences with no recorded amount are reported alongside the total rather than
hidden: "three sessions, two of them timed" is honest where "2.5 hours" alone is
not.

#### Events are separate from memories

An event is something the user did, at a time, sometimes with an amount. It is
counted and summed by the database, whereas a memory is retrieved by meaning and
read by a model. They live in different tables for that reason.

The distinction has a sharp consequence. Deduplication is about **beliefs**:
hearing the same goal twice is one goal. An event is about a **moment**:
studying on Monday and again on Wednesday is two occurrences however alike the
wording. So a repeated event is recorded again even when its memory is merely
reinforced — collapsing the two would silently undercount every total.

#### The model never supplies a date

When extraction records an event, the model gives a **day offset** — 0 for
today, 1 for yesterday — and the offset is resolved here. Events are placed at
local midday so they cannot drift into the neighbouring day when converted to
UTC, which would move them between weeks.

#### Timezones

"Last week" is a question about the user's calendar, not the server's. Answering
it in UTC can include or exclude a whole day of activity for anyone far enough
from Greenwich, which changes a number the user expects to be exact. The profile
carries a timezone, and every range boundary is resolved in it.

### Hybrid retrieval

```text
query ─┬─ keyword search (Postgres full-text) ─┐
       └─ semantic search (pgvector) ──────────┴─ RRF fusion ─ rerank ─ top N
```

**Keyword search complements semantic search rather than duplicating it.**
Embeddings blur names, identifiers and rare terms into their neighbourhood, which
is exactly where an exact word match helps: a question about "Rex" should find the
memory about Rex, not the ones about dogs in general. English stemming lets
"studying" find "studied".

The search vector is a **generated column**, so it cannot drift from the text it
indexes, and keyword search needs no network call — it keeps working when the
embedding provider does not. Before this phase, a failed query embedding meant no
recall at all; now it costs recall by meaning while recall by words continues.

Queries are built as an **OR of the message's words**, not the AND that
`websearch_to_tsquery` produces. A conversational question carries many words a
memory will not, and requiring all of them would make keyword search find almost
nothing (verified: the AND form fails to match "I play the cello" for "which
instrument, the cello or violin, do I play?"). Only runs of letters and digits
survive into the query, so it is valid tsquery syntax whatever the user typed.

**Fusion uses ranks, not scores.** Cosine similarity and full-text rank live on
unrelated scales, and adding them would need calibration nobody has done.
Reciprocal Rank Fusion only uses positions, so a memory both retrievers rank
highly accumulates from both.

**Reranking is feature-based and deterministic**: fused relevance, then importance
and recency — exactly the signals first-stage retrieval ignores. Relevance is
weighted to dominate, because every candidate has already cleared a relevance gate
and the other signals should only decide between memories that are all on topic.
Relevance is normalised against the best score *achievable*, not the best in the
batch, so a lone weak match cannot look perfect because nothing beat it.

`Reranker` is a protocol: a cross-encoder or a model call could replace this one.
The per-turn path already spends a planning call, an embedding and a completion,
so a heavier reranker should earn its latency in evaluation first.

Both retrievers apply the same scoping through one function
(`retrieval/filters.py`). Each applying its own would eventually disagree, and the
disagreement would be a leak.

The candidate pool sizes, RRF constant and rerank weights are starting points.
PRD 21 requires them to be benchmarked; that is the evaluation phase's job.

#### A stale vector is never compared — but the memory is still found

Phase 8's rule is that vectors from different embedding models are never
compared. That rule concerns *vectors*. Keyword search never touches them, so a
memory whose embedding is stale or failed is still found by its words, and
reported as keyword-only with no similarity score.

### Where concurrency pays

The embedding call goes to a different service from the database, so it is
started first and overlaps with the database work. The database queries run one
after another.

Each retriever reads through a short session of its own (see
[Connections are not held across model calls](#connections-are-not-held-across-model-calls)),
so they *could* run concurrently. They don't, because that would hold up to four
pooled connections per turn to save part of a step measured at about 13 ms, on a
turn dominated by model calls — the wrong trade while connections are the scarce
resource. (Phase 9 originally gathered them on one shared session, which bought
no speed and relied on an adapter internal SQLAlchemy says not to depend on.)

`create_task` only schedules a call, so the node yields once after creating the
embedding task; otherwise the request would not be sent until the database driver
happened to yield, and the overlap would be an accident of the driver. If the
database work fails, the in-flight embedding call is cancelled and awaited before
the error propagates. All three behaviours are pinned by tests.

### Agentic retrieval

Retrieval can succeed and still return the wrong thing. "How is my pet getting
on?" shares the word *getting* with "I am getting on well with my new manager"
and no word at all with "My greyhound Biscuit loves long walks". Hybrid search
finds the first and misses the second, and before this phase the reply was
written from whatever came back.

```text
retrieve_context ─→ grade_evidence ─┬─ sufficient ───────────────────┐
                        ↑           ├─ attempt budget spent ─────────┤
                        │           ├─ no new search to try ─────────┼─→ build_context
                        │           └─ insufficient ─→ retry_retrieval
                        └──────────────────────────────────┘
```

`grade_evidence` asks the model which recalled memories do not bear on the
question, whether what remains is enough, and — only if it is not — for a short
search for what is missing. `retry_retrieval` runs that search, merges the result
with what survived grading, and the union is graded again.

**The loop is bounded by an explicit counter**, `retrieval_attempts`, checked in
routing against `EvidencePolicy.max_retrieval_attempts` (default 2: one rewrite
at most). The retry node advances it on every entry, including the one where it
has nothing to search, so termination does not depend on routing being right.
The loop also stops early when another attempt could not change anything: the
grader proposes a search already tried (retrieval is deterministic, so it would
return the same memories), proposes none, or fails. A test runs a grader that
is never satisfied and asserts the retrieval count equals the budget.

Design decisions:

- **Grading and rewriting are one model call.** Working out what is missing is
  most of the work of searching for it; a separate rewrite call would add a round
  trip to every retry.
- **The grader names memories to drop, not memories to keep.** An omission then
  keeps a memory — the behaviour before grading existed — rather than silently
  losing a relevant one.
- **Only memory is searched again.** Timelines and totals are computed from the
  plan's period and activities, not from the question's wording, so rerunning
  them with different words would return the same rows.
- **An empty recall is not graded.** Hybrid search already matches loosely, so
  finding nothing usually means there is nothing to find — most often for a new
  user, who would otherwise pay a grading call and a retry on every message.
- **Grading fails open.** If the grading call fails, memories are used as
  retrieved and no retry runs: exactly the pre-grading behaviour.
- **Unresolved gaps are admitted.** If the last grade is still insufficient, the
  reply prompt says the search did not find everything the question needs, so
  the model says what it does not know instead of filling the gap. That note sits
  outside the untrusted fence because it is the system's own finding; the
  grader's words never reach the reply.

The rewritten query is model-written, and it is derived from untrusted content.
It only ever reaches the same user-scoped retrievers as the message itself, and
keyword search builds its query from letters and digits alone, so a hostile
rewrite can search the user's own memories and nothing else. An integration test
checks that a rewrite matching another user's memory does not recall it.

Each turn logs `evidence_graded` (attempt, graded, kept, sufficient, duration)
and `memory_retrieval_retried` (attempt, found, candidates, duration), and the
turn summary records `retrieval_attempts` — never the query text.

**Whether this improves answers is unmeasured.** Grading adds a model call to
every turn that recalls anything, and a retry adds another plus an embedding and
a search. The tests prove the loop is bounded and does what a grade says; only
evaluation can show whether a real grader's judgements are worth that latency.
`EvidencePolicy(enabled=False)` exists so evaluation can compare the two.

### Answer verification

Grading decides what the model is shown; verification checks what it said. A
reply can still state things about the user that nothing supports — "since you
ran a marathon last spring" — and a personal assistant that invents personal
history is worse than one that admits it does not know.

```text
generate_reply → verify_answer ─┬─ nothing unsupported ──────┐
       ↑                        ├─ not verified ─────────────┤
       │                        ├─ attempt budget spent ─────┼─→ END
       │                        └─ unsupported ─→ regenerate │
       └────────────────────────────────────────┘
```

`verify_answer` gives a model the same context sections the reply was given
(built by one shared function, so the two cannot drift apart) plus the reply,
and asks for every statement **about the user** that is unsupported, contradicted
by what they said, or gives a figure that differs from the exact figures. General
advice is out of scope: this checks grounding, not correctness.

**The verifier's findings are checked, not trusted.** Each must quote the reply,
and a quote the reply does not contain is discarded — the same principle as the
analysis graph, where a cited evidence id that does not exist is dropped. Quotes
are matched ignoring case, spacing, surrounding punctuation and typographic
quotes, and must be at least eight characters, so "you" cannot confirm a finding
by appearing somewhere. Dropping an unlocatable finding errs towards accepting
the reply; the alternative is rejecting replies on the verifier's say-so.

Whether a reply is grounded is decided by which findings survive that check,
not by a flag the model sets. A verifier asked for a boolean can say "false"
without being able to point at anything wrong.

A reply with confirmed findings is regenerated with those statements quoted back,
fenced as untrusted (they are model output shaped by untrusted context). The
**draft with the fewest confirmed findings** is returned, so a worse retry cannot
replace a better first draft; on a tie the earlier draft stays.

Failure handling:

- verification fails → the reply is returned unverified, `grounded: null`; an
  outage of the checker is not evidence that a reply invented anything
- the first draft fails → the fallback reply, `degraded: true`, not verified
- a regeneration fails → the best earlier draft is returned rather than the
  fallback; a flagged answer still answers, the fallback text does not

**An ungrounded reply is kept away from memory extraction.** Extraction reads
the assistant's reply alongside the user's message; remembering an invented
statement would turn it into evidence for every later answer.

#### Both loops are bounded, and so is the graph

`generation_attempts` is advanced by every generation, including one that fails,
and checked against `VerificationPolicy.max_generation_attempts` (default 2).

LangGraph's own default step limit is about ten thousand (`10007` in the pinned
version, overridable by environment variable). For this graph that would mean
thousands of model calls before a broken loop bound was noticed. The compiled
graph therefore carries an explicit `recursion_limit` set to the **exact** worst
case for its policies — understand and retrieve, a grade per retrieval attempt
with a retry between each, build, then a generation and a verification per
generation attempt — plus the one extra step LangGraph requires (measured, not
assumed). Tests run both loops to exhaustion at several budgets and pass, and
fail one step below the limit, so the limit has no slack for an unplanned step.

With the default policies a turn takes at most ten steps. In model calls that is
a plan, two grades, two replies and two verifications — **seven**, against two
before Phases 11 and 12. The common case (sufficient evidence, grounded first
draft) is four, or three when nothing is recalled and so nothing is graded.

**Whether this improves answers is unmeasured.** Every turn now spends a
verification call, and an ungrounded draft a further completion and
verification. `VerificationPolicy(enabled=False)` exists so evaluation can
compare.

### Streaming

`POST /api/v1/chat/stream` takes the same body as `POST /api/v1/chat` and
answers with server-sent events:

```text
event: query_started           {"conversation_id": "…"}
event: query_understood        {"needs": ["semantic"]}
event: retrieval_completed     {"memories": 3, "timeline_entries": 0, "has_totals": false}
event: evidence_graded         {"memories_kept": 2, "sufficient": true}
event: retrieval_retried       {"attempt": 2, "memories": 4}          (only on a retry)
event: draft_completed         {"attempt": 1, "failed": false}
event: verification_completed  {"grounded": true}
event: reply                   {"content": "…", "grounded": true}
event: completed               {"data": <the POST /chat response>, "meta": {"request_id": "…"}}
```

**Only a verified reply is streamed.** A draft may fail verification and be
rewritten, so no draft text appears in any event until the turn has finished;
progress events carry counts and outcomes only. They are built from an explicit
allow-list per step, not by forwarding graph state, so a field added to the state
later cannot reach clients by accident. A test sends a draft that fails
verification and asserts its text appears nowhere in the stream.

That has a cost the policy accepts: the reply arrives in one `reply` event once
the whole turn — retrieval, grading, drafting, verification — has finished, so
time to first reply token is the full turn latency. The stream's value is the
progress before it. Splitting an already-complete reply into `token` events
would only imitate streaming, so it is not done. If no draft passes within the
budget, the best one is still released, as `POST /chat` does, with
`grounded: false` in the `reply` event itself so a client can decide how to
present it before rendering it.

**The reply is stored before it is released.** The exchange is committed, then
`reply` and `completed` are sent; a test makes the commit fail and asserts no
`reply` event appears. If the client disconnects, the turn is cancelled and
nothing is committed: the request's session rolls back.

**Errors before the stream opens are ordinary HTTP errors.** Authentication,
validation, rate limiting and conversation ownership are resolved before the
`200` is sent, so a foreign conversation is still a `404` with the usual error
body. A failure after that can only be reported in the stream, as
`event: error` with a stable code (`TURN_FAILED`) and the request id; the
exception itself is logged, never sent.

Both chat endpoints draw on one rate-limit budget, so neither is a way around
the other. Both queue memory extraction in the same transaction as the
exchange, with the same rule that an ungrounded reply is withheld from it.

Responses carry `Cache-Control: no-cache, no-transform` and
`X-Accel-Buffering: no`, because a proxy that buffers the response would hold
every progress event until the turn finished.

### Execution tracing

Every conversation turn is a **run** with an id, returned as `run_id` on both
chat endpoints (and in the stream's first event). The run is stored, and its
trace can be read back:

```text
GET /api/v1/runs/{run_id}         status, duration, model calls, tokens, attempts, outcome
GET /api/v1/runs/{run_id}/trace   the same, plus every step in order
```

A trace, from a turn run locally without model credentials:

```text
understand_query      0.13 ms  ok  {needs: [semantic]}         retrieval.plan     failed LLMConfigurationError
retrieve_context     15.34 ms  ok  {memories: 0, …}             embedding.query    failed LLMConfigurationError
build_context         0.03 ms  ok  {}
generate_reply        0.16 ms  ok  {attempt: 1, failed: true}   conversation.reply failed LLMConfigurationError
```

**How it is collected.** A run's trace is bound to a context variable before the
graph runs. Each node is wrapped once, in the graph builder, so no node has to
know tracing exists; the model and embedding providers are wrapped once, in
`create_app`, so every call lands in the step that made it — including the
embedding call `retrieve_context` starts as a separate task, which inherits the
step's context. Context variables are what keep concurrent requests apart: the
nodes and providers are shared, and a test runs two traced runs concurrently and
asserts neither records into the other. Log lines inside a run carry its
`run_id`, read from the same trace so a log line and a stored trace cannot
disagree.

**Token usage** comes from the OpenAI adapter, which reports each attempt's
usage — every attempt is billed, including one whose output was unusable.
Structured calls request LangChain's raw message alongside the parsed result,
because that is the only place usage is reported. A provider that reports none
records `null`, never zero. Cost is not computed: it needs a price table per
model, which belongs with evaluation.

**What is stored is metadata.** Step detail comes from the same per-step
allow-list that decides what the stream shows, so the two cannot drift apart.
Model calls are recorded by prompt name and version, never prompt text; errors
by exception type, never message. Tests assert that neither the question nor
the reply appears anywhere in a trace.

**Failed runs are stored too** — they are the ones a trace is most needed for.
The exchange rolls back, then the run is written on its own with its status,
the exception type and the step it failed in. A conversation created for that
turn is rolled back with it, so the run does not point at it. (The ids are read
before the rollback: a rollback expires loaded objects, and reading one on an
async session raises rather than reloading — a test pins this.) A turn the
client abandons is cancelled and leaves no run.

Runs are scoped to their user like everything else (another user's run is
`404 RUN_NOT_FOUND`, identical to one that does not exist), deleted with the
user, and kept when their conversation is deleted. Only conversation turns are
traced so far; onboarding, analysis and background memory extraction are not.
Runs are kept indefinitely; a retention policy is future work.

#### Why there is no LangGraph checkpointer

LangGraph can save the full graph state after every step. That is not used, by
decision rather than omission:

- A conversation turn starts and finishes inside one request and is never
  resumed, so there is nothing to restore from a checkpoint.
- The state holds recalled memories, drafts and the profile. Saving it after
  each of up to ten steps would keep a second, unbounded copy of the user's
  personal data, against PRD 54's "avoid storing unnecessary raw personal data".
- It would add `langgraph-checkpoint-postgres` and with it a second Postgres
  driver (psycopg) and connection pool alongside asyncpg.

The run record is the persisted execution state: which steps ran, in what
order, how long each took, what it cost, and where it failed. A checkpointer
earns its place when a graph pauses for a person or runs long enough to need
resuming — human-in-the-loop approval, or a background job that outlives a
process — and none does yet. Onboarding's multi-turn state is already persisted
in its own table.

### Background processing

Memory extraction used to run in the API process after the response was sent,
and was lost whenever that process stopped — on every deploy, crash or restart —
with no retry. It now runs from a job queue, processed by a separate worker:

```bash
uv run python -m app.workers
```

**The queue is a PostgreSQL table, not Redis.** A job is written in the same
transaction as the exchange that needs it, so an exchange can never be stored
without its extraction job, or the other way round. This deployment's Redis is
also deliberately non-persistent (rate-limit counters only), so a queue there
would lose every waiting job whenever Redis restarted. No dependency was added.

**A job holds references, not content.** The payload names the conversation and
the two messages; the worker reads them from where they are stored, scoped to the
job's user, so the queue is never a second copy of what users wrote.

**Claiming.** Workers take the oldest due job with `FOR UPDATE SKIP LOCKED`, so
any number of them can poll without blocking each other or taking the same job.
A claim is a lease (`JOB_LEASE_SECONDS`) with a token. A job is bounded by
`JOB_TIMEOUT_SECONDS`, which must be shorter than the lease — the settings refuse
to load otherwise — so a job still running is never claimed by a second worker.
If a worker dies, its connection and row locks die with it, the lease runs out,
and another worker picks the job up.

**Exactly-once effects.** The worker marks the job succeeded *first*, inside the
transaction that then does the extraction, conditional on its claim token still
standing. Extraction commits once, so the memories and the job's completion
become visible together or not at all. A retry after a crash finds either both
or neither, and can never record the same run twice and double a total. A worker
whose claim was superseded commits nothing.

**Failures** are retried with exponential backoff from `JOB_RETRY_BASE_SECONDS`,
capped at an hour, up to `JOB_MAX_ATTEMPTS` (default 3). A job out of attempts
is kept as `FAILED` with its last error type, rather than deleted, so failures
stay visible. A worker that cannot reach the database keeps polling and
recovers; `SIGTERM` lets the job in progress finish, bounded by its timeout,
before the worker exits.

**Tests** run the real worker path: the shared integration-test client drains
the queue through `JobWorker.run_once` after every response, as a prompt worker
would. Queue behaviour — atomic enqueue, claim contention, lease expiry,
superseded claims, retry and dead-lettering, cross-user payloads — is tested
against real PostgreSQL with a client that has no worker behind it.

Not done yet: worker runs are not traced as runs (only conversation turns are),
polling is used rather than `LISTEN/NOTIFY` (a second's delay is acceptable for
extraction), and succeeded jobs are kept indefinitely.

### Caching

The PRD lists profile context and retrieval results as caching candidates.
Both were measured before being built, with `scripts/benchmark_turn.py`: the
real application against PostgreSQL, a user with 200 memories, a full profile
and events, and the widest retrieval plan, using the run traces for timings.

```text
step                                p50 ms   p95 ms
prepare_turn (profile + history)      5.4      6.4
retrieve_context                     12.7     14.6
every other step                     < 0.1    < 0.2
whole turn, model excluded           17.6     19.8

profile load alone                    3.25     3.91
Redis GET, 2 KB (a cache hit)         0.08     0.11
```

- **Profile context** would save about 3 ms per turn. Every profile write path —
  the profile endpoint and onboarding completion — would need invalidation, and
  a stale profile changes answers. That trade is not worth 3 ms.
- **Retrieval results** could save up to ~13 ms, but only on a hit. A hit needs
  the identical question and an unchanged memory version, and extraction changes
  memories after most exchanges, so the hit rate would be close to zero.

A turn's model calls (planning, grading, reply, verification, embedding) are
expected to dominate it by orders of magnitude, but that is not yet measured
here: every number above uses a fake model. Caching should be revisited with
live-model traces, starting from where those say the time goes.

```bash
uv run python -m scripts.benchmark_turn --turns 40 --seed-turns 25
```

### Evaluation

```bash
uv run python -m app.evaluation retrieval            # retrieval quality per strategy
uv run python -m app.evaluation answers --judge      # end to end, through the API
uv run python -m app.evaluation compare BASE.json CANDIDATE.json
```

Both runs need `OPENAI_API_KEY`: they measure the real model. They create
synthetic accounts (`@evaluation.example.com`) in the configured database and
delete them when they finish, refuse to run against a production environment,
and write a JSON report to `evaluation/results/` (git-ignored, apart from
committed baselines).

**The dataset** (`evaluation/datasets/personas-v1.json`) holds four personas —
profiles, memories and dated events — and 45 questions labelled with the
memories that answer them (graded 2) or help (graded 1), the retrieval needs a
correct plan includes, and the exact figures a correct answer states. Cases are
spread across categories so a result shows *where* a strategy wins or loses:
direct, paraphrase, named, multi-memory, temporal and unanswerable. The schema
rejects labels that cannot be right — unknown memories, impossible grades, an
"unanswerable" case with an answer — and a test recomputes every expected
figure through the SQL totals query, so a mislabelled figure fails the build
rather than marking correct answers wrong. **The labels were written, not yet
reviewed by a person**; the dataset says so (`"reviewed": false`), and every
report carries that flag. The PRD's target is 50–100 reviewed cases, grown from
real failures.

**Retrieval evaluation** seeds the personas and runs every answerable case
through the four strategies the guidelines require to be compared, each built
exactly as production builds it (`HybridConfig.for_limit`, shared with the API)
apart from the one thing varied: vector-only, keyword-only, hybrid (rank fusion
with relevance-only weights) and hybrid with the feature reranker. It reports
Recall@K, Precision@K, MRR and graded nDCG, per strategy and per category, the
number of memories returned for unanswerable questions (noise), and latency.

**Answer evaluation** sends every question through `POST /api/v1/chat` and reads
the run trace. Deterministic checks carry the weight: whether the plan asked for
what the question needed, whether expected figures appear exactly in the reply,
whether verification found it grounded, error and degraded rates, latency,
model calls, tokens, and cost per request when prices are given. `--judge` adds
a model's 1–5 scores for faithfulness, relevance and completeness against the
labelled facts, and whether unanswerable questions are admitted as unknown —
reported as a judgement, not a measurement, since the judge shares the biases
of the model it grades. Replies are never checked for exact wording.

**What runs in CI**, with fakes: the dataset's validity and figure integrity,
the answer harness end to end, and a retrieval regression gate against
`evaluation/results/baseline-retrieval-fake-embeddings.json` — any strategy
scoring worse than the baseline fails the build (an intended change is
recorded with `UPDATE_EVALUATION_BASELINE=1`). Current baseline:

```text
strategy          recall@6    prec@6     MRR    nDCG@6
vector               0.381     0.081   0.485     0.440
keyword              0.641     0.157   0.707     0.665
hybrid               0.672     0.162   0.752     0.706
hybrid_reranked      0.672     0.162   0.758     0.716
```

These come from **fake embeddings**, which hash words into vectors: "vector"
here measures word overlap, not meaning, which is why it scores 0.08 recall on
paraphrase questions. They show that fusion beats either retriever alone and
that the reranker improves ordering without changing what is found, *for this
embedder*. They say nothing yet about real semantic search; a run with real
embeddings will. Precision@6 is low by construction: cases have one to three
relevant memories, so its ceiling is 0.17–0.5.

**Not built: the PRD's `POST /api/v1/evaluations/run` endpoints.** An evaluation
creates accounts and spends model budget, and there is no admin role to limit
who could trigger one; exposing it to every user would be a cost-amplification
endpoint. It belongs behind an operator role, which does not exist yet.

### Hardening

Each change below started from a measurement or a reproduction rather than a
checklist, and each is pinned by a test that fails if it is undone.

#### Connections are not held across model calls

A chat turn used to hold one pooled connection — and an open transaction — from
preparation until the exchange was stored, so across every model call in the
turn. Measured with a fake model that takes 2.5 s per call and the default pool
(5 + 10 overflow): **15 of 45 concurrent chats failed with 500** on the pool
timeout, and while turns waited on the model the median number of checked-out
connections equalled the number of turns in flight.

Now `prepare_turn` commits its reads before the graph runs, a new conversation is
created only when the exchange is stored, and retrievers read through short
sessions of their own (`HybridConfig`/retrievers take the session factory, and
the conversation graph is compiled once at startup). Same test: **45 of 45
succeed**, in about one turn's time, and the median checked-out connections
while turns wait on the model is **0**. A test checks the pool from inside every
model call on both chat endpoints.

#### Two messages sent at once to one conversation

Both read the same message count and claimed the same positions; the second
failed with a 500 on the unique constraint (reproduced: `[201, 500]`). The count
is now re-read under a row lock when the exchange is stored — a lock held for
that final transaction only — so concurrent sends are stored in order.

#### Password hashing blocked the event loop

Argon2 takes about 50 ms per hash, and ran inline in async handlers: eight
concurrent logins froze the event loop — every other request and stream — for
**477 ms**. Hashing now runs on a dedicated four-thread pool (worst freeze in
the same test: 63 ms). The pool is small on purpose: each hash uses 64 MiB, and
the default executor would allow dozens at once.

#### Request bodies are bounded before they are read

Schemas bound every field, but only after the body has been read and parsed: an
unauthenticated 50 MB login body allocated about 157 MB before validation
rejected it. Bodies over `MAX_REQUEST_BODY_BYTES` (1 MiB) are now refused with
`413 PAYLOAD_TOO_LARGE`, in the usual error envelope, as they arrive — a declared
oversize on the first read, an undeclared one when it crosses the limit (peak
allocation about 2 MB).

#### Smaller fixes

- **Blank `OPENAI_API_KEY`** (as `.env.example` ships it) was read as an empty
  secret and satisfied the production credentials check. Blank now means unset.
- **Every error response sent `X-Request-ID` twice**, which clients read as one
  comma-joined value matching no log line. Sent once now.
- **Response headers:** `X-Content-Type-Options: nosniff` everywhere, and
  `Cache-Control: no-store` unless a response sets its own (token responses
  should never be cached).
- **Retention:** the worker prunes run records after `RUN_RETENTION_DAYS` (30)
  and finished jobs after `JOB_RETENTION_DAYS` (7), hourly, in bounded batches;
  pending and running jobs are never touched.
- **Dependencies:** `pip-audit` over the locked set found one advisory, in
  `pyjwt` 2.14.0 (deeply nested JSON escaping a handler in the JWKS flow). The
  flow is not used here, and crafted nested tokens were confirmed to return 401,
  but the library signs every access token, so it is upgraded to 2.15.1 and the
  floor raised. The audit is now clean.

Reviewed and left as they were: login already equalises timing for unknown
accounts and rehashes outdated hashes; refresh tokens rotate with reuse
detection; ownership is enforced inside queries; secrets are never logged; the
API docs are off in production.

### Known gaps

- **No payment provider.** Plans are granted with `scripts.grant_plan`; nothing
  takes money yet. See [Subscriptions](#subscriptions).
- **Not deployed.** There is no container image or hosting configuration yet;
  see [Deployment](#deployment).
- **Nothing has run against a live model.** Every number in this README uses a
  fake one. The first steps with a key: `python -m app.evaluation answers
  --judge` and `retrieval`, then read the run traces.
- **The evaluation labels are unreviewed**, and the dataset is smaller than the
  PRD's 50–100 cases.
- **Evaluation endpoints** (`/api/v1/evaluations`) need an operator role, which
  does not exist.
- **Only conversation turns are traced**; onboarding, analysis and worker jobs
  are not.
- **Caching** was measured and deferred; revisit with live-model traces.
- **A client that disconnects mid-stream** leaves no run and stores nothing; that
  path is not tested, because the test client cannot simulate a disconnect.

### Semantic retrieval

Memories are embedded when they are written and recalled by meaning when a
message arrives, so what the user said in one conversation is available in the
next.

```text
START → retrieve_memories → build_context → generate_reply → END
```

Retrieval runs *before* the context is assembled rather than inside it, so
everything the model will see — profile, recalled memories, recent turns —
competes for one budget in one place.

Every search is scoped three ways, and each has a test:

- to the user, so nothing crosses accounts
- to memories that are currently usable, so a superseded or expired belief is
  never recalled
- to vectors produced by the **model now configured**, because distances across
  embedding models look plausible and mean nothing

That last one is why each row stores the model it was embedded with. Changing
`EMBEDDING_MODEL` makes existing rows identifiable rather than silently wrong;
they stop being recalled until re-embedded.

Retrieval failing costs personalisation, not the answer: a failed query embedding
is logged and the reply is generated from the profile and conversation anyway.
A memory whose embedding failed is still stored, still listed, and still the
user's — it is simply not findable by meaning until backfilled.

Deduplication now prefers vector similarity over token overlap at the same seam
it was built behind, so a paraphrase is recognised as a restatement. Token
overlap remains the fallback when either side has no vector, because a failed
embedding should make deduplication worse, not absent.

#### No ANN index yet, deliberately

Every search is filtered to one user, and an HNSW index scanned *before* that
filter can return fewer rows than exist for that user — the filtered-recall
problem. At a few hundred memories per user an exact scan over the filtered
subset is both fast and always correct. Revisit with pgvector iterative scans
when measurement shows the exact scan is the bottleneck, not before.

#### The similarity threshold is a guess

`MEMORY_SIMILARITY_THRESHOLD` has never been calibrated against a real embedding
model, because no run in this repository has used one. Cosine similarity floors
differ substantially between models — what counts as "unrelated" for one is a
strong match for another — so this number should be tuned against measured
retrieval quality before it is trusted. It is exactly what the evaluation phase
is for.

### Memory

Most exchanges are worth remembering nothing from, and the design starts there.
Extraction runs **in the background worker** — nothing in the response depends
on it, so making the user wait would add latency for no visible benefit. See
[Background processing](#background-processing).

```text
extract_candidates → filter_candidates ─┬─ nothing survived → END
                                        └─ resolve_conflicts → END
```

**Cheap filtering happens before expensive comparison.** A candidate below the
importance or confidence floor never reaches a second model call. Both floors
matter and for different reasons: an unimportant memory dilutes every later
retrieval, and a low-confidence one is a guess about the user, which is worse
than having no memory of them.

**Comparison is narrowed deterministically before a model sees anything.** Two
selections, for two different failure modes:

- *lexical overlap* catches restatements of the same sentence. Anything above the
  duplicate threshold is settled without a model call at all.
- *same-type recency* catches disagreements phrased in entirely different words.
  "Move into AI engineering" and "stay in mobile development" contradict each
  other while sharing no vocabulary, and that is precisely the case superseding
  exists for.

Without this narrowing, every extraction would mean showing a model the user's
entire history. In the end-to-end run below, four exchanges cost four extraction
calls and **one** judgement call.

The lexical similarity is a placeholder for vector similarity, which Phase 8
brings; it will catch paraphrases this cannot. The pipeline around it does not
change when that happens.

**A changed mind supersedes rather than overwrites.** The old memory keeps its
content, gains `SUPERSEDED` status and a pointer to what replaced it, so what the
user used to believe stays answerable. Every change — created, reinforced,
updated, superseded, archived — is appended to `memory_versions`.

Hearing something twice **reinforces** it rather than storing it twice, taking
the maximum of the two scores: a second statement is evidence, and should never
weaken what was already believed.

Memories carry a durability, and temporary context is given an expiry, so "I am
on a train right now" stops shaping answers. Type overrides the model's choice
where the type settles it — a stated goal is long-term however the exchange was
phrased.

Users can list, correct, archive and delete what is held about them. `DELETE`
**actually removes the row and its history**: PRD 18 lists `DELETED` as a
lifecycle state, but a tombstone still holding the content is not a deletion, and
PRD 32 puts this under privacy. `ARCHIVED` covers "keep it, stop using it".
Supersession cannot be asserted by a client — it is a conclusion the system draws
from a contradiction.

`MEMORY_EXTRACTION_ENABLED=false` turns extraction off: no jobs are queued.

### Conversations

`POST /chat` appends a message, generates a reply, and stores both. Omitting
`conversation_id` starts a new conversation, named after its first message —
derived deterministically rather than generated, because a title is not worth a
model call or one more thing that can fail.

**Message order is an explicit column, not a timestamp.** A question and its
answer are written in one transaction, so `now()` gives them the same value and
the conversation could read back answer-first. A `position` per conversation,
unique with the conversation id, makes the order unambiguous and stops two
simultaneous sends interleaving silently.

**What the model sees is bounded on two axes** — a message count and a character
budget — because either alone is escapable: a count says nothing about length,
and a budget alone would admit a hundred one-word turns. History is selected
newest-first, then restored to chronological order so the model reads the
exchange as it happened. The message being answered is never dropped, only
truncated: replying to a question the model was not shown is worse than replying
to a shortened one. When older turns are omitted the prompt says so, so the model
treats its view as partial rather than assuming the conversation began where its
window does.

A conversation belonging to someone else reports **404, not 403** — a distinct
"forbidden" would confirm it exists. Ownership is part of the query rather than a
check afterwards, so there is no path that reads first and authorises second.

If the model is unreachable the exchange is still recorded, with a fallback reply
and `degraded: true`. Losing the user's message because generation failed would
be the worse outcome.

The response also carries `grounded`: `true` when verification found nothing
unsupported, `false` when the best draft still contained unsupported statements,
and `null` when the reply was not verified. It is not stored with the message, so
it describes the response it arrives in, not the conversation when re-read.

Both list endpoints are paginated (`page`, `page_size`, max 100). Reading one
conversation returns the conversation as an object, with the pagination applying
to its messages.

### Initial analysis

A second graph turns what is known about a user into an analysis:

```text
START → build_evidence → generate_analysis → verify_grounding ─┬─ good enough → END
                                                               └─ retry with feedback ─┘
```

**Every statement carries a claim type** — `fact`, `observation`, `inference`,
`recommendation`, `uncertainty` — and cites the evidence it rests on by id.
Presenting an inference as an established fact is the failure mode that makes a
personal assistant untrustworthy, so the distinction is not left to the model's
good intentions:

- a claim citing an evidence id that does not exist is **dropped**
- a fact, observation, inference or recommendation with **no** citation is dropped
- a `fact` below the confidence floor is dropped — hedged statements are not facts
- only `uncertainty` may cite nothing, because it is a claim about what is absent

Unsupported claims are dropped rather than relabelled: quietly downgrading an
invented fact to an inference would keep the invention in the analysis.

If too few claims survive, the graph retries **once**, telling the model exactly
which rejections occurred. The attempt ceiling is the termination condition — a
model that keeps citing evidence that does not exist would otherwise loop at
unbounded cost. When the budget runs out the analysis is returned anyway, marked
`degraded` so a client can present it with appropriate caution rather than
treating a thin reading as a confident one.

Generating with nothing to cite is refused outright (`409 INSUFFICIENT_CONTEXT`)
before any model call: an analysis with no evidence could only be invention.

Each analysis stores a **snapshot of the evidence it was generated from**, so its
citations keep resolving to what they meant even after the user edits their
profile. Reading the live profile later would silently misattribute claims.

### Working with the model

Every model call goes through one interface, `LLMProvider.generate_structured`.
Structured output is the only operation: free-form text would have to be parsed
before it could be trusted. Provider and framework exceptions are translated at
the infrastructure boundary, so no application or graph code imports an SDK.

Calls are bounded on both axes — a per-attempt timeout and a maximum attempt
count. Credential rejections are never retried.

Prompts live in `infrastructure/llm/prompts/`, carry a name and a version, and
are the only place prompt text appears.

#### Untrusted content

Everything the user typed enters a prompt inside an explicit fence, and the
system message states that content within it is data to analyse, never
instructions to follow. The fence sentinels are stripped from the content first —
without that, a user could type the closing sentinel and have what follows read
as though the system wrote it. Model-generated text, such as an earlier question,
is fenced on the same basis.

Structured output is the second layer: even a successful injection can only
produce profile content, never a tool call or a free-form instruction.

### Transactions

The request-scoped session dependency does **not** commit. Transaction
boundaries belong to application services, which know what a complete use case
is; leaving the session's scope rolls back anything uncommitted.

A chat turn uses two short transactions around its model calls, never one long
one: a read-only one in `prepare_turn`, committed before the graph runs, and the
write that stores the exchange, its run record and its extraction job together.
A new conversation is created in that second transaction, so a turn that fails
leaves nothing behind.

### API contracts

Successful responses:

```json
{ "data": { }, "meta": { "request_id": "…" } }
```

Errors:

```json
{ "error": { "code": "NOT_FOUND", "message": "…" }, "meta": { "request_id": "…" } }
```

Error codes are stable and machine-readable. Stack traces and internal details
are never returned to clients.

### Correlation ids

Every request is assigned a `request_id`, returned in the `X-Request-ID`
response header and in `meta.request_id`, and attached to every log record. An
inbound `X-Request-ID` is reused only when it matches a conservative token
pattern, since it is attacker-controlled input that reaches log storage.

### Verifying against a live model

The OpenAI adapter has never been exercised against the real API from this
repository. Its failure paths are covered — missing and rejected credentials,
bounded retries, timeouts, wrong-typed responses — but a successful live call is
not. To check it yourself:

```bash
OPENAI_API_KEY=sk-... uv run python -c "
import asyncio
from app.core.config import get_settings
from app.infrastructure.llm.openai_provider import OpenAIStructuredProvider
from app.infrastructure.llm.prompts.onboarding import build_extraction_prompt
from app.domain.onboarding.context import ExtractionResult

async def main():
    provider = OpenAIStructuredProvider(get_settings())
    prompt = build_extraction_prompt(
        'What do you want to achieve?',
        'I want to move into AI engineering, but I only have evenings free.',
    )
    print((await provider.generate_structured(prompt, ExtractionResult)).model_dump_json(indent=2))

asyncio.run(main())
"
```

Confirm `LLM_MODEL` names a model your account can reach before relying on it.

---

## Roadmap

Each phase is a coherent, testable milestone.

| Phase | Scope                                                       |
| ----- | ----------------------------------------------------------- |
| 1 ✅  | FastAPI foundation, configuration, logging, health checks    |
| 2 ✅  | PostgreSQL, Alembic migrations, session management, `/ready` checks |
| 3 ✅  | Authentication, user and profile domain                      |
| 4 ✅  | Adaptive onboarding graph                                    |
| 5 ✅  | Initial profile analysis                                     |
| 6 ✅  | Conversation infrastructure                                  |
| 7 ✅  | Memory extraction and persistence                            |
| 8 ✅  | Semantic memory retrieval (pgvector)                         |
| 9 ✅  | Temporal and structured retrieval                            |
| 10 ✅ | Hybrid retrieval and reranking                               |
| 11 ✅ | Agentic retrieval and evidence grading                       |
| 12 ✅ | Answer verification with bounded retries                     |
| 13 ✅ | SSE streaming                                                |
| 14 ✅ | LangGraph persistence and execution tracing                  |
| 15 ✅ | Background processing; caching measured and deferred          |
| 16 ✅ | Evaluation framework                                         |
| 17 ✅ | Performance, reliability and security hardening              |

Dependencies are added when the phase that needs them begins, not upfront.
