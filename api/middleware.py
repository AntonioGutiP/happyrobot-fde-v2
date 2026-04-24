from fastapi import Request, HTTPException
from starlette.middleware.base import BaseHTTPMiddleware
from config import get_settings

# Paths that skip auth
PUBLIC_PATHS = {"/", "/docs", "/openapi.json", "/redoc", "/api/v1/health"}


class APIKeyMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        # Skip auth for public paths and OPTIONS (CORS preflight)
        if request.url.path in PUBLIC_PATHS or request.method == "OPTIONS":
            return await call_next(request)

        # Also skip for docs assets
        if request.url.path.startswith("/docs") or request.url.path.startswith("/redoc"):
            return await call_next(request)

        api_key = request.headers.get("X-API-Key")
        settings = get_settings()

        if not api_key or api_key != settings.api_key:
            raise HTTPException(status_code=401, detail="Invalid or missing API key")

        return await call_next(request)
