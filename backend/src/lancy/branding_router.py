"""
Branding API — agent name and avatar customization.

GET /api/v1/branding  — public, read by all clients on load
PUT /api/v1/branding  — admin-only (enforced at Next.js middleware layer)

Config persisted in db/branding.json (gitignored).
Uploaded avatars saved to db/uploads/ and served as static files at /uploads/.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from pydantic import BaseModel

log = logging.getLogger("uvicorn")

ALLOWED_TYPES = {"image/png", "image/jpeg", "image/svg+xml", "image/webp"}
MAX_SIZE = 2 * 1024 * 1024  # 2 MB


class BrandingConfig(BaseModel):
    agent_name: str = "Lancy"
    agent_avatar_url: str | None = None


def _load(path: Path) -> BrandingConfig:
    try:
        if path.exists():
            return BrandingConfig(**json.loads(path.read_text()))
    except Exception:
        pass
    return BrandingConfig()


def create_branding_router(db_dir: Path) -> APIRouter:
    branding_path = db_dir / "branding.json"
    uploads_dir = db_dir / "uploads"
    uploads_dir.mkdir(exist_ok=True)

    router = APIRouter(prefix="/api/v1/branding")

    @router.get("", response_model=BrandingConfig)
    async def get_branding() -> BrandingConfig:
        return _load(branding_path)

    @router.put("", response_model=BrandingConfig)
    async def put_branding(
        agent_name: str = Form(""),
        avatar: UploadFile | None = File(None),
    ) -> BrandingConfig:
        cfg = _load(branding_path)

        if agent_name.strip():
            cfg.agent_name = agent_name.strip()

        if avatar and avatar.filename:
            ct = avatar.content_type or ""
            if ct not in ALLOWED_TYPES:
                raise HTTPException(400, "Unsupported file type. Use PNG, JPEG, WebP, or SVG.")
            data = await avatar.read()
            if len(data) > MAX_SIZE:
                raise HTTPException(400, "Avatar file too large (max 2 MB).")
            suffix = Path(avatar.filename).suffix.lower() or ".png"
            dest = uploads_dir / f"avatar{suffix}"
            dest.write_bytes(data)
            cfg.agent_avatar_url = f"/uploads/{dest.name}"

        branding_path.write_text(json.dumps(cfg.model_dump(), indent=2))
        log.info(f"Branding updated: agent_name={cfg.agent_name!r}, avatar={cfg.agent_avatar_url!r}")
        return cfg

    @router.delete("/avatar", response_model=BrandingConfig)
    async def delete_avatar() -> BrandingConfig:
        cfg = _load(branding_path)
        if cfg.agent_avatar_url:
            fname = cfg.agent_avatar_url.lstrip("/").removeprefix("uploads/")
            dest = uploads_dir / fname
            dest.unlink(missing_ok=True)
            cfg.agent_avatar_url = None
            branding_path.write_text(json.dumps(cfg.model_dump(), indent=2))
        return cfg

    return router
