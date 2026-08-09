"""Local authentication and role-based authorisation."""

from .auth import AuthError, AuthService, TokenPair
from .permissions import Action, Role, is_allowed

__all__ = ["Action", "AuthError", "AuthService", "Role", "TokenPair", "is_allowed"]
