import json
import os
from collections.abc import Iterator
from typing import Any

import google.auth
import google.auth.transport.requests
import httpx

from app.models import (
    extract_text,
    extract_usage,
    new_stream_ids,
    sse,
    sse_chunk,
)

PROJECT_ID = os.environ.get("PROJECT_ID", "")
LOCATION = os.environ.get("LOCATION", "us-central1")
TIMEOUT = float(os.environ.get("VERTEX_TIMEOUT", "120"))


class VertexError(Exception):
    def __init__(self, status_code: int, detail: str):
        self.status_code = status_code
        self.detail = detail
        super().__init__(detail)


def _require_project() -> str:
    if not PROJECT_ID:
        raise VertexError(502, "PROJECT_ID env is required")
    return PROJECT_ID


def _endpoint_url() -> str:
    project = _require_project()
    return (
        f"https://{LOCATION}-aiplatform.googleapis.com/v1beta1/"
        f"projects/{project}/locations/{LOCATION}/endpoints/openapi/chat/completions"
    )


def _access_token() -> str:
    credentials, _ = google.auth.default(
        scopes=["https://www.googleapis.com/auth/cloud-platform"]
    )
    credentials.refresh(google.auth.transport.requests.Request())
    token = getattr(credentials, "token", None)
    if not token:
        raise VertexError(502, "failed to refresh Google access token")
    return token


def _build_body(
    *,
    vertex_model_id: str,
    messages: list[dict[str, str]],
    max_tokens: int,
    temperature: float | None,
    top_p: float | None,
    stream: bool,
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "model": vertex_model_id,
        "messages": messages,
        "max_tokens": max_tokens,
        "stream": stream,
    }
    if temperature is not None:
        body["temperature"] = temperature
    if top_p is not None:
        body["top_p"] = top_p
    return body


def _raise_for_status(response: httpx.Response) -> None:
    if response.status_code < 400:
        return
    detail = response.text
    try:
        payload = response.json()
        if isinstance(payload, dict):
            err = payload.get("error")
            if isinstance(err, dict) and err.get("message"):
                detail = str(err["message"])
            elif payload.get("detail"):
                detail = str(payload["detail"])
            elif isinstance(err, str):
                detail = err
    except Exception:  # noqa: BLE001
        pass
    raise VertexError(response.status_code, detail)


def chat_completion(
    *,
    vertex_model_id: str,
    messages: list[dict[str, str]],
    max_tokens: int,
    temperature: float | None,
    top_p: float | None,
) -> dict[str, Any]:
    body = _build_body(
        vertex_model_id=vertex_model_id,
        messages=messages,
        max_tokens=max_tokens,
        temperature=temperature,
        top_p=top_p,
        stream=False,
    )
    headers = {
        "Authorization": f"Bearer {_access_token()}",
        "Content-Type": "application/json",
    }
    with httpx.Client(timeout=TIMEOUT) as client:
        response = client.post(_endpoint_url(), headers=headers, json=body)
    _raise_for_status(response)
    payload = response.json()
    if not isinstance(payload, dict):
        raise VertexError(502, "vertex returned non-object JSON")
    return {
        "text": extract_text(payload),
        "usage": extract_usage(payload),
        "raw": payload,
    }


def _parse_sse_data_line(line: str) -> str | None:
    if not line.startswith("data:"):
        return None
    return line[5:].lstrip()


def stream_chat_completion(
    *,
    vertex_model_id: str,
    messages: list[dict[str, str]],
    max_tokens: int,
    temperature: float | None,
    top_p: float | None,
    response_model: str,
) -> Iterator[str]:
    """Yield Bedrock-compatible SSE frames; raises VertexError on setup failure."""
    body = _build_body(
        vertex_model_id=vertex_model_id,
        messages=messages,
        max_tokens=max_tokens,
        temperature=temperature,
        top_p=top_p,
        stream=True,
    )
    headers = {
        "Authorization": f"Bearer {_access_token()}",
        "Content-Type": "application/json",
        "Accept": "text/event-stream",
    }

    client = httpx.Client(timeout=TIMEOUT)
    try:
        with client.stream(
            "POST", _endpoint_url(), headers=headers, json=body
        ) as response:
            if response.status_code >= 400:
                # Consume body for error detail before raising.
                detail = response.read().decode("utf-8", errors="replace")
                try:
                    payload = json.loads(detail)
                    if isinstance(payload, dict):
                        err = payload.get("error")
                        if isinstance(err, dict) and err.get("message"):
                            detail = str(err["message"])
                except Exception:  # noqa: BLE001
                    pass
                raise VertexError(response.status_code, detail)

            completion_id, created = new_stream_ids()
            # Emit role-first chunk to match Bedrock MVP SSE contract.
            yield sse_chunk(
                completion_id, created, response_model, {"role": "assistant", "content": ""}
            )

            finish_reason: str | None = None
            for line in response.iter_lines():
                if not line:
                    continue
                data = _parse_sse_data_line(line)
                if data is None:
                    continue
                if data.strip() == "[DONE]":
                    break
                try:
                    chunk = json.loads(data)
                except json.JSONDecodeError:
                    continue
                if not isinstance(chunk, dict):
                    continue

                choices = chunk.get("choices")
                if not isinstance(choices, list) or not choices:
                    continue
                choice = choices[0] or {}
                fr = choice.get("finish_reason")
                if isinstance(fr, str) and fr:
                    finish_reason = fr

                delta = choice.get("delta") or {}
                if isinstance(delta, dict):
                    content = delta.get("content")
                    if isinstance(content, str) and content:
                        yield sse_chunk(
                            completion_id, created, response_model, {"content": content}
                        )
                    continue

                # Some upstreams put full message on stream chunks.
                message = choice.get("message") or {}
                if isinstance(message, dict):
                    content = message.get("content")
                    if isinstance(content, str) and content:
                        yield sse_chunk(
                            completion_id, created, response_model, {"content": content}
                        )

            yield sse_chunk(
                completion_id,
                created,
                response_model,
                {},
                finish_reason=finish_reason or "stop",
            )
            yield sse("[DONE]")
    finally:
        client.close()
