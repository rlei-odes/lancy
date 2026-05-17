# Authentication Guide
How Lancy authenticates users, and how to configure each mode.
---

## Overview

Lancy supports three authentication modes. The mode is determined automatically from your environment configuration and the presence of an SSO configuration saved via the Admin UI. All modes share the same session mechanism: an HMAC-signed cookie (`rag_auth`) that encodes the user's role and session identity.

| Mode | Who can log in | Roles available | SSO required |
|---|---|---|---|
| **Mode 1** — shared password | Anyone who knows `APP_PASSWORD` | Single role — no separation | No |
| **Mode 2** — role-separated | `APP_PASSWORD` (users) + `ADMIN_PASSWORD` (admins) | `user` and `admin` | No |
| **Mode 3** — SSO / directory | Identity provider (OIDC or LDAP/AD) for users; `ADMIN_PASSWORD` for admins | `user` and `admin` | Yes |

The mode is reported by `GET /api/auth/mode`. The Admin UI (Auth / SSO tab) reads this to show the current state.

---

## Mode 1 — Shared Password

A single shared password (`APP_PASSWORD`) gates access. No username is required. Everyone who knows the password gets in — there is no role separation; all users have the same level of access.

**When to use:** Personal deployments, local single-user setups, or any environment where you need a basic access gate but do not need to distinguish between regular users and administrators.

**Configuration:**

```
# frontend/.env
APP_PASSWORD=change-me-to-something-strong
# ADMIN_PASSWORD not set → Mode 1 (no role separation)
```

Mode 1 is active when `APP_PASSWORD` is set and no `ADMIN_PASSWORD` is configured (neither as an env var nor via the Admin UI password change).

---

## Mode 2 — Role-Separated Passwords

Two passwords, two roles. `APP_PASSWORD` is the shared user password; `ADMIN_PASSWORD` is the separate admin password. No username is required for either.

**When to use:** Small internal teams where you want most users to have read/chat access and only designated admins to have access to the Admin UI — without running an identity provider.

**Configuration:**

```
# frontend/.env
APP_PASSWORD=shared-user-password
ADMIN_PASSWORD=separate-admin-password   # or set via Admin UI → Password settings
```

Mode 2 is active when both `APP_PASSWORD` and `ADMIN_PASSWORD` are configured (env var or Admin UI) and no SSO provider is set.

The login page shows a single password field with no username. Users enter `APP_PASSWORD` and receive the `user` role. Admins enter `ADMIN_PASSWORD` and receive the `admin` role.

---

## Mode 3 — SSO / Directory Authentication

Authenticated users log in via an external identity provider (OIDC or LDAP/Active Directory). Successful authentication grants the `user` role. The admin role is available only via the admin password escape hatch (see below).

`APP_PASSWORD` and `ADMIN_PASSWORD` must both be set when Mode 3 is active — the server refuses to serve Mode 3 if either is absent. Session cookies are signed with `SESSION_SECRET`, which is auto-generated and written to `frontend/.env` on the first SSO save; a frontend restart is required for it to take effect. Until then, `APP_PASSWORD` acts as the signing key fallback.

### When to use

- Multi-user enterprise deployments where user identity is managed by an existing IdP or Active Directory
- Environments where you want login to be tied to a corporate identity rather than a shared password

### Requirements

- `APP_PASSWORD` set in `frontend/.env` (required for all modes — login credential for Modes 1 & 2; signing key fallback for Mode 3 before `SESSION_SECRET` is activated)
- `ADMIN_PASSWORD` set in `frontend/.env` or via the Admin UI (required — admin escape hatch will not work without it)
- `SESSION_SECRET` — auto-generated and appended to `frontend/.env` on the first SSO save; requires a frontend restart to activate. Until then, `APP_PASSWORD` is used as the fallback signing key. Set it manually with `openssl rand -base64 32` if you prefer.
- `ldap3` Python package installed in the backend venv (required for LDAP only; OIDC uses no backend dependencies)
- An OIDC-compliant IdP (Keycloak, Azure AD, Okta, Authentik, …) or an LDAP/AD server reachable from the backend host

### Activating Mode 3

1. Ensure `APP_PASSWORD` and `ADMIN_PASSWORD` are set in `frontend/.env`.
2. Open the Admin UI → **Auth / SSO** tab.
3. Select a provider (OIDC or LDAP) and fill in the configuration fields.
4. Click **Save**. Mode 3 is active immediately. If `SESSION_SECRET` was not already set, it is auto-generated and appended to `frontend/.env`.
5. If the Admin UI shows a "restart required" notice, restart the frontend process so the new signing key takes effect.
6. Click **Test connection** in the Admin UI to verify the provider is reachable before relying on it for real logins.

To revert, set the provider back to **None** and save. The active mode (1 or 2) will depend on whether `ADMIN_PASSWORD` is configured.

---

## OIDC Configuration

