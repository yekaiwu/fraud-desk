# Self-hosting the app

How to run the whole tool (FastAPI backend and Next.js frontend) on an always-on machine and put a shared-login gate in front of it. The scripts in `deploy/` target **macOS** (two `launchd` agents); on Linux, run the same two commands under systemd or any supervisor (see "Not on macOS" at the end).

**Fictitious data only.** The shared login, the rate limits and the dollar caps below are a demo-grade gate, not a security boundary for real customer documents. Do not upload real IDs, bank statements or company records, and do not put the app on the public internet without reading "Honest limits".

## 1. How it fits together

```
browser --https--> your HTTPS front (reverse proxy, tunnel or tailnet) --> 127.0.0.1:3000  Next.js  (login gate, /api proxy, security headers)
                                                                                |  X-Gldb-Proxy-Secret + X-Forwarded-For
                                                                                v
                                                                          127.0.0.1:8000  FastAPI  (proxy-secret check, rate limits, job cap)
                                                                                |
                                                                          optional LLM (hosted API or your own OpenAI-compatible server)
```

- Next is the only entry point. Both services bind to `127.0.0.1`; whatever publishes the app forwards one local port (3000).
- The browser only talks to same-origin `/api/*`. A production build defaults `NEXT_PUBLIC_API_BASE_URL` to `/api` (local dev keeps `http://localhost:8000`; mock mode `NEXT_PUBLIC_USE_MOCK=1` needs no backend).
- **Gate.** Every page and every `/api` route needs a valid session cookie, except `/welcome` (a public product tour with static copy and fictitious sample data), `/login`, `/api/auth/login`, `/api/auth/logout`, `/robots.txt` and the static build files. Pages are gated by `frontend/src/proxy.ts`; `/api` is gated inside its own route handler (`frontend/src/app/api/[...path]/route.ts`) because a Next proxy would buffer and truncate request bodies over 10 MB, which breaks 25 MB uploads. Both call the same `decide()` in `frontend/src/lib/auth/gate.ts`. **Any route you add under `/api` must call it too.**
- **Sign-in.** One shared username and password (`GLDB_SITE_USERNAME`, `GLDB_SITE_PASSWORD`). The cookie is HMAC-SHA256 signed with `GLDB_SESSION_SECRET`, `HttpOnly`, `SameSite=Lax`, `Secure` when `X-Forwarded-Proto` is https, 12 h expiry. Changing the password or the secret signs everybody out.
- **Login limits.** 5 failures per 10 minutes per client address (first hop of `X-Forwarded-For`), plus a global backstop of 60 failures per 10 minutes in case an address can be forged. In memory: a frontend restart clears it.
- **Fail closed.** In production, if the username (under 3 characters), password (under 12) or session secret (under 32) is missing or short, every page and API call returns a 503 setup-error page and nothing else.
- **Backend.** `backend/app/access.py` rejects any request without the matching `X-Gldb-Proxy-Secret` (401), except `GET /health` from a loopback peer; limits `POST /cases` to `GLDB_RATE_CASES_PER_HOUR` and review POSTs to `GLDB_RATE_REVIEWS_PER_MIN` per client (429 with `Retry-After`); and answers 503 `busy` to analysis-starting requests while `GLDB_MAX_ACTIVE_JOBS` jobs are queued or running (a soft cap: checked, not reserved).
- **Headers.** `X-Content-Type-Options`, `X-Frame-Options: DENY`, `Referrer-Policy: no-referrer`, `Permissions-Policy`, `Cross-Origin-Opener-Policy`, HSTS, `X-Robots-Tag: noindex`, and in production a Content-Security-Policy (`default-src 'self'`, inline scripts allowed because Next inlines its bootstrap, `frame-ancestors 'none'`). `/robots.txt` disallows everything.

## 2. Requirements

