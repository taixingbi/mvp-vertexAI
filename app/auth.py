import os

from fastapi.responses import JSONResponse

API_KEY = os.environ.get("API_KEY", "")


def authorized(x_api_key: str | None, authorization: str | None) -> bool:
    if not API_KEY:
        return False
    if x_api_key == API_KEY:
        return True
    if authorization and authorization.lower().startswith("bearer "):
        return authorization[7:].strip() == API_KEY
    return False


def unauthorized() -> JSONResponse:
    return JSONResponse(status_code=401, content={"error": "unauthorized"})
