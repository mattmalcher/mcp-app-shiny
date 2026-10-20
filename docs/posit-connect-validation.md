# Posit Connect validation runbook

**Status: pending.** Nobody has run this on a real Posit Connect server yet.
The recipe in [deployment.md](deployment.md) has been rehearsed locally
(`make rehearse-connect`: clean-venv installs, the three entry points, the
environment variables, and every check below against `localhost` /
`127.0.0.1`). Until this runbook passes on Connect, the documentation must not
call the Connect recipe verified.

This runbook is written for a coding agent (or a person) with access to a
Connect server. Everything that could be prepared without Connect already
exists in the repository; the work left is deploying, observing, and fixing
what Connect does differently.

## Starting prompt

> You have access to a Posit Connect server. In the `mattmalcher/mcp-app-shiny`
> repository, follow `docs/posit-connect-validation.md` from the top. First
> inspect what you have: Connect URL and API key, publisher rights, whether a
> second hostname (sandbox alias) exists and has TLS, `rsconnect-python`, and a
> browser for Playwright. Deploy the three bundles with `make connect-bundles`,
> run every check in the runbook with `scripts/check_deployment.py`, and record
> the evidence it asks for. If a check fails, diagnose it with the "Failure
> diagnosis" section and fix it, but only within the authorised scope: the
> three temporary content items and this repository's code and docs; do not
> change server-wide Connect settings, DNS or certificates yourself (write
> down what an administrator must change). Keep the sandbox on its own
> hostname and keep all security defaults; do not implement the opaque-origin
> alternative. Finally update `docs/deployment.md` ("What has been verified",
> the status note, and any corrected commands) and this runbook's "Results"
> section with what you observed, remove the temporary content, and summarise
> what passed, what failed and what remains open.
>
> Once the three-content baseline is validated, evaluate serving Shiny and the
> sandbox from one ASGI application, accessed through separate hostnames (see
> "Next step: one application, two hostnames"). Keep the three-content
> deployment as the baseline until the combined arrangement is validated. A
> separate process is a deployment choice, not the browser isolation
> requirement.

## 1. Prerequisites