`git`, [`uv`](https://docs.astral.sh/uv/), Node 20 or newer with `npm`, `openssl`, `lsof` and `curl`. Install into a folder that is **not** under `~/Desktop`, `~/Documents` or `~/Downloads` (macOS privacy protection stops background services reading them; `install.sh` refuses to run there). `~/.local/gldb-fraud` is a good choice.

## 3. Install

```bash
git clone <this repo's URL> ~/.local/gldb-fraud
cd ~/.local/gldb-fraud
uv sync                                                                    # Python 3.12 and the locked dependencies
uv run python backend/scripts/make_bundle.py --scenario all --variant all --out samples   # fictitious demo bundles + registry (a few minutes)
deploy/install.sh                                                          # build, start both launchd agents on loopback
```

`samples/registry.json` is the mock company registry that the ownership and director checks read. Optionally load ten ready-made demo cases into the worklist: `deploy/load-demo-data.sh` (it backs up your data folder first).

The first run creates `deploy/.env.production` (mode 600, gitignored) with the username `officer`, a random password, a session secret and a proxy secret, and **prints the password once**. Write it down; it is also in the file. To choose your own, edit the file (keep mode 600) and restart (section 8). `install.sh` then:

- refuses to run unless `uname` is Darwin and the checkout is outside Desktop, Documents and Downloads;
- runs `uv sync`, `npm ci` and `npm run build`;
- checks that ports 8000 and 3000 are free;
- installs `~/Library/LaunchAgents/com.jay.gldb-backend.plist` and `...gldb-frontend.plist` (RunAtLoad, KeepAlive, logs in `~/Library/Logs/gldb/`) and bootstraps them with `launchctl bootstrap gui/$(id -u)`;
- checks the gate: the backend must answer 401 without the proxy secret.

Re-running it is safe: it keeps `deploy/.env.production` and `data/`. `deploy/status.sh` shows agent state, local health, listeners and the publishing state. `deploy/uninstall.sh` removes the agents (it keeps `data/` and the env file).

Launch agents (the `gui/` domain) start when the user is logged in. If the disk is encrypted (FileVault), macOS cannot log in automatically, so after a reboot log in once and the agents start by themselves.

## 4. Check it locally

```bash
deploy/status.sh
curl -s -o /dev/null -w '%{http_code} %{redirect_url}\n' http://127.0.0.1:3000/            # 303 to /welcome
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:3000/api/cases                    # 401
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8000/cases                        # 401 (no proxy secret)
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8000/health                       # 200 from loopback
lsof -nP -iTCP -sTCP:LISTEN | grep -E ':(3000|8000) '                                       # 127.0.0.1 only
# limiter: six wrong logins from one made-up client address end in 429
for i in 1 2 3 4 5 6; do curl -s -o /dev/null -w '%{http_code} ' -X POST http://127.0.0.1:3000/api/auth/login \
  -H 'content-type: application/json' -H 'X-Forwarded-For: 203.0.113.250' -d '{"username":"officer","password":"wrong-password-test"}'; done; echo
```

## 5. Choose the model

Settings go into `deploy/.env.production`; afterwards restart the backend: `launchctl kickstart -k gui/$(id -u)/com.jay.gldb-backend`. Never paste a key into a chat, a ticket or a commit.

- **No model (default).** A built-in fake client: digital PDFs are fully checked from their text layer; scanned or image-only pages are reported as unreadable and the score is marked partial.
- **Anthropic Claude Haiku 5.5** (paid, vision; page text and images leave the machine, so fictitious documents only): `GLDB_LLM_BACKEND=anthropic`, `GLDB_ANTHROPIC_MODEL=claude-haiku-5-5`, your key in `GLDB_ANTHROPIC_API_KEY`, and your workspace id in `GLDB_ANTHROPIC_WORKSPACE_ID` if the key is user-scoped. Set the caps: `GLDB_MAX_USD_PER_CASE` and `GLDB_MAX_USD_PER_DAY`, and a monthly limit in the Anthropic Console as the hard stop. Smoke test (one tiny call; never prints the key): `set -a; . deploy/.env.production; set +a; uv run python backend/scripts/anthropic_smoke.py`. Details: [anthropic-haiku.md](anthropic-haiku.md).
- **Your own OpenAI-compatible server** (for example a local model): `GLDB_LLM_BACKEND=openai`, `GLDB_LLM_BASE_URL`, `GLDB_LLM_API_KEY` and the model names; see [local-llm.md](local-llm.md) and `.env.example`. Keep that server on loopback or a private network; never forward it.

Every variable is listed in `deploy/env.production.example` and `.env.example`.

## 6. Publish it (optional)

Until you do this the app is reachable only from the machine itself. Use any HTTPS front that forwards to `127.0.0.1:3000` and sets `X-Forwarded-For` and `X-Forwarded-Proto`: a reverse proxy (Caddy, nginx), a tunnel (Cloudflare Tunnel, ngrok) or Tailscale.

- **Inside a tailnet only:** `tailscale serve --bg 3000` makes it reachable to your own devices and nobody else.
- **Tailscale Funnel (public internet):** `deploy/install.sh --funnel` runs `tailscale funnel --bg 3000`. It needs, in the Tailscale admin console, MagicDNS and HTTPS certificates enabled and the `funnel` attribute granted to the node (`"nodeAttrs": [{ "target": ["autogroup:member"], "attr": ["funnel"] }]`). Funnel serves ports 443, 8443 and 10000 only, you cannot set bandwidth limits, and the `ts.net` name is published in Certificate Transparency logs, so assume it is discoverable.
- **Anything public:** anyone who learns the URL can reach the login page and try passwords. Check "Honest limits" and the checklist first.

Turn it off again: `deploy/uninstall.sh --funnel-off` (removes the agents and runs `tailscale funnel reset`, which wipes **all** serve/funnel config on that node, so use `tailscale funnel --https=443 off` instead if the node serves other things).

## 7. Keep the machine awake

System Settings > Energy: prevent automatic sleeping, wake for network access, start up after a power failure. Or `sudo pmset -a sleep 0 disksleep 0 displaysleep 10 womp 1 autorestart 1`. `caffeinate -dimsu &` is a stop-gap for testing only.

## 8. Update, rotate, restart

```bash
cd ~/.local/gldb-fraud && git pull --ff-only && deploy/install.sh    # re-syncs, rebuilds, restarts; keeps the env file, data/ and publishing as they are
```

If `git pull` reports local changes, stop and look at them; do not `reset --hard`.

To rotate the password or the secrets, edit `deploy/.env.production` (keep mode 600), then restart:

```bash
launchctl kickstart -k gui/$(id -u)/com.jay.gldb-frontend    # site username/password, session secret
launchctl kickstart -k gui/$(id -u)/com.jay.gldb-backend     # proxy secret
```

A new password or session secret signs everybody out. A new proxy secret needs **both** agents restarted (they must match). Generate values with `openssl rand -base64 24 | tr -d '/+='` (password) and `openssl rand -hex 32` (secrets).

## 9. Honest limits

- **Reviewer identity is self-declared.** One shared login means the reviewer name in the audit log is whatever the user typed. The second-approver control therefore only separates two typed names; it does not prove two different people.
- Anyone with the password can upload, review and delete cases. There are no roles.
- Rate limits and the job cap are in memory in one process; they reset on restart.
- The client address comes from `X-Forwarded-For`. If your front passes a client-supplied header through, an attacker can pick the address the per-address limits key on; the global login backstop and the job cap still bound the damage.
- No TLS between your front and Next (loopback). Uploaded documents are stored unencrypted under the data directory. SQLite, one process; jobs are not durable across restarts.
- Everything extracted from a document is untrusted text from the person being checked; the tool never auto-approves or auto-rejects.

## 10. Pre-flight checklist

Before you publish:

- [ ] `deploy/status.sh`: both agents running; backend `/cases` without the secret is 401; frontend `/` is 303; listeners only on `127.0.0.1`.
- [ ] `deploy/.env.production` is mode 600, not in git, not in a backup others can read; password at least 12 characters, session secret at least 32, proxy secret at least 16.
- [ ] Six wrong logins return 429 on the sixth.
- [ ] Every sample and anything uploaded is fictitious.
- [ ] If a paid model is on, the daily cap is set (and a monthly limit in the provider's console).
- [ ] The security headers are present: `curl -sI https://<your-host>/login`.
- [ ] You know how to switch it off, and who has the password.
- [ ] Reviewers understand that names in the audit log are self-declared.

## Not on macOS

The two services are plain commands; `deploy/run-backend.sh` and `deploy/run-frontend.sh` show the exact environment and arguments (they read `deploy/.env.production`). Run them under systemd, a container or any supervisor, bind both to `127.0.0.1`, and keep the same environment variables. `deploy/install.sh` and the launchd templates are the only macOS-specific parts.
