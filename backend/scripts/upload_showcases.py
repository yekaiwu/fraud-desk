"""Post the showcase cases (make_showcase_bundles.py) to a running backend and wait for each analysis.

    uv run python backend/scripts/upload_showcases.py --dir showcases [--base http://127.0.0.1:8000]

Authenticates the way the frontend proxy does (GLDB_PROXY_SECRET from the environment or the production
env file). Skips a reference that already exists (or replaces it with --replace), so it can be run twice. Prints one line per case with
the lane, band and flags that fired; never prints the secret.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

DEFAULT_ENV = Path("~/.local/gldb-fraud/deploy/.env.production").expanduser()


def _secret(env_file: Path) -> str:
    if os.environ.get("GLDB_PROXY_SECRET"):
        return os.environ["GLDB_PROXY_SECRET"]
    for line in env_file.read_text().splitlines():
        m = re.match(r"^GLDB_PROXY_SECRET=(.*)$", line)
        if m:
            return m.group(1).strip().strip("\"'")
    raise SystemExit("GLDB_PROXY_SECRET not found in the environment or " + str(env_file))


def _call(
    base: str,
    secret: str,
    method: str,
    path: str,
    body: bytes | None = None,
    ctype: str | None = None,
):
    headers = {"x-gldb-proxy-secret": secret}
    if ctype:
        headers["content-type"] = ctype
    req = urllib.request.Request(base + path, data=body, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return r.status, json.loads(r.read() or b"null")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()[:300]


def _multipart(fields: dict[str, str], files: list[Path]) -> tuple[bytes, str]:
    boundary = uuid.uuid4().hex
    out = bytearray()
    for k, v in fields.items():
        out += f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode()
    for p in files:
        out += (
            f'--{boundary}\r\nContent-Disposition: form-data; name="files"; filename="{p.name}"\r\n'
            "Content-Type: application/octet-stream\r\n\r\n"
        ).encode()
        out += p.read_bytes() + b"\r\n"
    out += f"--{boundary}--\r\n".encode()
    return bytes(out), f"multipart/form-data; boundary={boundary}"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dir", type=Path, required=True)
    ap.add_argument("--base", default="http://127.0.0.1:8000")
    ap.add_argument(
        "--replace", action="store_true", help="delete and redo a reference that exists"
    )
    ap.add_argument("--env-file", type=Path, default=DEFAULT_ENV)
    a = ap.parse_args(argv)
    secret = _secret(a.env_file)
    manifest = json.loads((a.dir / "showcases.json").read_text())
    st, existing = _call(a.base, secret, "GET", "/cases")
    if st != 200:
        raise SystemExit(f"GET /cases failed: {st}")
    rows = (
        existing if isinstance(existing, list) else existing.get("cases", existing.get("items", []))
    )
    have = {r.get("reference"): (r.get("id") or r.get("case_id")) for r in rows}
    for sc in manifest:
        ref = sc["reference"]
        if ref in have:
            if not a.replace:
                print(f"{ref}: already there, skipped (--replace to redo it)")
                continue
            q = "?reviewer=showcase-loader&reason=replace+showcase"
            st, _ = _call(a.base, secret, "DELETE", f"/cases/{have[ref]}{q}")
            print(f"{ref}: removed the old copy ({st})")
        fields = {"use_case": sc["use_case"], "reference": ref}
        for k in ("applicant_name", "applicant_uen"):
            if sc.get(k):
                fields[k] = sc[k]
        body, ctype = _multipart(fields, [a.dir / f for f in sc["files"]])
        st, res = _call(a.base, secret, "POST", "/cases", body, ctype)
        if st != 202:
            print(f"{ref}: upload failed {st} {res}")
            continue
        # one at a time: the open ACRA service rate-limits parallel lookups
        job: dict = {}
        for _ in range(240):
            st, job = _call(a.base, secret, "GET", "/jobs/" + res["job_id"])
            if st == 200 and job.get("state") in {"succeeded", "failed"}:
                break
            time.sleep(2)
        st, c = _call(a.base, secret, "GET", "/cases/" + res["case_id"])
        case = c.get("case", c) if isinstance(c, dict) else {}
        rules = sorted({f.get("rule") for f in case.get("flags", []) if f.get("rule")})
        score = case.get("score") or {}
        print(
            f"{ref}: {job.get('state')} lane={case.get('lane')} band={score.get('band')} "
            f"partial={score.get('partial')} flags={rules}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
