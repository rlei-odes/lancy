# Design Doc: SSO / AD Integration (Mode 3)

**Status:** Implemented — v0.3.4

---

## Context

Lancy currently supports two auth modes:

| Mode | How it works | Who uses it |
|---|---|---|
| **1** | No `APP_PASSWORD` set — all requests are admin | Local dev, single-user |
| **2** | Two passwords (`APP_PASSWORD` = user, `ADMIN_PASSWORD` = admin) — role encoded in a signed HMAC cookie | Small trusted teams |
| **3** (this doc) | IdP validates credentials, Lancy maps identity → role, issues same signed cookie | Organisations with centralised user management |

Mode 3 is **opt-in**. Modes 1 and 2 remain the default and are unaffected when no SSO provider is configured.

---

## Goals

- Let users authenticate against a directory or identity provider instead of a shared password, via one of two mutually exclusive paths:
  - **OIDC** — for Keycloak, Azure AD (Entra ID), Okta, Google Workspace, or any OAuth2-compatible IdP
  - **LDAP** — for traditional Active Directory and OpenLDAP environments (AD natively exposes LDAP on port 389/636)
- Gate access via the IdP — authenticated users get the `user` role; admin access stays via `ADMIN_PASSWORD` (escape hatch on the login page)
- No user provisioning step: a user who authenticates successfully is admitted lazily; their DB record is created on first message
- Keep the middleware, cookie mechanism, and FastAPI backend contract unchanged
- Provide local testing paths for both: Keycloak in Docker (OIDC), lldap in Docker (LDAP)

---

## What Does Not Change

The middleware ([frontend/src/middleware.ts](../frontend/src/middleware.ts)) already only verifies the signed cookie — it does not care how the credential was validated. Every authenticated session ends up with the same `rag_auth` cookie carrying a signed `admin` or `user` role claim, regardless of which mode issued it.

Mode 3 adds new routes (OIDC callback page, verify-token API, ldap-verify FastAPI endpoint) and modifies the existing login route to branch on provider type. The `x-session-id` → FastAPI header contract is untouched.

**What did change in `auth.ts` / `middleware.ts`:** `signToken` gained an optional `ttlSeconds` parameter (Modes 1/2 keep the 30-day default; Mode 3 uses `session_ttl_hours`). The signing key is now `SESSION_SECRET || APP_PASSWORD` — `SESSION_SECRET` is auto-generated on first SSO save and requires a frontend restart to activate. Both `middleware.ts` and `login.ts` use the same key expression so tokens remain consistent before and after the restart.

---

## Two Paths — Mutually Exclusive, Both First-Class

Exactly one provider is configured at a time. The `provider` field in the `sso` config block selects the path. Both paths converge on the same cookie issuance logic.

### Path A: OIDC / OAuth2

Browser-redirect flow using `oidc-client-ts`. The browser handles the full PKCE protocol — the Lancy server never contacts the IdP per login. The FastAPI backend is not involved.

**When to use:** you have a modern IdP — Keycloak, Azure AD (Entra ID), Okta, Google Workspace, or any OAuth2-compatible server. Keycloak is the local dev/test target for this path.

**Network topology:** only the user's browser needs to reach both Lancy and the IdP. The Lancy server only contacts the IdP once — to fetch the JWKS public keys for JWT validation, cached in memory. No server-to-server call per login. The FastAPI backend never reaches the IdP at all.

**Trust setup (one-time):** register a public client (no secret) in the IdP, configure the allowed redirect URI pointing to Lancy's callback page. That redirect URI restriction and the PKCE verifier are the trust mechanism.

**Flow:**
1. User clicks "Log in with SSO" → `oidc-client-ts` generates PKCE `code_verifier` + `code_challenge`, stores verifier in `sessionStorage`, redirects browser to IdP
2. User authenticates at IdP; IdP redirects browser to `/auth/callback?code=...`
3. `oidc-client-ts` on the callback page verifies `state`, sends `code` + `code_verifier` directly to the IdP's token endpoint (browser → IdP), receives `id_token`
4. Callback page POSTs `id_token` to Next.js `/api/auth/verify-token`
5. Next.js validates JWT signature with `jose` (using cached JWKS), extracts `sub` + display name, issues `rag_auth` + `session_id` cookies, redirects to app

