import os

from fastapi import APIRouter
from fastapi.responses import FileResponse, JSONResponse


from conversational_toolkit import __version__


def create_static_router(*, dist_path: str = "") -> APIRouter:
    static_router = APIRouter()

    @static_router.get("/", response_model=None)
    @static_router.get("/c/{path:path}", response_model=None)
    async def root() -> FileResponse | JSONResponse:
        if os.path.isfile(os.path.join(dist_path, "index.html")):
            return FileResponse(os.path.join(dist_path, "index.html"))
        else:
            return JSONResponse(content={"version": __version__})

    @static_router.get("/favicon.ico")
    async def favicon() -> FileResponse:
        return FileResponse(os.path.join(dist_path, "favicon.ico"))

    return static_router
