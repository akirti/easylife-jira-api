"""JWT token validation — validates tokens issued by the main backend.

Uses shared secret_key, algorithm, issuer, audience from config.
"""
import logging
from typing import Annotated, Any, Dict, List

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from src.config import Config

logger = logging.getLogger(__name__)

security = HTTPBearer()

# Module-level config reference — set during app startup via init_auth()
_config: Config | None = None

# Error messages (constants to avoid duplicate strings)
ERR_TOKEN_EXPIRED = "Token has expired"
ERR_TOKEN_INVALID = "Invalid token"
ERR_ADMIN_REQUIRED = "Admin access required"
ERR_AUTH_NOT_INITIALIZED = "Auth module not initialized — call init_auth(config) first"

# Admin role names
ADMIN_ROLES = ("super-administrator", "administrator")


def init_auth(config: Config) -> None:
    """Initialize auth module with config. Called during app startup."""
    global _config  # noqa: PLW0603
    _config = config
    logger.info("Auth module initialized (issuer=%s)", config.get("jwt.issuer"))


def _get_jwt_settings() -> Dict[str, str]:
    """Return JWT settings from config."""
    if _config is None:
        raise RuntimeError(ERR_AUTH_NOT_INITIALIZED)
    return {
        "secret_key": _config.get("jwt.secret_key", "change-me-in-production"),
        "algorithm": _config.get("jwt.algorithm", "HS256"),
        "issuer": _config.get("jwt.issuer", "easylife-auth"),
        "audience": _config.get("jwt.audience", "easylife-api"),
    }


class CurrentUser:
    """Decoded JWT payload — mirrors main backend's CurrentUser."""

    def __init__(self, payload: Dict[str, Any]):
        self.user_id: str = payload.get("sub", "")
        self.email: str = payload.get("email", "")
        self.username: str = payload.get("username", "")
        self.roles: List[str] = payload.get("roles", [])
        self.groups: List[str] = payload.get("groups", [])
        self.payload: Dict[str, Any] = payload

    @property
    def is_admin(self) -> bool:
        """Check if user has an admin role."""
        return any(role in ADMIN_ROLES for role in self.roles)

    def __repr__(self) -> str:
        return f"CurrentUser(user_id={self.user_id!r}, email={self.email!r})"


def decode_token(token: str) -> Dict[str, Any]:
    """Decode and validate a JWT token from the main backend."""
    settings = _get_jwt_settings()
    try:
        return jwt.decode(
            token,
            settings["secret_key"],
            algorithms=[settings["algorithm"]],
            issuer=settings["issuer"],
            audience=settings["audience"],
        )
    except jwt.ExpiredSignatureError:
        logger.warning("Expired JWT token received")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=ERR_TOKEN_EXPIRED,
        )
    except jwt.InvalidTokenError as exc:
        logger.warning("Invalid JWT token: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=ERR_TOKEN_INVALID,
        )


async def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials, Depends(security)],
) -> CurrentUser:
    """FastAPI dependency — extract and validate JWT from Authorization header."""
    payload = decode_token(credentials.credentials)
    return CurrentUser(payload)


async def require_admin(
    user: Annotated[CurrentUser, Depends(get_current_user)],
) -> CurrentUser:
    """FastAPI dependency — require admin role."""
    if not user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=ERR_ADMIN_REQUIRED,
        )
    return user