**Libraries:**
- `oidc-client-ts` — browser-side OIDC/PKCE protocol handler
- `jose` — Next.js API route JWT validation and JWKS fetching (~15 kB, Web Crypto API)

### Path B: LDAP direct bind

User submits username + password to Lancy's login form. The LDAP bind runs in the **FastAPI backend** using `ldap3` — the most battle-tested Python LDAP library for AD environments.

**When to use:** you have a traditional Active Directory or OpenLDAP environment. AD natively exposes LDAP on port 389/636 — no additional IdP software needed. lldap in Docker is the local dev/test target.

**Network topology:** the browser only talks to Next.js. Next.js makes an internal server-to-server call to FastAPI. FastAPI connects to the LDAP/AD server on the local network. FastAPI is not internet-exposed.

**Flow:**
1. User submits username + password to Next.js `/api/auth/login`
2. Next.js proxies credentials to FastAPI `POST /api/v1/auth/ldap-verify` (internal call)
3. FastAPI (`ldap3`) opens LDAP connection, attempts bind with user credentials
4. On success, fetches group memberships via `memberOf` attribute (AD) or group search
5. Returns `{ session_id, display_name }` to Next.js
6. Next.js issues `rag_auth` + `session_id` cookies, returns to browser

**Libraries:**
- `ldap3` (Python/FastAPI) — mature, handles AD quirks, StartTLS, referrals, connection pooling
- No new Node.js LDAP dependency needed

**Notes:**
- For AD: bind uses `username@domain.com` format (`bind_dn_template: "{username}@corp.example.com"`)
- `session_id` is set to the user's `userPrincipalName` fetched after a successful bind — see Identity section
- LDAPS (`ldaps://`) or STARTTLS must be used in production — plain LDAP sends passwords in the clear
- FastAPI needs intranet access to the LDAP port only — not internet exposure

---

## Configuration Schema

A new `sso` block in `auth_config.json` (already gitignored, writable from the admin UI). Only one provider is active at a time — `provider` selects the path.

**Design decision: SSO only determines access, not role.** Every user who successfully authenticates via SSO receives the `user` role. Admin access is always via `ADMIN_PASSWORD` (escape hatch on the login page). This removes all group-to-role mapping complexity from the IdP integration.

`allowed_groups` is optional on both paths. If set, Lancy checks membership after a successful authentication and denies access if the user is not in any listed group. If omitted, Lancy relies entirely on IdP-level access control (app assignment in Entra ID, client policies in Keycloak, etc.) — any valid authentication equals access.

`session_ttl_hours` is configurable per deployment. OIDC defaults to 48 hours (IdP re-auth can be silent via IdP session cookie). LDAP defaults to 168 hours / 7 days (re-auth requires the user to type their password again).

### OIDC — Keycloak / generic

```json
{
  "admin_password": "...",
  "sso": {
    "provider": "oidc",
    "client_id": "lancy-app",
    "issuer_url": "https://keycloak.example.com/realms/lancy",
    "redirect_uri": "https://lancy.example.com/auth/callback",
    "allowed_groups": ["lancy-users"],
    "session_ttl_hours": 48
  }
}
```

No `client_secret` — the registered redirect URI and PKCE verifier are the trust mechanism. `issuer_url` is used to fetch `/.well-known/openid-configuration` automatically. If `allowed_groups` is set, Lancy checks the `groups` and `roles` claims in the ID token (in that order) for a match.

### OIDC — Azure Entra ID

Register the app as **"Single-page application"** type (not "Web") — this enables CORS on the token endpoint, required for browser-side code exchange. Enable **"User assignment required"** in the app registration to gate access at the IdP level; with that set, `allowed_groups` in Lancy is not needed since Entra ID already enforces who can authenticate.

No App Roles configuration needed — Lancy no longer derives role from the token.

