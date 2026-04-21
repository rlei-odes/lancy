import os

from fastapi import APIRouter, HTTPException, Response, status, FastAPI, Request
from fastapi.responses import FileResponse, RedirectResponse
from pydantic import BaseModel
from starlette.middleware.base import BaseHTTPMiddleware

from conversational_toolkit.api.auth.base import AuthProvider


class PasscodeInput(BaseModel):
    passcode: str


class PasscodeMiddleware(BaseHTTPMiddleware):
    def __init__(
        self,
        app,
        passcode: str,
        cookie_name: str,
        passcode_path: str = "/passcode",
        passcode_check_path: str = "/passcode-check",
        url_prefix: str = "",
    ):
        super().__init__(app)
        self.passcode = passcode
        self.cookie_name = cookie_name
        self.exempt_paths = [
            passcode_path,
            passcode_check_path,
            "/_next",
            "/favicon.ico",
            "/robots.txt",
            "/assets/logo.png",
        ]
        self.passcode_path = passcode_path
        self.url_prefix = url_prefix

    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if any(
            path == self.url_prefix + exempt or path.startswith(self.url_prefix + exempt + "/")
            for exempt in self.exempt_paths
        ):
            return await call_next(request)

        cookie_value = request.cookies.get(self.cookie_name)
        if cookie_value != self.passcode:
            return RedirectResponse(url=self.url_prefix + self.passcode_path, status_code=302)

        return await call_next(request)


class PasscodeProvider(AuthProvider):
    def __init__(
        self,
        auth_provider: AuthProvider,
        passcode: str = "1234",
        env: str = "local",
        dist_path: str = "dist",
        url_prefix: str = "",
    ):
        self.auth_provider = auth_provider
        self.passcode = passcode
        self.env = env
        self.dist_path = dist_path
        self.url_prefix = url_prefix
        self.cookie_name = "passcode"

    def bind_to_app(self, app: FastAPI) -> None:
        router = APIRouter()

        @router.get("/passcode")
        async def passcode_page():
            return FileResponse(os.path.join(self.dist_path, "passcode.html"))

        @router.post("/passcode-check")
        async def check_passcode(payload: PasscodeInput, response: Response):
            if payload.passcode == self.passcode:
                response.set_cookie(
                    key=self.cookie_name,
                    value=payload.passcode,
                    httponly=True,
                    secure=False if self.env == "local" else True,
                    path="/",
                )
                return {"success": True}
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid passcode")

        app.include_router(router)
        app.add_middleware(
            PasscodeMiddleware,  # type: ignore
            passcode=self.passcode,
            cookie_name=self.cookie_name,
            url_prefix=self.url_prefix,
        )

        self.auth_provider.bind_to_app(app)

    def get_current_user_id(self, request: Request) -> str:
        return self.auth_provider.get_current_user_id(request)
