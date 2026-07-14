# iMAX

iMAX is a single React and FastAPI application for three internal securities
workflows: grounded manual search, guarded NL2SQL analysis, and expense proposal
automation.

## Local setup

```bash
make bootstrap
make dev
```

Development UI: `http://127.0.0.1:5173`

For the single-origin demo build:

```bash
make start
```

Demo UI and API: `http://127.0.0.1:8000`

Real credentials belong only in the ignored root `.env`. The SQLite databases,
LangGraph checkpoints, audit logs, and uploaded evidence files are generated at
runtime and are not committed.

## Structure

- `apps/web`: React 19, Vite, responsive iMAX interface, and ECharts results.
- `apps/api`: FastAPI routes, LangGraph supervisor, and the existing NL2SQL package.
- `docs`: product, design, and migration documentation.

The pre-React code is recoverable from the remote tag
`pre-react-unification-20260714`.