```json
{
  "sso": {
    "provider": "oidc",
    "client_id": "<application-client-id>",
    "issuer_url": "https://login.microsoftonline.com/<tenant-id>/v2.0",
    "redirect_uri": "https://lancy.example.com/auth/callback",
    "session_ttl_hours": 48
  }
}
```

### LDAP — generic / OpenLDAP

```json
{
  "sso": {
    "provider": "ldap",
    "server": "ldaps://ldap.example.com:636",
    "bind_dn_template": "uid={username},ou=people,dc=example,dc=com",
    "base_dn": "dc=example,dc=com",
    "user_id_attribute": "uid",
    "display_name_attribute": "cn",
    "allowed_groups": ["cn=lancy-users,ou=groups,dc=example,dc=com"],
    "session_ttl_hours": 168
  }
}
```

### LDAP — Active Directory

```json
{
  "sso": {
    "provider": "ldap",
    "server": "ldaps://dc.corp.example.com:636",
    "bind_dn_template": "{username}@corp.example.com",
    "base_dn": "DC=corp,DC=example,DC=com",
    "user_id_attribute": "userPrincipalName",
    "display_name_attribute": "displayName",
    "allowed_groups": ["CN=Lancy-Users,OU=Groups,DC=corp,DC=example,DC=com"],
    "session_ttl_hours": 168
  }
}
```

`bind_dn_template` substitutes `{username}` with what the user typed. `username@domain` is the standard modern AD format.

`user_id_attribute` names the LDAP attribute fetched after a successful bind to use as the stable `session_id`. **For AD: `userPrincipalName`** — globally unique within the forest, human-readable (`jsmith@corp.example.com`), aligns with the UPN login format. For generic LDAP: `uid`. Do not use `sAMAccountName` (not unique across domains) or `objectGUID` (opaque, hard to debug).

`display_name_attribute` names the attribute used as the user's display name in the UI. **For AD: `displayName`**. For generic LDAP: `cn`. Fallback chain: configured attribute → `cn` → the value the user typed at login.

If `allowed_groups` is set, Lancy checks the `memberOf` attribute on the user object after a successful bind — natively supported in AD. Some LDAP servers require a separate search; use the optional `search_bind_dn` / `search_bind_password` config fields for those.

---

## Role Mapping

SSO authentication always yields the `user` role. Lancy does not map IdP groups to roles — the IdP is only asked "is this person allowed in?", not "what can they do?".

```
authenticated + (no allowed_groups set, or member of allowed_groups)  → "user"
authenticated + allowed_groups set + not a member                     → 401
unauthenticated / bind failed / token invalid                         → 401
```

Admin access is always through `ADMIN_PASSWORD`. The "Admin Login" escape hatch is always visible on the login page when Mode 3 is active — deliberately so, since a broken SSO config would otherwise lock out all admins.

Group membership is checked at login only — not cached between requests. Existing sessions live out their `session_ttl_hours` TTL after a group removal, which is the expected trade-off for a stateless cookie-based system.

---

## User Identity and Existing Data Storage

The app already has a substantial per-user data layer:

| Storage | Tables | Key | Contains |
|---|---|---|---|
| `conversations.db` | `users`, `conversations`, `messages`, `sources`, `reactions` | `session_id` | Full conversation history |
| `user_config.db` | `user_config`, `presets` | `session_id` | Per-user retrieval config, personal presets |

`session_id` is a random UUID issued on first login and stored in a 1-year HttpOnly cookie. The Next.js middleware injects it as `x-session-id` on every backend request. All user data is keyed against it. A `users` table row is created automatically on a user's first message — no admin provisioning step required.

### How Mode 3 changes the identity

In Modes 1 and 2, `session_id` is a per-browser UUID — data is tied to the browser cookie, not the person. Clear cookies or use a new device and you start fresh.

In Mode 3, the login handler sets `session_id` to a stable IdP-issued identity instead of a browser UUID:

| Path | `session_id` value | Example |
|---|---|---|
| OIDC | `sub` claim from the ID token | `f7a2c1d0-...` (Keycloak) or `abc123` (Azure AD) — opaque but stable |
| LDAP | Value of `user_id_attribute` fetched after bind | `jsmith@corp.example.com` (`userPrincipalName`) |

