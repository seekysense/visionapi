from fastapi import HTTPException, Security, status
from fastapi.security.api_key import APIKeyHeader

from app.config import get_settings

_reader_header = APIKeyHeader(name="X-API-Key", auto_error=False)
_admin_header  = APIKeyHeader(name="X-Admin-Key", auto_error=False)


async def require_api_key(key: str = Security(_reader_header)) -> str:
    if not key or key != get_settings().api_password:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing API key",
            headers={"WWW-Authenticate": "ApiKey"},
        )
    return key


async def require_admin_key(key: str = Security(_admin_header)) -> str:
    if not key or key != get_settings().api_admin_password:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing admin key",
            headers={"WWW-Authenticate": "ApiKey"},
        )
    return key