Lancy uses the [Authorization Code + PKCE](https://datatracker.ietf.org/doc/html/rfc7636) flow. No client secret is required on the server side; the browser handles the full PKCE exchange. The Next.js server validates the resulting ID token against the IdP's JWKS endpoint.

### Fields

| Field | Description |
|---|---|
| **Client ID** | The client ID registered in your IdP |
| **Issuer URL** | The IdP's base URL for the realm / tenant (e.g. `https://keycloak.example.com/realms/lancy`). The OIDC discovery doc is fetched from `{issuer_url}/.well-known/openid-configuration`. |
| **Redirect URI** | Must exactly match the URI registered in the IdP. Set it to `https://<your-lancy-host>/auth/callback`. |
| **Allowed groups** | Comma-separated list of group names/paths. If non-empty, the user's `groups` or `roles` claim must contain at least one matching value (case-insensitive). Leave empty to admit any authenticated user. |
| **Session TTL (hours)** | How long the Lancy session cookie is valid. Default 48 h. If shorter than the IdP's own session, silent re-auth via the IdP session is possible. |

### IdP registration checklist

- Register a public OIDC client (no client secret).
- Add `https://<your-lancy-host>/auth/callback` as an allowed redirect URI.
- Enable the `openid`, `profile`, and `email` scopes.
- If using group-based access control, configure the IdP to include a `groups` or `roles` claim in the ID token.

### Example — Keycloak

1. Realm → **Clients** → **Create client**
2. Client type: OpenID Connect; Client ID: `lancy-app`
3. Turn off "Client authentication" (public client / standard flow only)
4. Valid redirect URIs: `https://lancy.example.com/auth/callback`
5. Save

**Adding the Group Membership claim** (required if using `allowed_groups`):

6. Clients → `lancy-app` → **Client scopes** tab → click `lancy-app-dedicated`
7. **Add mapper** → **By configuration** → **Group Membership**
8. Set:
   - Name: `groups`
   - Token Claim Name: `groups`
   - Full group path: **off** (sends `allowed`, not `/allowed`)
   - Add to ID token: **on**
   - Add to access token: **on**
9. Save

With this mapper in place, create a group (e.g. `allowed`), add users to it, and set `allowed_groups: allowed` in the Lancy SSO config. Users not in the group will be rejected at login.

**Multiple groups** are supported — a user is admitted if they belong to any one of them. Enter them comma-separated in the Allowed Groups field:

```
allowed, staff, contractors
```

Access is granted on first match (OR logic). Leave the field empty to admit all authenticated users.

### Testing the connection

The **Test connection** button probes the OIDC provider without requiring a browser login:

1. Fetches `{issuer_url}/.well-known/openid-configuration` — confirms the discovery document is reachable.
2. Fetches the `jwks_uri` from the discovery document — confirms the token-signing key endpoint is reachable.

Run this before your first SSO login to catch misconfigured issuer URLs or network-level issues.

---

## LDAP / Active Directory Configuration

LDAP authentication is handled server-side by the FastAPI backend using the `ldap3` library. The user supplies credentials in the browser; they are forwarded over an encrypted backend-to-backend channel (Next.js → FastAPI) and never stored.

Authentication flow:
1. User supplies username + password on the login page.
2. Next.js sends credentials and LDAP config to the backend (`POST /api/v1/auth/ldap-verify`).
3. Backend binds to the LDAP server using the configured bind DN template.
4. If group restriction is configured, the backend searches for the user's `memberOf` attributes and verifies membership.
5. On success, a session cookie is issued. The `session_id` is set to the value of `user_id_attribute` for that user (stable across logins).

### Fields

| Field | Description |
|---|---|
| **Server** | LDAP server URI, e.g. `ldaps://ldap.example.com:636`. Always use `ldaps://` (TLS) in production. |
| **Bind DN template** | DN pattern for authenticating users. `{username}` is replaced with whatever the user types in the username field. AD format: `{username}@corp.example.com`. Classic LDAP format: `uid={username},ou=People,dc=example,dc=com`. |
| **Base DN** | Root of the directory tree to search, e.g. `DC=corp,DC=example,DC=com`. |
| **User ID attribute** | LDAP attribute used as the stable session identity. AD: `userPrincipalName`. Classic LDAP: `uid`. This value becomes the `session_id` stored in the cookie. |
| **Display name attribute** | Attribute to read for the user's display name shown in the UI. AD: `displayName`. Classic LDAP: `cn`. |
| **Allowed groups** | Full group DNs, one per line. If non-empty, the user must be a member of at least one listed group (checked via `memberOf`). Leave empty to admit any authenticated user. |
| **Session TTL (hours)** | How long the Lancy session cookie is valid. Default 168 h (7 days). Re-authentication requires the user to re-enter their LDAP password. |
| **Service account DN** | Optional. A dedicated account used to search the directory for group membership, if the server requires it. Full DN, e.g. `CN=svc-lancy,OU=ServiceAccounts,DC=corp,DC=example,DC=com`. |
| **Service account password** | Password for the service account above. Only shown when a service account DN is entered. Stored encrypted in `auth_config.json`. |

### Security notes

- **Use `ldaps://` (port 636) in production.** Plain `ldap://` sends the user's password in the clear over the network between the backend host and the LDAP server. The Admin UI warns you if you configure a plain LDAP URI.
- The user's password is transmitted from the browser to the Next.js server over HTTPS (your TLS terminator) and then from Next.js to FastAPI over the loopback/private network (or `BACKEND_URL`). It is never logged or stored.
- `search_bind_password` is stored in `auth_config.json` on the frontend host. Protect that file's permissions accordingly (`chmod 600`).

### Testing the connection

The **Test connection** button probes the LDAP server using the current form values — you can fill, test, and save without multiple round trips:

1. **Server connection** — opens a TCP connection and completes the LDAP protocol handshake.
2. **Service account bind** (if a service account DN is configured) or **Anonymous bind** — verifies bind credentials, or confirms whether the server allows anonymous bind.
3. **Base DN** — searches for the base DN entry. Shown as a yellow warning (not an error) if no service account is configured, because anonymous directory reads are typically restricted.

### lldap example

The following configuration was tested against [lldap](https://github.com/lldap/lldap) with base DN `dc=lancy,dc=test`:

```
Server:                  ldap://<server>:3890   (use ldaps:// in production)
Bind DN template:        uid={username},ou=people,dc=lancy,dc=test
Base DN:                 dc=lancy,dc=test
User ID attribute:       uid
Display name attribute:  cn
Allowed groups:          cn=lancy-users,ou=groups,dc=lancy,dc=test
Session TTL (hours):     168
```

lldap allows anonymous bind by default, so the service account fields can be left empty. The Base DN connectivity test will show a yellow warning in that case — this is expected and not an error.

### Active Directory quick-start

```
Server:                  ldaps://dc01.corp.example.com:636
Bind DN template:        {username}@corp.example.com
Base DN:                 DC=corp,DC=example,DC=com
User ID attribute:       userPrincipalName
Display name attribute:  displayName
Allowed groups:          CN=Lancy-Users,OU=Groups,DC=corp,DC=example,DC=com
Session TTL (hours):     168
```

Leave the service account fields empty unless your AD requires a search account for `memberOf` lookups (most AD setups do not — the authenticated user can read their own `memberOf`).

---

## Admin Access in Mode 3

The admin role is not granted via SSO. SSO users always receive the `user` role.

To log in as admin when Mode 3 is active:

1. On the login page, click **Admin login** (always visible in Mode 3).
2. Enter `ADMIN_PASSWORD`.
3. You are logged in with the `admin` role.

This escape hatch is intentional. If SSO is misconfigured or the IdP is unreachable, you can always recover by logging in with the admin password, correcting the SSO configuration in the Admin UI, and saving.

---

## Session Cookies

| Cookie | Content | Scope |
|---|---|---|
| `rag_auth` | HMAC-SHA256-signed JWT (role + session_id + expiry); signed with `SESSION_SECRET` if set, otherwise `APP_PASSWORD` | HttpOnly, Secure (in production), `Max-Age` = session TTL |
| `lancy_display_name` | URL-encoded display name (for UI rendering only) | HttpOnly, same TTL as `rag_auth` |

Both cookies are cleared on logout.

In Modes 1 and 2, the `session_id` is a random UUID generated on first visit and stored in the browser cookie. In Mode 3, the `session_id` is overwritten with the stable IdP identity (`sub` for OIDC, `user_id_attribute` value for LDAP) on every successful SSO login.

---

## Troubleshooting

**`GET /api/auth/mode` returns 500**
Either `APP_PASSWORD` or `ADMIN_PASSWORD` is missing while an SSO provider is configured. The error message says which. Set both in `frontend/.env` and restart the frontend.

**OIDC: "Login failed (security check)"**
The PKCE state parameter did not match. Usually caused by an expired login attempt or a browser redirect loop. Have the user try again from the login page.

**OIDC: "Token verification failed"**
The ID token signature could not be verified against the IdP's JWKS. Check that `issuer_url` matches the `iss` claim in the token exactly (including trailing slashes). If the IdP recently rotated keys, the cache clears automatically on the next attempt.

**LDAP: "Invalid credentials" (user is sure the password is correct)**
Check that `bind_dn_template` produces a valid DN for that user. For AD, `{username}@corp.example.com` expects the user to type their short username (`jdoe`), not UPN or full DN. Test the bind independently with `ldapsearch -H ldaps://dc01 -D "jdoe@corp.example.com" -W`.

**LDAP: "User not found" after successful bind**
The bind succeeded but the directory search returned no results. Check `base_dn` and `user_id_attribute`. The attribute value must match exactly what the user typed as their username (or be derived from it).

**LDAP: "Access denied (group restriction)"**
The user authenticated successfully but is not a member of any allowed group. Check the group DNs in **Allowed groups** — they must be full distinguished names. Use `ldapsearch` to verify the user's `memberOf` values:
```bash
ldapsearch -H ldaps://dc01 -D "svc@corp.example.com" -W \
  -b "DC=corp,DC=example,DC=com" \
  "(userPrincipalName=jdoe@corp.example.com)" memberOf
```

**Plain LDAP warning**
The Admin UI shows a warning if `server` starts with `ldap://`. Switch to `ldaps://` and configure TLS on your LDAP server or use a local stunnel proxy.
