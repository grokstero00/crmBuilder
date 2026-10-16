# crmbuilder

A SaaS for the CIS market. An integrator, freelancer or in-house IT person gives a brief (ТЗ)
and the service designs, configures, checks and documents a Bitrix24 portal, and later
maintains it. The core idea is "portal configuration as code": a YAML spec of the portal plus
import → plan → apply → rollback, with AI design, audit, templates and a client report on top.

## Layout

- `src/crmbuilder/`: the engine and the CLI (`crmbuilder import|plan|apply|rollback|design|audit|report|template|validate`).
  - `spec/models.py`: pydantic spec; objects have stable `key`s, Bitrix IDs live in the state file.
  - `importer/`: portal → spec + state. `plan.py`: spec diff. `apply.py`: plan → REST calls.
  - `design.py`: Claude tool loop editing the spec. `audit.py`, `report.py`, `automation.py` (robots checklist).
  - `bitrix/client.py`: webhook REST client, read-only unless `read_only=False`.
- `backend/` (package `crmbuilder_web`): FastAPI + SQLAlchemy + Alembic (migrations in the
  package, applied by `crmbuilder-web` at start). It imports the engine; it never reimplements it.
  `runs.py` carries out apply/rollback in a background thread (`app.state.run_executor`; tests
  run it inline) with progress in the `runs` table. Writes need `Project.allow_write`, the typed
  portal name and the plan `fingerprint` the person reviewed. `jobs.py` runs design/audit/report
  the same way (table `jobs`); a design waiting for answers polls its row until they arrive.
- `frontend/`: Nuxt 4 SPA with `@bitrix24/b24ui-nuxt` (components are `B24*`). `/api/*` is
  proxied by `server/api/[...path].ts` to `NUXT_API_URL`. Use pnpm, not npm (npm fails on b24ui peers).
- `tests/`: engine tests; `tests/web/`: API tests. `tests/fake_bitrix.py` is an in-memory
  Bitrix24 that follows the REST docs; extend it rather than mocking calls ad hoc.
- `docker-compose.yml`: `web`, `api`, `db` (postgres) start with `up`; `cli` is in profile `cli`.

## Commands

```bash
uv sync && uv run pytest && uv run ruff check . && uv run ruff format --check .
cd frontend && pnpm install && pnpm typecheck && pnpm build
```

## Bitrix24 facts that shape the code

- Stage semantics: process / success (exactly one) / failure; a stage cannot change group.
- New pipelines and smart processes get default stages from Bitrix; apply reconciles them.
- REST cannot read or place stage robots/triggers: they live in `automation` as a manual
  checklist (carried over on import/rollback, instructions in the report).
- REST docs source: `raw.githubusercontent.com/bitrix24/b24restdocs/main/api-reference/...`.

## Working with the owner

- The owner writes in Russian; answer in Russian. UI text and user-facing API errors are Russian;
  code, comments, commits and CLI output are English.
- Test portal: `crmbuilder.bitrix24.ru`. Never touch the production portal `crm.atv.kz`.
- The owner runs Windows + Git Bash + Docker Desktop. GitHub works there only through a local
  VPN proxy (`git config --global http.proxy`); on the work PC an antivirus inspects HTTPS
  (`BITRIX_CA_BUNDLE=/workspace/ca.pem`).
- Work goes through PRs to `main`; the owner merges and tests live, then reports back.