The same person logging in from any device gets the same value → same conversation history, same retrieval config. No schema changes needed — `session_id` is already a string column everywhere. The middleware and FastAPI backend are entirely unaffected.

**Display name** is stored in a separate HttpOnly cookie `lancy_display_name` set alongside `rag_auth` at login. The existing `/api/auth/me` endpoint (currently returns `{ role }`) is extended to also return `display_name` by reading this cookie. The login page, header, and any "logged in as" UI element reads from `/api/auth/me`.

- OIDC: extract from `name` claim → `preferred_username` → `email` → fallback to `sub`
- LDAP: fetch `display_name_attribute` (e.g. `displayName`) → `cn` → fallback to typed username

### No user provisioning step

A `users` row is created lazily on first use. "No provisioning" means no admin action is needed before a user can log in — the record appears automatically the moment the user sends their first message.

### Future: per-KB access control

Role-based KB access (restricting which knowledge bases a given user or group can query) is a natural next step once individual identities are in place. Not in scope for Mode 3 — noted here so the design is not closed against it.

---

## API Client Auth in SSO Mode

Browser sessions work via the redirect flow. API clients (curl, Open WebUI) that use `Authorization: Bearer` have no browser session. Options:

1. **Service account password** — `APP_PASSWORD` continues to work as a Bearer token → admin role. This is already implemented in the middleware and requires no change. Suitable for trusted internal integrations.
2. **API key** — a separate, separately-issued token stored in `auth_config.json`. More explicit, revocable without changing the app password. Deferred — service account password is sufficient for now.

---

## Implementation Plan

### Key existing files to understand first

| File | Role |
|---|---|
| `frontend/src/middleware.ts` | Auth gatekeeper — `getRole()` reads `rag_auth` cookie; `x-session-id` header injection |
| `frontend/src/lib/auth.ts` | `signToken` / `verifyToken` — HMAC cookie signing, unchanged |
| `frontend/src/lib/auth-config.ts` | Reads/writes `auth_config.json`; pattern to follow for SSO config extension |
| `frontend/src/pages/api/auth/login.ts` | Existing login handler — branch here for LDAP mode |
| `frontend/src/pages/api/auth/me.ts` | Returns `{ role }` — extend to return `{ role, display_name }` |
| `frontend/src/pages/api/admin/admin-config.ts` | Existing admin config endpoint — extend for SSO config |
| `frontend/src/pages/login.tsx` | Login page — add SSO button / username field based on mode |
| `backend/src/lancy/main.py` | FastAPI entry point — add `POST /api/v1/auth/ldap-verify` here |

### Step 1: Config and mode detection

- Extend `auth-config.ts` with `getSSOConfig()` and `isMode3Active()` (true when `sso` block is present)
- Extend the admin UI with an "Auth / SSO" tab: provider selector, fields per provider type, prerequisites status panel, test-configuration button
- API endpoint `GET/POST /api/auth/admin-config` (note: lives under `/api/auth/`, not `/api/admin/`) extended to read/write the `sso` block; auto-generates `SESSION_SECRET` into `frontend/.env` on first SSO save

### Step 2: OIDC path

New frontend pieces — FastAPI not touched:

- `npm install oidc-client-ts jose` in the frontend
- `/api/auth/sso-config` — public Next.js API route returning `{ client_id, issuer_url, redirect_uri }` so the browser can initialise `oidc-client-ts` without exposing the full server config
- `/auth/callback` — Next.js **client-side page**: initialises `UserManager` from `oidc-client-ts`, calls `signinCallback()` (handles state verification + code exchange with IdP), extracts `id_token`, POSTs to `/api/auth/verify-token`, redirects to home on success
- `/api/auth/verify-token` — Next.js API route: receives `id_token`, validates JWT signature with `jose` using JWKS from `issuer_url/.well-known/openid-configuration` (fetched once, module-level cache), extracts `sub` + display name, issues `rag_auth` + `session_id` cookies

The login button calls `userManager.signinRedirect()` — `oidc-client-ts` handles PKCE, state, and `sessionStorage` automatically.

### Step 3: LDAP path