| Need | How to check | If missing |
|---|---|---|
| Connect server URL and a **publisher** API key | `curl -s -H "Authorization: Key $CONNECT_API_KEY" "$CONNECT_SERVER/__api__/v1/user"` returns your user with `"user_role": "publisher"` or `"administrator"` | Ask the Connect administrator |
| Connect runs Python ASGI APIs (the "FastAPI" content type) and Shiny for Python, with Python ≥ 3.11 | The Python versions offered in the dashboard's publishing docs, or the first deploy's log (rsconnect reports a missing match) | Administrator: install Python ≥ 3.11 |
| **Sandbox alias**: a second hostname routed to the same server, e.g. `connect-sandbox.example.com` (DNS alias, or a load-balancer rule that forwards it) | `getent hosts connect-sandbox.example.com` resolves; `curl -sI https://connect-sandbox.example.com/` answers | Administrator: DNS record, see below |
| TLS certificate covering the alias | `curl -sv https://connect-sandbox.example.com/ -o /dev/null 2>&1 \| grep -i "subject\|SSL certificate verify"` | Administrator: add the name to the certificate (or the proxy's) |
| Connect serves content on the alias **without redirecting** to the main name | step 4 below (`--check` reports redirects) | Administrator: Connect's `Server.Address` / proxy rules may force a canonical host; the alias must be allowed to serve content as-is |
| Tooling | `pip install rsconnect-python`; `make setup-dev` (Python, Playwright, Chromium; no Node) | |
| Network: Connect can install packages (PyPI, or a mirror) | the first deploy's log | Use the mirror; or `--from-git` needs GitHub access instead of the bundled wheel |

Shell variables used below (placeholders; never commit real values):

```bash
export CONNECT_SERVER=https://connect.example.com          # main hostname
export SANDBOX_HOST=https://connect-sandbox.example.com    # alias
export CONNECT_API_KEY=...                                  # publisher key
```

Administrator configuration this recipe may need (record what was required):

- the alias in DNS and on the TLS certificate;
- behind a load balancer (e.g. an AWS ALB), a listener rule that forwards the
  alias to Connect's target group, not a redirect rule;
- no redirect from the alias to the main hostname for `/content/...`;
- no `X-Frame-Options` / `Content-Security-Policy: frame-ancestors` added by
  Connect or a proxy to the sandbox content (the sandbox sets its own);
- permission to give one content item "Anyone, no login required" access.

## 2. Artifacts

Build from a clean checkout of the revision under test:

```bash
git rev-parse HEAD
make setup-dev
make connect-bundles          # dist/connect/{mcp-server,sandbox,shiny-app}, dist/connect/REVISION
make rehearse-connect         # recommended local baseline; if you run it, it must pass before deploying
```

| Item | Directory | Entry point | Maintained source |
|---|---|---|---|
| MCP server | `dist/connect/mcp-server/` | `server:app` | `examples/sales_explorer/server.py` (+ pre-rendered View) |
| Sandbox | `dist/connect/sandbox/` | `app:app` | `examples/posit_connect/sandbox/app.py` |
| Shiny app | `dist/connect/shiny-app/` | `app:app` | `examples/sales_explorer/app.py` |

Do not edit files inside `dist/`; fix the maintained source and rebuild.

## 3. Configuration and deployment

Deploy in this order (each needs the URL of the one before), using the
commands in [deployment.md § Deploy](deployment.md#2-deploy). Use titles
starting with `TEMP mcp-app-shiny validation` so cleanup can find them, and
record each content GUID and URL:

1. **MCP server** → `https://connect.example.com/content/<mcp-guid>/`;
   endpoint `…/mcp`. Env: `MCP_ALLOWED_HOSTS=<main hostname>`. Access:
   logged-in users (or a specific group).
2. **Sandbox** → reached by browsers **on the alias**:
   `$SANDBOX_HOST/content/<sandbox-guid>/sandbox.html`. Env:
   `SHINY_APP_ORIGIN=$CONNECT_SERVER` (origin only, no path). Access:
   **Anyone, no login required**.
3. **Shiny app** → `https://connect.example.com/content/<app-guid>/`. Env:
   `MCP_SERVER_URL=$CONNECT_SERVER/content/<mcp-guid>/mcp`,
   `SHINY_MCP_SANDBOX_URL=$SANDBOX_HOST/content/<sandbox-guid>/sandbox.html`,
   `MCP_SERVER_API_KEY=<key of an account allowed to view the MCP server>`
   (a dedicated key for this test is better than your own; revoke it at
   cleanup).

How the three URLs relate: the browser loads the Shiny app from the main
hostname; the app's Python process calls the MCP server (server to server,
with the API key); the page embeds the sandbox from the alias, which must
answer for `SHINY_APP_ORIGIN` = the app's origin. If the alias can serve
content directly, you can also use vanity URLs; keep `SHINY_APP_ORIGIN` equal
to what the browser's address bar shows for the app (scheme + host + port).

Access can be changed in the dashboard (content → Access) or with the
Connect API, e.g.
`curl -X PATCH -H "Authorization: Key $CONNECT_API_KEY" -H "Content-Type: application/json" "$CONNECT_SERVER/__api__/v1/content/<guid>" -d '{"access_type": "all"}'`
(`all` = anyone, `logged_in`, `acl`).

## 4. Infrastructure checks (server side)

```bash
SANDBOX_URL=$SANDBOX_HOST/content/<sandbox-guid>/sandbox.html
.venv/bin/shiny-mcp-sandbox --check "$SANDBOX_URL" --host-origin "$CONNECT_SERVER"
```

Every line must be `PASS`; `INCONCLUSIVE` means the check could not decide
(for example a `frame-ancestors` source with a path, which it does not model)
and counts as not passed until confirmed in a browser. It covers: the alias
resolves and its TLS certificate verifies; `sandbox.html` answers `200`
**without a redirect** and without asking for a login; the response is the
shiny-mcp-host sandbox page with the restrictive CSP; every `frame-ancestors`
policy on the response (including any a proxy adds) allows `$CONNECT_SERVER`;
there is no `X-Frame-Options`; `sandbox.js` and `healthz` are served under the
same prefix.

Also record, sanitized (no cookies, no keys):

```bash
curl -sS -o /dev/null -D - "$SANDBOX_URL?host=$CONNECT_SERVER&nonce=$(python -c 'import secrets;print(secrets.token_urlsafe(16))')"
curl -sS -o /dev/null -D - "$CONNECT_SERVER/content/<sandbox-guid>/sandbox.html?host=$CONNECT_SERVER&nonce=xxxxxxxxxxxxxxxxxxxxxxxx"
```

The second request (sandbox on the **main** hostname) shows what happens if
someone misconfigures `SHINY_MCP_SANDBOX_URL`: the sandbox answers `403`
("must differ from the host origin") or the host refuses it in the browser
(`sandbox_config`). Either is correct; record which.

## 5. Functional browser checks

```bash
APP_API_KEY=... MCP_API_KEY=... .venv/bin/python scripts/check_deployment.py \
  --app-url "$CONNECT_SERVER/content/<app-guid>/" \
  --sandbox-url "$SANDBOX_URL" \
  --mcp-url "$CONNECT_SERVER/content/<mcp-guid>/mcp" \
  --evidence-dir test-results/connect-validation
```

It repeats the server-side checks, checks the MCP server directly (reachable
with `MCP_API_KEY`; then an unauthenticated `initialize` must be refused with
401/403 or a redirect to a login page; a timeout, 5xx or other status is
reported `INCONCLUSIVE`, not as proof of authentication), then drives
Chromium through:

1. open the Shiny app; Shiny connects;
2. ask *"Explore Q3 sales in the West"*; the explorer renders in the reply
   (`data-state="ready"`, i.e. the View initialized);
3. change the Product filter; the total changes (View → Python → MCP);
4. click the South region; the sidebar shows the selection Python received;
5. click **Ask about this region**; the question appears and Python answers
   from the selection ("South brought in …").

`evidence.json` is always written, including when a stage crashes (browser
launch failure, navigation timeout, missing element: recorded as a failed
`unexpected error` check with the traceback under `errors`) or the run is
interrupted (`"completed": false`, never `"passed": true`).

**Logging the browser in.** If the Shiny app requires login, `APP_API_KEY`
adds `Authorization: Key …` to HTTP requests for the app's origin only (never
the alias). Whether Connect accepts that for the Shiny websocket is
**unverified**; if "app loads" fails with "Shiny did not connect", log in
once in a Playwright browser and save its state
(`page.context.storage_state(path="connect-login.json")` after an interactive
or scripted login), then pass `--storage-state connect-login.json` (delete the
file at cleanup). As a last resort, with the owner's agreement, set the
temporary Shiny app's access to "Anyone" for the test and restore it.

