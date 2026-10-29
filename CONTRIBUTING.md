# Contributing

Thanks for helping. The most valuable contributions right now are **honest measurements and new test cases** (see "Try it and try to break it" in the [README](README.md)); code and doc fixes are just as welcome.

## Data rules (please read first)

- **Never** commit or post real personal or customer data: no real IDs, bank statements, invoices, company records, names or account numbers. Use the generator (`backend/scripts/make_bundle.py`) or documents you are licensed to share and have anonymised.
- Never commit keys, tokens, passwords, `.env` files, hostnames or addresses of your own machines. `.env` and `deploy/.env.production` are gitignored; keep it that way.

## Set up

```bash
uv sync                                   # Python 3.12, managed by uv only (no pip, no hand-edited uv.lock)
uv run pytest                             # offline; no key, no network
uv run ruff check . && uv run ruff format --check .
cd frontend && npm ci && npm run lint && npx tsc --noEmit && npm test
```

CI runs the same commands. `uv run python backend/scripts/gen_check_catalogue.py --check` must pass if you add or change a check (it regenerates `docs/check-catalogue.md`; drop `--check` to write it).

## Ground rules for code

- Registries auto-discover: add a file, do not edit an index (a check goes under `backend/app/checks/` with `@check(...)`, a router under `backend/app/api/` exposing `router`).
- Checks never read the clock; use the injected `CaseContext.today`. Amounts are `Decimal`, never `float`.
- Text extracted from documents is untrusted: never put it into paths, SQL, shell commands or HTML.
- Every flag is made with `make_flag`, carries evidence, and never changes after scoring.
- Model calls go through `LLMClient`; tests use `FakeLLMClient`. No network in tests.
- Nothing is approved, rejected or escalated automatically. A change that makes the tool decide on its own will not be accepted.
- Do not add or upgrade dependencies casually: `pyproject.toml` and `uv.lock` changes need a reason in the PR.
- Any new route under `frontend/src/app/api/` must call the gate (`decide()` in `frontend/src/lib/auth/gate.ts`).

## Adding a tamper or a benchmark

1. Add the tamper to `backend/scripts/_eval/tamper_defs.py` with the rule you expect to fire (or mark it as an expected miss and say why).
2. Run `uv run python backend/scripts/eval.py --suite recall --tamper <ID>` and put the numbers in your PR or in a *Test result* issue.
3. Report **misses and false alarms**, not only the good numbers. A documented miss is a contribution.

The definitions of the metrics are in [docs/test-data-and-eval.md](docs/test-data-and-eval.md).

## Pull requests

Keep them small and focused; say what you ran. Add or update a test for behaviour you change. Docs changes should keep the plain, specific style of the existing docs. If you changed the frontend, check it at phone width (390 px) and desktop (1440 px).

By contributing you agree that your contribution is licensed under the repository's license.
