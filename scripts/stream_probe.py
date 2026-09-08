"""Measure how quickly the agent's text starts reaching the browser.

Sends one prompt, then prints a timeline of the websocket events a browser
would see. Usage: python scripts/stream_probe.py "your prompt"
"""

from __future__ import annotations

import asyncio
import json
import sys
import time

import httpx
import websockets

BASE = "http://127.0.0.1:8000"
WS = "ws://127.0.0.1:8000/ws"


async def main() -> None:
    prompt = sys.argv[1] if len(sys.argv) > 1 else "算一下 2**100 有多少位"
    async with websockets.connect(WS) as ws:
        t0 = time.monotonic()
        async with httpx.AsyncClient(timeout=30) as client:
            await client.post(f"{BASE}/api/agent/chat", json={"message": prompt})

        counts: dict[str, int] = {}
        firsts: dict[str, float] = {}
        chars = {"reasoning": 0, "content": 0}
        # Per round: streamed tail vs. what gets kept for the folded trace.
        streamed_round = 0
        kept: list[tuple[int, int]] = []
        while True:
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=120)
            except asyncio.TimeoutError:
                print("!! no event for 120s")
                return
            event = json.loads(raw)
            kind = event.get("type", "?")
            label = kind
            if kind == "agent_delta":
                label = f"agent_delta:{event.get('kind')}"
                chars[event["kind"]] = chars.get(event["kind"], 0) + len(event.get("text", ""))
                if event["kind"] == "reasoning":
                    streamed_round += len(event.get("text", ""))
            if kind == "agent_round_end":
                kept.append((streamed_round, len(event.get("reasoning") or "")))
                streamed_round = 0
            elapsed = time.monotonic() - t0
            counts[label] = counts.get(label, 0) + 1
            if label not in firsts:
                firsts[label] = elapsed
                print(f"{elapsed:6.2f}s  first {label}")
            if kind == "agent_status" and event.get("phase") == "idle":
                print(f"{elapsed:6.2f}s  done")
                break

        print("\nevent counts:", json.dumps(counts, indent=2, sort_keys=True))
        print("streamed chars:", chars)

        # The folded trace must carry the whole round, not just the live tail.
        rounds = [(s, k) for s, k in kept if s or k]
        print("\n每轮思考 流式字数 / 保留字数:", rounds)
        lost = [(s, k) for s, k in rounds if k != s]
        print("思考完整保留" if not lost else f"!! 有丢失或不一致：{lost}")


asyncio.run(main())