Also do the flow once by hand in a normal browser if possible, and note the
browser and version: the script uses Chromium only.

## 6. Isolation checks

`check_deployment.py` checks, in the deployed setup:

- the outer frame's origin is the alias (not the app's origin) and its
  `sandbox` attribute is `allow-scripts allow-same-origin allow-forms`;
- the inner (View) frame has no `allow-same-origin` (opaque origin);
- from inside the View: `parent.document` and `top.document` are
  inaccessible, the app's cookie is not readable, `fetch` to the app is
  blocked by CSP;
- a `tools/call` for a tool outside the policy is refused by Python.

The full malicious-View suite runs locally, not against Connect (it needs the
fixture MCP server). Run it on the same revision and record the result:

```bash
make test-security
```

If any isolation check fails on Connect but passes locally, treat it as a
security issue: stop, do not relax the policy, record the observation.

## 7. Failure diagnosis

| Symptom | Likely cause | How to confirm | Fix (scope) |
|---|---|---|---|
| `--check`: `reachable` FAIL with "DNS lookup failed" | Alias not in DNS | `getent hosts` | Administrator |
| `--check`: "TLS certificate not accepted" | Alias not on the certificate | `curl -v` | Administrator |
| `--check`: "HTTP 30x redirect to https://connect.example.com/…" | Connect/proxy canonicalises the host | `curl -sI` on the alias | Administrator: let the alias serve `/content/` as-is. Never point `SHINY_MCP_SANDBOX_URL` at the main hostname instead. |
| Same, behind an AWS ALB (or similar load balancer) | A listener rule for the alias uses a **redirect** action | ALB listener rules for that host header | Administrator: a rule matching the alias's host header that **forwards** to the Connect target group. DNS and the certificate usually exist already if the redirect works over HTTPS. If the redirect exists for another reason, add a new hostname for the sandbox rather than repurposing it. |
| `--check` / `check_deployment.py`: INCONCLUSIVE `frame-ancestors` | A framing policy uses a form the checker does not model (e.g. a path) | The recorded CSP headers | Confirm in the browser run (`explorer renders`); record the header |
| `mcp requires auth` INCONCLUSIVE | The unauthenticated request timed out or got a non-auth error (5xx, 404, 421, other redirect) | `evidence.json` detail | Fix reachability first; this check needs a clear 401/403 or login redirect |
| `--check`: "redirect to …/__login__…" or HTTP 401/403 "requires authentication" | Sandbox access is not "Anyone" | Content → Access | Set access to Anyone (temporary item) |
| `--check`: HTTP 403 "not in its allowed_host_origins" | `SHINY_APP_ORIGIN` ≠ the app origin browsers use (scheme, host, port, trailing path) | Compare with the address bar | Fix the variable, restart the content |
| `--check`: HTTP 404 | Wrong GUID/path, or content failed to start | Connect content logs | Redeploy; read logs |
| `--check`: `no X-Frame-Options` or `frame-ancestors` FAIL | Connect or a proxy adds framing headers | Recorded headers | Administrator: exempt the sandbox content/alias |
| Content log: `RuntimeError: Set SHINY_APP_ORIGIN` | Variable missing on the sandbox | Content → Vars | Set it |
| Deploy fails installing `./shiny_mcp_host-…whl` | Connect does not install local wheels from `requirements.txt` | Deploy log | Push the revision and rebuild with `scripts/package_connect.py --from-git <commit>`; record the result |
| `mcp reachable` FAIL with 401/403 | Key missing or account not allowed to view the MCP server | Run without `--mcp-url`'s key / check ACL | Fix access or key |
| `mcp reachable` FAIL with 421 / "Invalid Host header" | `MCP_ALLOWED_HOSTS` does not match the Host header behind Connect | MCP content log | Try the exact Host Connect forwards; record it. Unsetting it disables DNS-rebinding protection: only as a diagnosis, and say so |
| `app loads` FAIL "login required" / "Shiny did not connect" | App authentication or websocket | Browser devtools, Connect log | See "Logging the browser in" |
| `explorer renders` FAIL, `data-error-code=sandbox_config` | `SHINY_MCP_SANDBOX_URL` is on the app's origin or http under https | Error text | Fix the variable |
| `data-error-code=sandbox_unreachable` while `--check` passes | Browser-only block: framing header visible only to browsers, mixed content, network path, extension | `evidence.json` → `console` | Depends; record the console line |
| `data-error-code=view_init_timeout` | Proxy loaded but the View did not start (View HTML/JS, CSP) | Console | Check the MCP server's View |
| Error in widget with `server_unavailable` / `timeout` | Shiny app → MCP server call failed (URL, auth, Host header) | Shiny app log (`shiny_mcp_host` logger) | Fix `MCP_SERVER_URL`/key/`MCP_ALLOWED_HOSTS` |
| `filter re-queries` or later FAIL | View → Python → MCP path or websocket drops | App log, console | Investigate; compare with `make rehearse-connect` |

