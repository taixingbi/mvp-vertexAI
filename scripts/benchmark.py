#!/usr/bin/env python3
"""Minimal concurrency benchmark for mvp-vertexAI.

Usage:
  export GATEWAY_URL='https://....run.app'
  export API_KEY='...'
  python scripts/benchmark.py --model llama --concurrency 8 --requests 32 --max-tokens 64 --stream
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import statistics
import time
from dataclasses import dataclass, field

import httpx


@dataclass
class Result:
    ok: bool
    status: int
    latency_s: float
    ttft_s: float | None = None
    prompt_tokens: int = 0
    completion_tokens: int = 0
    error: str = ""


@dataclass
class Aggregate:
    results: list[Result] = field(default_factory=list)

    def add(self, r: Result) -> None:
        self.results.append(r)

    def summary(self) -> dict:
        n = len(self.results)
        oks = [r for r in self.results if r.ok]
        lat = [r.latency_s for r in oks]
        ttfts = [r.ttft_s for r in oks if r.ttft_s is not None]
        prompt = sum(r.prompt_tokens for r in oks)
        completion = sum(r.completion_tokens for r in oks)
        total_out_time = sum(r.latency_s for r in oks) or 1e-9
        rate_429 = sum(1 for r in self.results if r.status == 429) / n if n else 0
        rate_5xx = sum(1 for r in self.results if r.status >= 500) / n if n else 0
        success = len(oks) / n if n else 0

        def pct(values: list[float], p: float) -> float | None:
            if not values:
                return None
            ordered = sorted(values)
            idx = min(len(ordered) - 1, max(0, int(round((p / 100) * (len(ordered) - 1)))))
            return ordered[idx]

        return {
            "n": n,
            "success_rate": success,
            "p50_s": pct(lat, 50),
            "p95_s": pct(lat, 95),
            "mean_s": statistics.mean(lat) if lat else None,
            "ttft_p50_s": pct(ttfts, 50),
            "ttft_p95_s": pct(ttfts, 95),
            "tps_out": (completion / total_out_time) if oks else 0.0,
            "prompt_tokens": prompt,
            "completion_tokens": completion,
            "rate_429": rate_429,
            "rate_5xx": rate_5xx,
        }


async def one_request(
    client: httpx.AsyncClient,
    *,
    url: str,
    api_key: str,
    model: str,
    max_tokens: int,
    stream: bool,
    prompt: str,
) -> Result:
    body = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": max_tokens,
        "temperature": 0,
        "stream": stream,
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    t0 = time.perf_counter()
    try:
        if not stream:
            resp = await client.post(url, headers=headers, json=body)
            latency = time.perf_counter() - t0
            if resp.status_code >= 400:
                return Result(False, resp.status_code, latency, error=resp.text[:300])
            data = resp.json()
            usage = data.get("usage") or {}
            return Result(
                True,
                resp.status_code,
                latency,
                prompt_tokens=int(usage.get("prompt_tokens") or 0),
                completion_tokens=int(usage.get("completion_tokens") or 0),
            )

        ttft: float | None = None
        async with client.stream("POST", url, headers=headers, json=body) as resp:
            if resp.status_code >= 400:
                body_text = (await resp.aread()).decode("utf-8", errors="replace")
                latency = time.perf_counter() - t0
                return Result(False, resp.status_code, latency, error=body_text[:300])
            async for line in resp.aiter_lines():
                if not line.startswith("data:"):
                    continue
                data = line[5:].lstrip()
                if data == "[DONE]":
                    break
                try:
                    chunk = json.loads(data)
                except json.JSONDecodeError:
                    continue
                choices = chunk.get("choices") or []
                if not choices:
                    continue
                delta = (choices[0] or {}).get("delta") or {}
                content = delta.get("content")
                if isinstance(content, str) and content and ttft is None:
                    ttft = time.perf_counter() - t0
        latency = time.perf_counter() - t0
        return Result(True, 200, latency, ttft_s=ttft)
    except Exception as exc:  # noqa: BLE001
        latency = time.perf_counter() - t0
        return Result(False, 0, latency, error=str(exc))


async def run(args: argparse.Namespace) -> None:
    gateway = args.gateway.rstrip("/")
    url = f"{gateway}/v1/chat/completions"
    api_key = args.api_key
    prompt = args.prompt or ("hello " * max(1, args.prompt_tokens // 2)).strip()

    sem = asyncio.Semaphore(args.concurrency)
    agg = Aggregate()

    async with httpx.AsyncClient(timeout=args.timeout) as client:

        async def bounded() -> None:
            async with sem:
                r = await one_request(
                    client,
                    url=url,
                    api_key=api_key,
                    model=args.model,
                    max_tokens=args.max_tokens,
                    stream=args.stream,
                    prompt=prompt,
                )
                agg.add(r)

        await asyncio.gather(*[bounded() for _ in range(args.requests)])

    s = agg.summary()
    row = {
        "provider": "vertex",
        "model": args.model,
        "concurrency": args.concurrency,
        "requests": args.requests,
        "stream": args.stream,
        **s,
    }
    print(json.dumps(row, indent=2))
    print()
    print(
        f"{'Provider':<10} {'Model':<10} {'C':>3} {'TTFT_p50':>10} {'P50':>8} {'P95':>8} "
        f"{'TPS':>8} {'OK':>6} {'429':>6} {'5xx':>6}"
    )
    ttft = f"{s['ttft_p50_s']:.3f}" if s["ttft_p50_s"] is not None else "-"
    p50 = f"{s['p50_s']:.3f}" if s["p50_s"] is not None else "-"
    p95 = f"{s['p95_s']:.3f}" if s["p95_s"] is not None else "-"
    print(
        f"{'vertex':<10} {args.model:<10} {args.concurrency:>3} {ttft:>10} {p50:>8} {p95:>8} "
        f"{s['tps_out']:>8.2f} {s['success_rate']:>6.1%} {s['rate_429']:>6.1%} {s['rate_5xx']:>6.1%}"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Benchmark mvp-vertexAI gateway")
    parser.add_argument("--gateway", default=os.environ.get("GATEWAY_URL", ""))
    parser.add_argument("--api-key", default=os.environ.get("API_KEY", ""))
    parser.add_argument("--model", default="llama")
    parser.add_argument("--concurrency", type=int, default=1)
    parser.add_argument("--requests", type=int, default=8)
    parser.add_argument("--max-tokens", type=int, default=64)
    parser.add_argument("--prompt-tokens", type=int, default=32)
    parser.add_argument("--prompt", default="")
    parser.add_argument("--stream", action="store_true")
    parser.add_argument("--timeout", type=float, default=120.0)
    args = parser.parse_args()
    if not args.gateway:
        raise SystemExit("set --gateway or GATEWAY_URL")
    if not args.api_key:
        raise SystemExit("set --api-key or API_KEY")
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