New FastAPI endpoint + Next.js proxy — no new frontend dependency:

- FastAPI: `POST /api/v1/auth/ldap-verify` — receives `{ username, password }`, uses `ldap3` to bind, fetches `memberOf` / group membership, checks `allowed_groups`, returns `{ session_id, display_name }` or raises 401
- `pip install ldap3` in the backend
- Next.js `/api/auth/login`: when Mode 3 + LDAP active, forward credentials to FastAPI endpoint instead of comparing against `APP_PASSWORD`; on success issue `rag_auth` + `session_id` cookies as normal
- AD and generic LDAP differ only in bind DN format — handled by `bind_dn_template` config

### Step 4: Login page UI

- `/login` page: detect Mode 3 from a new public `/api/auth/mode` endpoint (returns `{ mode: 1|2|3, provider: "oidc"|"ldap"|null }`)
- OIDC: replace password form with a single "Log in with SSO" button
- LDAP: replace password form with username + password fields (password label updated, no hint about shared password)
- Escape hatch: always shown in Mode 3 — unconditional, not gated on APP_PASSWORD presence. Clicking it shows a password field (labelled "Admin password") that accepts `ADMIN_PASSWORD`

---

## Testing Locally

### Path A: OIDC with Keycloak

Keycloak runs in Docker and provides a full OIDC server with a management UI.

```bash
docker run -p 8080:8080 \
  -e KEYCLOAK_ADMIN=admin \
  -e KEYCLOAK_ADMIN_PASSWORD=admin \
  quay.io/keycloak/keycloak:latest start-dev
```

