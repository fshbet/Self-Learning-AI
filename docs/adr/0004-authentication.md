# ADR 0004 — Authentication: local-only mode needs none; LAN/remote mode gets an optional layer

Status: **proposed (design only — no authentication is implemented; awaiting approval)** · Date: 2026-09-14 · P2.7

## Context

The platform is local-first and single-user by design: the API binds to `127.0.0.1:8010`, the CLI talks to the same
database directly, and the person at the keyboard is the only principal. Audit P0.1 closed the one hole that
existed in that model (a web page in the user's browser driving the API) with same-origin CORS and an origin guard.
The question for P2.7 is whether that boundary is sufficient, and for which deployments.

## Threat model — local-only mode (the default)

**Assets.** Curated knowledge and evidence, model-provider settings including API keys stored (masked) in the
`settings` table, the source catalogue (adding a source = choosing what the platform will crawl and believe),
review decisions, snapshots.

| Adversary | Can they reach the API? | Mitigation | Residual risk |
|---|---|---|---|
| Another host on the network | no — loopback bind | `KP_API_HOST=127.0.0.1` | none while the bind is loopback |
| A web page in the user's browser (drive-by CSRF, DNS-rebinding page) | it can *send* requests to 127.0.0.1 | CORS limited to own origins; origin guard refuses cross-origin mutations; `Sec-Fetch-Site: cross-site` refused | reads are also blocked by CORS (no `Access-Control-Allow-Origin`); a rebinding page carries the attacker's origin and is refused |
| Another *user account* on the same machine | yes — loopback is shared by all local users | none (single-user assumption) | a multi-user workstation exposes the API and the database to every local account; documented as out of scope |
| Malware running as the user | yes, and it can also read `.env`, the object store and the database | none — an attacker with the user's privileges already owns everything the app protects | inherent to local software |
| The user's own browser extensions | yes (same origin) | none | same as above |

**Verdict:** for a single user on a personal machine the boundary is sufficient. Adding a login would not change
any row of the table: every adversary who can bypass the origin checks already runs with the user's privileges and
can read the credentials a login would be guarding. Authentication in this mode would be theatre plus friction for
the CLI (which has direct database access anyway).

## Threat model — LAN / remote mode (`KP_API_HOST=0.0.0.0`, a reverse proxy, a VM)

Here the assumptions break: other hosts *can* reach the API, the network may carry other people and other
software, and "the user" becomes "whoever can open a TCP connection".

| Adversary | What they get today | Needed |
|---|---|---|
| Anyone on the LAN / internet | every route: read all knowledge, change model endpoint + keys, add sources (poison the corpus), delete data, run the crawler against arbitrary URLs (the SSRF guard limits targets to public addresses, but it is still a crawler the attacker controls) | **authentication** of every request, and **authorization** distinguishing read from write |
| Passive network observer | plaintext HTTP: knowledge, settings, keys in transit | **TLS** |
| A browser on another machine | origin guard treats the proxy's origin as foreign unless listed | `KP_ALLOWED_ORIGINS` (exists) |
| Brute force / credential stuffing | n/a today | rate limiting at the edge |

**Verdict:** the current boundary is **not** sufficient for LAN/remote deployment; the README already says so and
requires an authenticating reverse proxy. That requirement is right but currently unenforceable: nothing stops a
user from binding to `0.0.0.0` without one.

## Decision (proposed)

1. **Local mode keeps no login.** The CLI workflow, the embedded worker and the served UI stay as they are.
2. **Exposure guard (small, implementable now).** When `api_host` is not a loopback address and no authentication
   layer is configured (`KP_AUTH_MODE=none`, the default), `kp serve` refuses to start and explains why. Binding
   beyond loopback requires an explicit choice: `KP_AUTH_MODE=proxy` (trust an authenticating reverse proxy) or
   `KP_AUTH_MODE=token` (see 3). `KP_AUTH_MODE=none` on a non-loopback bind is allowed only with
   `KP_INSECURE_EXPOSE=true`, which is logged at startup as a warning. This turns the README's advice into a
   guarantee without adding any credential handling to the platform.
3. **Optional authentication layer for remote mode — design.** Two modes, both *outside* the local workflow:
   * `proxy`: the platform trusts a reverse proxy that terminates TLS and authenticates (Caddy/nginx with
     forward-auth, Cloudflare Access, Tailscale Serve, Authelia…). The proxy passes the identity in a header
     (`X-Forwarded-User`, configurable); the platform records it as the `reviewer` / `created_by` actor instead of
     the free-text field the UI sends today, and refuses requests that lack the header or arrive from a source other
     than the configured proxy address. Roles come from a second header (`X-Forwarded-Groups`) mapped to
     `reader` / `reviewer` / `admin`.
   * `token`: static bearer tokens generated by `kp auth token create --role reviewer` and stored hashed in a new
     `api_tokens` table; every `/api/*` request must carry `Authorization: Bearer …`; the SPA stores the token in
     `sessionStorage` after a one-time paste. Suitable for a single owner reaching their own machine over Tailscale
     or an SSH tunnel; not a multi-user account system (no passwords, no signup, no email).
   * Authorization in both modes is a three-role matrix: `reader` = GET; `reviewer` = review actions, knowledge
     entry, snapshots, evaluations; `admin` = settings, sources, domains, jobs. Enforced by one dependency on the
     routers, so the local mode (`none`) simply injects `admin`.
   * TLS is the proxy's job in both modes; the platform never terminates TLS itself.
4. **Not proposed:** a built-in username/password system, sessions with cookies, OAuth/OIDC clients, an admin UI
   for users. They are a product in themselves and the local-first target does not need them; `proxy` mode covers
   organisations that already have an identity provider.

## Implementation scope if approved

* Step 2 (exposure guard): ~40 lines in `config.py` / `cli.py serve` + tests. **Recommended regardless of 3.**
* Step 3 (`proxy` + `token` + roles): a new `api/auth.py` dependency, an `api_tokens` table + migration, a CLI
  sub-app, a header-based actor for review/creation routes, SPA token prompt, tests for every role/route class.
  Roughly a day of work; **significant enough to wait for approval**, especially the decision on roles.

## Consequences

* Local users see no change.
* The platform can no longer be exposed by accident; exposing it deliberately requires naming the layer that
  authenticates.
* Provenance improves in remote mode: the actor recorded on review decisions comes from the authentication layer,
  not from a free-text field.
