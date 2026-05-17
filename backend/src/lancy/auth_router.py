"""
LDAP endpoints for Mode 3 SSO.

POST /api/v1/auth/ldap-verify
    Receives credentials + LDAP config from the Next.js login route.
    Performs an LDAP bind, fetches user attributes, checks group membership.
    Returns { session_id, display_name } on success, 401 on failure.

POST /api/v1/auth/ldap-test
    Connectivity probe used by the admin UI test button.
    Does not require user credentials — tests server reachability, optional
    service-account bind, and base DN lookup.
    Returns { steps: [{label, ok, detail}] }.
"""

import asyncio
import logging
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

log = logging.getLogger("uvicorn")


class LDAPTestRequest(BaseModel):
    server: str
    base_dn: str
    search_bind_dn: Optional[str] = None
    search_bind_password: Optional[str] = None


class LDAPTestStep(BaseModel):
    label: str
    ok: bool
    detail: str


class LDAPTestResponse(BaseModel):
    steps: list[LDAPTestStep]


def _ldap_test_sync(req: LDAPTestRequest) -> LDAPTestResponse:
    try:
        import ldap3
        from ldap3.core.exceptions import LDAPException
    except ImportError as e:
        raise HTTPException(status_code=500, detail="ldap3 is not installed") from e

    steps: list[LDAPTestStep] = []

    # Step 1: TCP connection + LDAP protocol handshake.
    try:
        server = ldap3.Server(req.server, connect_timeout=10)
        conn = ldap3.Connection(server, auto_bind=ldap3.AUTO_BIND_NONE)
        conn.open()
        steps.append(LDAPTestStep(label="Server connection", ok=True, detail=f"Connected to {req.server}"))
    except LDAPException as e:
        steps.append(LDAPTestStep(label="Server connection", ok=False, detail=str(e)))
        return LDAPTestResponse(steps=steps)

    search_conn = None

    # Step 2: Bind — service account if provided, anonymous otherwise.
    if req.search_bind_dn and req.search_bind_password:
        try:
            search_conn = ldap3.Connection(
                server, user=req.search_bind_dn, password=req.search_bind_password, auto_bind=True
            )
            steps.append(LDAPTestStep(label="Service account bind", ok=True, detail=f"Bound as {req.search_bind_dn}"))
        except LDAPException as e:
            steps.append(LDAPTestStep(label="Service account bind", ok=False, detail=str(e)))
    else:
        try:
            conn.bind()
            search_conn = conn
            steps.append(LDAPTestStep(label="Anonymous bind", ok=True, detail="Server allows anonymous bind"))
        except LDAPException:
            steps.append(LDAPTestStep(
                label="Anonymous bind",
                ok=False,
                detail="Anonymous bind not supported — normal for lldap and Active Directory",
            ))

    # Step 3: Base DN lookup (only if we have a working bind).
    if search_conn is not None:
        try:
            search_conn.search(req.base_dn, "(objectClass=*)", search_scope=ldap3.BASE, attributes=["objectClass"])
            found = bool(search_conn.entries)
            steps.append(LDAPTestStep(
                label="Base DN",
                ok=found,
                detail=f"{req.base_dn} {'found' if found else 'not found or no read access'}",
            ))
        except LDAPException as e:
            steps.append(LDAPTestStep(label="Base DN", ok=False, detail=str(e)))
        finally:
            if search_conn is not conn:
                try:
                    search_conn.unbind()
                except Exception:
                    pass
    else:
        steps.append(LDAPTestStep(
            label="Base DN",
            ok=False,
            detail="Skipped — add a service account to verify base DN access",
        ))

    try:
        conn.unbind()
    except Exception:
        pass

    return LDAPTestResponse(steps=steps)


class LDAPVerifyRequest(BaseModel):
    username: str
    password: str
    server: str
    bind_dn_template: str
    base_dn: str
    user_id_attribute: str = "uid"
    display_name_attribute: str = "cn"
    allowed_groups: list[str] = []
    search_bind_dn: Optional[str] = None
    search_bind_password: Optional[str] = None


class LDAPVerifyResponse(BaseModel):
    session_id: str
    display_name: str


def _build_user_filter(username: str, bind_dn: str, user_id_attribute: str) -> str:
    # UPN format (user@domain): search by user_id_attribute=bind_dn
    if "@" in bind_dn and "=" not in bind_dn:
        return f"({user_id_attribute}={bind_dn})"
    # DN format (uid=user,ou=...): search by uid=username
    return f"({user_id_attribute}={username})"


def _ldap_verify_sync(req: LDAPVerifyRequest) -> LDAPVerifyResponse:
    try:
        import ldap3
        from ldap3.core.exceptions import LDAPBindError, LDAPException
    except ImportError as e:
        raise HTTPException(status_code=500, detail="ldap3 is not installed") from e

    bind_dn = req.bind_dn_template.replace("{username}", req.username)

    try:
        server = ldap3.Server(req.server, get_info=ldap3.ALL, connect_timeout=10)
        conn = ldap3.Connection(server, user=bind_dn, password=req.password, auto_bind=True)
    except LDAPBindError:
        raise HTTPException(status_code=401, detail="Invalid credentials")
    except LDAPException as exc:
        log.warning(f"LDAP connection error: {exc}")
        raise HTTPException(status_code=503, detail="LDAP server unavailable")

    try:
        # If a service account is configured for group search, use it for the search.
        search_conn = conn
        if req.search_bind_dn and req.search_bind_password:
            search_conn = ldap3.Connection(
                server,
                user=req.search_bind_dn,
                password=req.search_bind_password,
                auto_bind=True,
            )

        user_filter = _build_user_filter(req.username, bind_dn, req.user_id_attribute)
        search_conn.search(
            req.base_dn,
            user_filter,
            attributes=[req.user_id_attribute, req.display_name_attribute, "cn", "memberOf"],
        )

        if not search_conn.entries:
            raise HTTPException(status_code=401, detail="User not found in directory")

        entry = search_conn.entries[0]

        def _attr(name: str) -> Optional[str]:
            try:
                val = getattr(entry, name).value
                return str(val) if val else None
            except Exception:
                return None

        session_id = _attr(req.user_id_attribute) or bind_dn
        display_name = (
            _attr(req.display_name_attribute)
            or _attr("cn")
            or req.username
        )

        if req.allowed_groups:
            member_of: list[str] = []
            try:
                attrs = entry.entry_attributes_as_dict
                for key, vals in attrs.items():
                    if key.lower() == "memberof":
                        member_of = [str(v) for v in (vals or [])]
                        break
            except Exception as e:
                log.warning(f"LDAP memberOf fetch failed: {e}")

            member_of_lower = [g.lower() for g in member_of]
            allowed_lower = [g.lower() for g in req.allowed_groups]

            if not any(g in member_of_lower for g in allowed_lower):
                raise HTTPException(
                    status_code=401,
                    detail="Your account is not authorised to use this application",
                )

    finally:
        conn.unbind()
        if req.search_bind_dn and "search_conn" in dir() and search_conn is not conn:
            search_conn.unbind()

    return LDAPVerifyResponse(session_id=session_id, display_name=display_name)


def create_auth_router() -> APIRouter:
    router = APIRouter()

    @router.post("/api/v1/auth/ldap-verify", response_model=LDAPVerifyResponse)
    async def ldap_verify(req: LDAPVerifyRequest):
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, _ldap_verify_sync, req)

    @router.post("/api/v1/auth/ldap-test", response_model=LDAPTestResponse)
    async def ldap_test(req: LDAPTestRequest):
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, _ldap_test_sync, req)

    return router