**Realm and client setup (Keycloak admin UI at http://localhost:8080):**

1. Create a realm named `lancy`
2. Create a client `lancy-app`:
   - Client type: OpenID Connect
   - **Client authentication: off** (public client — no secret, PKCE only)
   - Valid redirect URIs: `http://localhost:3000/auth/callback`
   - **Web Origins: `http://localhost:3000`** — required for CORS on the token endpoint; without this the browser's token exchange fetch is blocked
3. Create test users

**Option A — No group check (simplest):**

4. Create test users — that's it. Any user who can authenticate against the realm is admitted.

**Option B — Group-gated access:**

4. Create a group `lancy-users`; assign test users to it; leave one user unassigned to test denial
5. Add a `groups` claim: Client Scopes → `lancy-app-dedicated` → Add mapper → Group Membership → Token claim name: `groups`
   - Keycloak prefixes group paths with `/` by default

**Lancy config:**
```json
{
  "sso": {
    "provider": "oidc",
    "client_id": "lancy-app",
    "issuer_url": "http://localhost:8080/realms/lancy",
    "redirect_uri": "http://localhost:3000/auth/callback",
    "allowed_groups": ["/lancy-users"],
    "session_ttl_hours": 48
  }
}
```

Omit `allowed_groups` entirely for Option A.

---

### Path B: LDAP with lldap

[lldap](https://github.com/lldap/lldap) is a lightweight LDAP server in Docker — far simpler to set up than OpenLDAP, and sufficient to test Lancy's LDAP bind logic. It is not a substitute for AD in production, but the LDAP queries Lancy issues are the same.

```bash
docker run -p 3890:3890 -p 17170:17170 \
  -e LLDAP_JWT_SECRET=dev-secret \
  -e LLDAP_LDAP_BASE_DN="dc=lancy,dc=test" \
  -e LLDAP_LDAP_USER_PASS=adminpassword \
  lldap/lldap:stable
```

lldap admin UI is at http://localhost:17170 (user: `admin`, password: `adminpassword`). Create a group `lancy-users`, create test users, assign some to the group (leave one out to test denial).

**Lancy config:**

```json
{
  "sso": {
    "provider": "ldap",
    "server": "ldap://localhost:3890",
    "bind_dn_template": "uid={username},ou=people,dc=lancy,dc=test",
    "base_dn": "dc=lancy,dc=test",
    "user_id_attribute": "uid",
    "display_name_attribute": "cn",
    "allowed_groups": ["cn=lancy-users,ou=groups,dc=lancy,dc=test"],
    "session_ttl_hours": 168
  }
}
```

When testing against a real AD later, only the `server`, `bind_dn_template`, and DNs change — the logic path is identical.

---

### Shared verification checklist

- [ ] Authenticated user lands in app with `user` role
- [ ] If `allowed_groups` set: user in the group → admitted; user not in group → 401
- [ ] If `allowed_groups` not set: any authenticated user → admitted
- [ ] Admin Login escape hatch works: enter `ADMIN_PASSWORD` → admin session
- [ ] Bearer token with `APP_PASSWORD` still works for API clients
- [ ] Group removal in IdP reflected on next login (session lives out TTL)
- [ ] Mode 1 and Mode 2 still work when `sso` block is absent from `auth_config.json`
- [ ] Session expires after configured `session_ttl_hours`

---

## Additional Considerations

### Session revocation

When a user's IdP account is disabled, their Lancy cookie remains valid until `session_ttl_hours` expires. There is no active revocation mechanism. This is the standard trade-off for stateless cookie-based sessions — most internal tools accept it and use TTL as the revocation window.

The TTL defaults reflect this: 48 h for OIDC, 7 days for LDAP. If the organisation has stricter requirements (e.g. immediate access cut-off when an employee leaves), the OIDC TTL can be reduced to 8 h — silent re-auth via the IdP session means users won't notice unless their IdP account is actually disabled. A server-side session blocklist is the alternative but adds stateful infrastructure.

### Silent re-authentication (OIDC)

When a Lancy OIDC session expires, the user is redirected to the IdP. If the IdP session is still alive (typical — IdP sessions are usually longer than app sessions), the IdP issues new tokens immediately and redirects back with no visible login screen. This is the "SSO magic" — the user never sees a login prompt during the working day.

LDAP has no equivalent. Expiry means the user must type their password again, which is why the 7-day default is reasonable there.

### Predictable `session_id` in Mode 3

In Modes 1/2, `session_id` is an unguessable random UUID. In Mode 3 it becomes the IdP subject (`john.smith@corp.com` or similar) — a predictable value. A user who has a valid `rag_auth` cookie and knows another user's IdP subject could craft a `session_id` cookie to impersonate them and access their conversation history.

This requires a stolen `rag_auth` cookie, so it is not an unauthenticated attack. For the current intranet deployment model it is an acceptable risk. Worth being aware of: if Lancy ever faces an internet threat model, the `session_id` should be a signed value rather than a plain string.

### Data continuity at Mode 3 activation

Existing Mode 2 users have history under their browser UUID. When they first log in via SSO, their `session_id` becomes their IdP subject — their old history is unreachable under the old UUID. **Decided: accept the break.** Users start fresh under SSO. No migration mechanism needed.

### Display name in the UI

Specified in the Identity section above. Implement in the same release as the auth flow — the mechanism (separate `lancy_display_name` cookie + `/api/auth/me` extension) is small and should not be deferred.

### LDAP service account for group search

When `allowed_groups` is set on the LDAP path, Lancy checks group membership after a successful bind. In AD, users typically have directory read access so their own credentials can be used for the search. Some LDAP servers require a dedicated read-only service account instead. Optional config fields to add if needed: `search_bind_dn` and `search_bind_password`.

### Error states

The following need explicit error handling and user-facing messages:

| Scenario | User sees today | Should show |
|---|---|---|
| IdP unreachable at login | Unhandled exception / blank | "Login service unavailable — try the admin password login" |
| PKCE `state` mismatch | Crash | "Login failed (security check) — please try again" |
| `allowed_groups` check fails | Unhandled 401 | "Your account is not authorised to use this application" |
| OIDC token invalid / expired | Unhandled | "Login failed — token could not be verified" |

### Reverse proxy and `redirect_uri`

The `redirect_uri` configured in both the IdP and Lancy's config must be the URL the user's browser sees — the public external URL, not the internal Docker/localhost address. If Lancy runs behind nginx or a reverse proxy, the configured URI must be `https://lancy.corp.com/auth/callback`, not `http://localhost:3000/auth/callback`. Easy to get wrong in deployment; worth a note in the setup guide when Mode 3 ships.

### Library decisions

| Concern | Library | Where | Reason |
|---|---|---|---|
| OIDC browser flow (PKCE, state, code exchange) | `oidc-client-ts` | Browser (React) | Handles full protocol correctly; battle-tested across IdPs |
| JWT validation + JWKS fetching | `jose` | Next.js API route | Minimal, Web Crypto API, works in Edge Runtime if needed |
| LDAP bind + group search | `ldap3` | FastAPI (Python) | Most capable LDAP library for AD; handles StartTLS, referrals, pooling |

**Auth.js / NextAuth.js — not adopted.** It wants to own the session model, which conflicts with the `session_id` → `x-session-id` contract the FastAPI backend relies on. It would also require Mode 1/2 password auth to become a "Credentials" provider — a second-class citizen in its model. The OIDC implementation with `oidc-client-ts` + `jose` is small enough to own directly.

**BFF pattern (server-side token exchange) — not adopted.** Would require FastAPI to make outbound calls to the IdP's token endpoint. Keeping the FastAPI backend fully internal (no internet exposure required) is a design constraint. The browser-side PKCE flow satisfies this; the BFF pattern does not.

---

## Resolved Decisions

1. **Session TTL** — configurable via `session_ttl_hours` in the SSO config block. Defaults: OIDC 48 h (IdP re-auth can be silent), LDAP 168 h / 7 days (re-auth requires password entry).

2. **No role mapping from SSO** — SSO only gates access. Every authenticated user gets the `user` role. Admin access is always via `ADMIN_PASSWORD` (escape hatch on the login page). This eliminates group-claim name differences across IdPs and removes all role-mapping configuration from the SSO setup.

3. **`session_id` as IdP subject in Mode 3** — OIDC: `sub` claim. LDAP: value of `user_id_attribute` (default `userPrincipalName` for AD, `uid` for generic LDAP). No schema changes. Gives portable identity across devices for free.

4. **Display name** — separate `lancy_display_name` HttpOnly cookie set at login alongside `rag_auth`. `/api/auth/me` extended to return `{ role, display_name }`. OIDC: `name` → `preferred_username` → `email` → `sub`. LDAP: `display_name_attribute` → `cn` → typed username.

5. **LDAP TLS** — `ldap://` allowed in config (dev convenience). Admin UI shows a warning when plain LDAP is configured. No hard block.

6. **Data continuity** — accept the break. Users start fresh under SSO; no migration from Mode 2 UUID history.

7. **Libraries** — `oidc-client-ts` (browser PKCE flow) + `jose` (Next.js JWT validation) for OIDC; `ldap3` (Python) via FastAPI for LDAP. Auth.js and the BFF pattern both rejected: Auth.js conflicts with the session model; BFF requires FastAPI internet exposure.

8. **`ADMIN_PASSWORD` required for Mode 3** — The server refuses to activate Mode 3 if `ADMIN_PASSWORD` is absent (`/api/auth/mode` returns HTTP 500). Without it, the admin escape hatch is inoperable and no one can reach the Admin UI once SSO is active. A server admin can still recover by editing `auth_config.json` directly.

9. **`SESSION_SECRET` as the cookie signing key** — `APP_PASSWORD` is a user-facing credential in Modes 1/2 (users type it to log in). Using it as the HMAC signing key in Mode 2 means users know the key and could forge tokens. The fix: a separate `SESSION_SECRET` env var, auto-generated and appended to `frontend/.env` on the first SSO save. The signing key is `SESSION_SECRET || APP_PASSWORD` in both `middleware.ts` and `login.ts` so behaviour is consistent before and after the required frontend restart. In Mode 3, `APP_PASSWORD` is never typed by regular users, so the fallback is not a security risk during the restart window.

---

## To Do (deferred, not blocking initial implementation)

- **OIDC logout** — RP-initiated logout to also invalidate the IdP session. Currently logout just clears the local cookie. Low priority — users who close the browser lose the Lancy cookie anyway.
- **OIDC logout** — RP-initiated logout to also invalidate the IdP session. Low priority for initial release.
