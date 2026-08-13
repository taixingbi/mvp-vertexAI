from collections.abc import Iterator

from fastapi import FastAPI, Header, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse

from app import auth
from app.models import (
    VERSION,
    normalize_messages,
    openai_completion,
    parse_sampling,
    resolve_model,
)
from app.vertex_client import VertexError, chat_completion, stream_chat_completion

app = FastAPI(title="mvp-vertexAI", version=VERSION)


def _vertex_error(exc: Exception) -> JSONResponse:
    if isinstance(exc, VertexError):
        status = exc.status_code
        detail = exc.detail
        if status == 429:
            return JSONResponse(
                status_code=429,
                content={"error": "rate limited", "detail": detail},
            )
        if 400 <= status < 500 and status != 401:
            # Upstream client errors (bad model, etc.) — keep gateway contract simple.
            return JSONResponse(
                status_code=502,
                content={"error": "vertex request failed", "detail": detail},
            )
        return JSONResponse(
            status_code=502,
            content={"error": "vertex request failed", "detail": detail},
        )
    return JSONResponse(
        status_code=502,
        content={"error": "vertex request failed", "detail": str(exc)},
    )


async def _parse_chat_payload(
    request: Request,
    x_api_key: str | None,
    authorization: str | None,
):
    if not auth.authorized(x_api_key, authorization):
        return auth.unauthorized()

    try:
        payload = await request.json()
    except Exception:  # noqa: BLE001
        return JSONResponse(status_code=400, content={"error": "invalid JSON body"})

    try:
        messages = normalize_messages(payload)
        max_tokens, temperature, top_p = parse_sampling(payload)
        response_model, vertex_model_id = resolve_model(payload.get("model"))
    except ValueError as exc:
        return JSONResponse(status_code=400, content={"error": str(exc)})

    return messages, max_tokens, temperature, top_p, response_model, vertex_model_id, payload


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/version")
async def version() -> dict[str, str]:
    return {"version": VERSION, "provider": "vertex"}


@app.options("/{full_path:path}")
async def options(full_path: str) -> Response:  # noqa: ARG001
    return Response(
        status_code=204,
        headers={
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Headers": "content-type,x-api-key,authorization",
            "Access-Control-Allow-Methods": "GET,POST,OPTIONS",
        },
    )


@app.post("/v1/chat/completions")
async def chat_completions(
    request: Request,
    x_api_key: str | None = Header(default=None),
    authorization: str | None = Header(default=None),
) -> Response:
    parsed = await _parse_chat_payload(request, x_api_key, authorization)
    if isinstance(parsed, JSONResponse):
        return parsed

    messages, max_tokens, temperature, top_p, response_model, vertex_model_id, payload = (
        parsed
    )
    stream = bool(payload.get("stream"))

    if stream:
        try:
            generator = stream_chat_completion(
                vertex_model_id=vertex_model_id,
                messages=messages,
                max_tokens=max_tokens,
                temperature=temperature,
                top_p=top_p,
                response_model=response_model,
            )
            first = next(generator)
        except Exception as exc:  # noqa: BLE001
            return _vertex_error(exc)

        def event_stream() -> Iterator[str]:
            yield first
            yield from generator

        return StreamingResponse(
            event_stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
            },
        )

    try:
        inferred = chat_completion(
            vertex_model_id=vertex_model_id,
            messages=messages,
            max_tokens=max_tokens,
            temperature=temperature,
            top_p=top_p,
        )
    except Exception as exc:  # noqa: BLE001
        return _vertex_error(exc)

    return JSONResponse(
        content=openai_completion(response_model, inferred["text"], inferred["usage"])
    )