## 8. Evidence and acceptance criteria

Record in the "Results" section below (no secrets, no cookies, no internal
hostnames beyond what the owner agrees to publish; use placeholders if in
doubt):

- repository revision (`dist/connect/REVISION`) and whether bundles used the
  wheel or `--from-git`;
- Connect version (`curl -s $CONNECT_SERVER/__api__/server_settings | jq .version`),
  Python version used by each content item (deploy logs), `rsconnect-python`
  version, checker browser version (`evidence.json`);
- the commands run (with placeholders), the `--check` output, and
  `evidence.json` (`sandbox_response` and `app_response` are already
  sanitized; review `console` before publishing);
- the result of `make test-security` on the same revision;
- the manual browser run, if any;
- administrator changes that were needed;
- anything unresolved.

The Connect recipe may be called **verified** only when all of these hold:

1. `shiny-mcp-sandbox --check` passes against the alias URL;
2. `scripts/check_deployment.py` exits `0` with `--mcp-url` and an MCP key
   (all server, browser and isolation checks PASS, none INCONCLUSIVE;
   `evidence.json` has `"completed": true, "passed": true`);
3. the MCP server refuses requests without credentials (`mcp requires auth`);
4. the sandbox on the main hostname is refused (403 or `sandbox_config`);
5. `make test-security` passes on the same revision;
6. the deployment commands in `docs/deployment.md` are the ones actually used
   (corrected if they differed), and its "What has been verified" table and
   status note are updated with the date, Connect version and revision.

