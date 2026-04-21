"""
Authentication provider abstractions.

An 'AuthProvider' integrates with a FastAPI application to identify the current
user on every request. The toolkit ships two implementations:

'SessionCookieProvider' issues JWT cookies and exposes '/auth/refresh'.
'PasscodeProvider' wraps another provider and adds a passcode gate via
middleware and a '/passcode' route.
"""

from abc import ABC, abstractmethod
from fastapi import Request, FastAPI


class AuthProvider(ABC):
    """
    Abstract base class for authentication backends.

    Implementors must supply a FastAPI dependency that resolves to the current
    user ID ('get_current_user_id') and a setup hook that registers all required
    routes and middleware with the application ('bind_to_app').
    """

    @abstractmethod
    def get_current_user_id(self, request: Request) -> str:
        """FastAPI dependency that returns the authenticated user's ID.

        Raise 'HTTPException' with status 401 if the request is not authenticated.
        """
        pass

    @abstractmethod
    def bind_to_app(self, app: FastAPI) -> None:
        """Register routes and middleware required by this provider.

        The frontend expects '/auth/refresh' to be available after binding.
        """
        pass
