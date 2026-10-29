# Security

This is a research prototype that processes **fictitious** data. It is not hardened for real customer documents (see "Honest limits" in [docs/deploy.md](docs/deploy.md)). Security reports are still very welcome: the code is public so that it can be reviewed.

## Reporting a vulnerability

Please report privately, not in a public issue: use GitHub's **"Report a vulnerability"** button on the *Security* tab of this repository. Include what you found, how to reproduce it (a request, a file, a command), and what you think the impact is. We aim to acknowledge within a few days. Please do not test against anyone else's deployment, and do not use real personal data.

## In scope

The backend (`backend/app`), the frontend gate and proxy (`frontend/src/proxy.ts`, `frontend/src/app/api`, `frontend/src/lib/auth`), the upload and render paths, and the deployment scripts in `deploy/`. Examples: authentication or authorisation bypass, path traversal, injection, request smuggling through the `/api` proxy, secrets leaking into logs or responses, memory or disk exhaustion from a crafted file, prompt injection that changes state.

## Known limits (not vulnerabilities)

One shared login with self-declared reviewer names; in-memory rate limits; unencrypted storage under `data/`; no roles. These are documented and by design for a demo.

## Secrets

If you find a credential in this repository or its history, please report it the same way. Nothing in the repository is meant to be a real secret; the values in tests and examples are fakes.