Partial success is still worth recording: mark individual rows verified and
leave the status note saying what is pending.

## Next step: one application, two hostnames

The baseline uses three content items. Browser isolation needs the sandbox on
a **different origin (hostname)**, not in a different process: one ASGI
application could serve both the Shiny app and the sandbox (e.g. a Starlette
app mounting Shiny and `sandbox_app()`), reached through the main hostname for
the app and the alias for the sandbox. This would halve the content to manage.

Evaluate it only after the baseline passes, as a separate experiment:

- serve both from one content item; point `SHINY_MCP_SANDBOX_URL` at the
  sandbox path **on the alias** and set the sandbox's allowed origin to the
  main hostname;
- confirm that the alias serves the sandbox path without redirecting, and that
  the app is **not** usable through the alias (or, if it is, that nothing
  relies on it: a page loaded from the alias would share the sandbox's origin);
- run the same `--check`, `check_deployment.py` and acceptance criteria.

Record the result here. Until it passes, document only the three-content
deployment as the recipe. Nothing in the repository implements the combined
arrangement yet.

## 9. Cleanup

Temporary items, all created by this runbook:

- the three content items titled `TEMP mcp-app-shiny validation …` (delete
  them by GUID from the dashboard, or
  `curl -X DELETE -H "Authorization: Key $CONNECT_API_KEY" "$CONNECT_SERVER/__api__/v1/content/<guid>"`
  for each recorded GUID; check the title before deleting, and never delete
  by search pattern alone);
- any API key created for `MCP_SERVER_API_KEY` or `APP_API_KEY` (revoke it);
- `connect-login.json` storage state, if used;
- local `dist/connect/`, `.cache/connect-rehearsal/`,
  `test-results/connect-validation/` (keep the evidence you committed to the
  Results section; delete the rest);
- any temporary access change (restore it before deleting, if the item is
  kept).

Do not touch other content, users or server settings. If an administrator
added the alias or header exemptions for this test, ask them whether to keep
them (production needs them) rather than removing them yourself.

## Results

_Not yet run. Fill in: date, revision, Connect version, what passed, what
failed, administrator changes, unresolved issues._
