import json
import os
import time
import uuid
from typing import Any

VERSION = "0.1.0"
DEFAULT_MODEL_ID = os.environ.get("MODEL_ID", "llama")

_BUILTIN_MODELS: dict[str, str] = {
    "llama": "meta/llama-3.3-70b-instruct-maas",
    "llama3.3": "meta/llama-3.3-70b-instruct-maas",
    "llama-3.3-70b": "meta/llama-3.3-70b-instruct-maas",
    "meta/llama-3.3-70b-instruct-maas": "meta/llama-3.3-70b-instruct-maas",
    "gpt-oss": "openai/gpt-oss-20b-maas",
    "gpt-oss-20b": "openai/gpt-oss-20b-maas",
    "openai/gpt-oss-20b-maas": "openai/gpt-oss-20b-maas",
}

_RAW_MODEL_PREFIXES = ("meta/", "openai/", "google/", "publishers/")


def _load_model_map() -> dict[str, str]:
    mapping = dict(_BUILTIN_MODELS)
    raw = os.environ.get("MODEL_MAP", "").strip()
    if not raw:
        return mapping
    try:
        extra = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"MODEL_MAP must be valid JSON: {exc}") from exc
    if not isinstance(extra, dict):
        raise RuntimeError("MODEL_MAP must be a JSON object of alias → model id")
    for key, value in extra.items():
        if not isinstance(key, str) or not isinstance(value, str):
            raise RuntimeError("MODEL_MAP keys and values must be strings")
        mapping[key] = value
    return mapping


MODEL_MAP = _load_model_map()


def resolve_model(request_model: Any) -> tuple[str, str]:
    """Return (response_model_name, vertex_model_id)."""
    if request_model is None or request_model == "":
        name = DEFAULT_MODEL_ID
        if name in MODEL_MAP:
            return name, MODEL_MAP[name]
        return name, name
    if not isinstance(request_model, str):
        raise ValueError("model must be a string")

    name = request_model.strip()
    if name in MODEL_MAP:
        return name, MODEL_MAP[name]
    if name.startswith(_RAW_MODEL_PREFIXES):
        return name, name

    known = ", ".join(sorted(MODEL_MAP))
    raise ValueError(f"unknown model '{name}'; known: {known}")


def parse_sampling(payload: dict[str, Any]) -> tuple[int, float | None, float | None]:
    max_tokens = payload.get("max_tokens", 512)
    if not isinstance(max_tokens, int) or max_tokens < 1 or max_tokens > 8192:
        raise ValueError("max_tokens must be an integer between 1 and 8192")

    temperature = payload.get("temperature")
    if temperature is not None and (
        not isinstance(temperature, (int, float)) or temperature < 0 or temperature > 2
    ):
        raise ValueError("temperature must be a number between 0 and 2")

    top_p = payload.get("top_p")
    if top_p is not None and (
        not isinstance(top_p, (int, float)) or top_p <= 0 or top_p > 1
    ):
        raise ValueError("top_p must be a number between 0 and 1")

    return (
        max_tokens,
        float(temperature) if temperature is not None else None,
        float(top_p) if top_p is not None else None,
    )


def normalize_messages(payload: dict[str, Any]) -> list[dict[str, str]]:
    messages = payload.get("messages")
    if isinstance(messages, list) and messages:
        normalized: list[dict[str, str]] = []
        for item in messages:
            if not isinstance(item, dict):
                raise ValueError("each message must be an object")
            role = item.get("role")
            content = item.get("content")
            if role not in ("system", "user", "assistant"):
                raise ValueError("message.role must be system, user, or assistant")
            if not isinstance(content, str):
                raise ValueError("message.content must be a string")
            normalized.append({"role": role, "content": content})
        if not any(m["role"] == "user" for m in normalized):
            raise ValueError("at least one user message is required")
        return normalized

    prompt = payload.get("prompt")
    if not isinstance(prompt, str) or not prompt.strip():
        raise ValueError("messages or prompt is required")

    normalized: list[dict[str, str]] = []
    system = payload.get("system")
    if isinstance(system, str) and system.strip():
        normalized.append({"role": "system", "content": system})
    normalized.append({"role": "user", "content": prompt})
    return normalized


def openai_completion(model: str, text: str, usage: dict[str, int]) -> dict[str, Any]:
    prompt_tokens = int(usage.get("prompt_tokens", 0) or 0)
    completion_tokens = int(usage.get("completion_tokens", 0) or 0)
    return {
        "id": f"chatcmpl-{uuid.uuid4().hex[:24]}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": text},
                "finish_reason": "stop",
            }
        ],
        "usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
        },
    }


def new_stream_ids() -> tuple[str, int]:
    return f"chatcmpl-{uuid.uuid4().hex[:24]}", int(time.time())


def sse(data: str) -> str:
    return f"data: {data}\n\n"


def openai_chunk(
    *,
    completion_id: str,
    created: int,
    model: str,
    delta: dict[str, Any],
    finish_reason: str | None = None,
) -> dict[str, Any]:
    return {
        "id": completion_id,
        "object": "chat.completion.chunk",
        "created": created,
        "model": model,
        "choices": [
            {
                "index": 0,
                "delta": delta,
                "finish_reason": finish_reason,
            }
        ],
    }


def sse_chunk(
    completion_id: str,
    created: int,
    model: str,
    delta: dict[str, Any],
    finish_reason: str | None = None,
) -> str:
    return sse(
        json.dumps(
            openai_chunk(
                completion_id=completion_id,
                created=created,
                model=model,
                delta=delta,
                finish_reason=finish_reason,
            )
        )
    )


def extract_usage(body: dict[str, Any]) -> dict[str, int]:
    usage = body.get("usage") or {}
    if not isinstance(usage, dict):
        return {"prompt_tokens": 0, "completion_tokens": 0}
    prompt = usage.get("prompt_tokens", usage.get("input_tokens", 0))
    completion = usage.get("completion_tokens", usage.get("output_tokens", 0))
    return {
        "prompt_tokens": int(prompt or 0),
        "completion_tokens": int(completion or 0),
    }


def extract_text(body: dict[str, Any]) -> str:
    choices = body.get("choices")
    if isinstance(choices, list) and choices:
        choice = choices[0] or {}
        message = choice.get("message") or {}
        if isinstance(message, dict):
            content = message.get("content")
            if isinstance(content, str):
                return content
        text = choice.get("text")
        if isinstance(text, str):
            return text
    return ""
