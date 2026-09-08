"""Check that the agent works the way a person works a notebook.

Starts a fresh notebook, sends one prompt, then reports the cell sequence and
whether writes and runs actually interleaved (write, run, read, write ...).

Usage: python scripts/cadence_probe.py "your prompt"
"""

from __future__ import annotations

import asyncio
import json
import sys

import httpx
import websockets

BASE = "http://127.0.0.1:8000"


async def main() -> None:
    prompt = sys.argv[1] if len(sys.argv) > 1 else "分析一组销售数据，按月汇总并画柱状图"
    async with websockets.connect("ws://127.0.0.1:8000/ws") as ws:
        async with httpx.AsyncClient(timeout=60) as client:
            await client.post(f"{BASE}/api/agent/reset")
            await client.post(f"{BASE}/api/notebook/new")
            await client.post(f"{BASE}/api/agent/chat", json={"message": prompt})

            # Track the shape of the run: how writes and runs interleaved.
            timeline: list[str] = []
            seen_cells: set[str] = set()
            while True:
                try:
                    event = json.loads(await asyncio.wait_for(ws.recv(), timeout=180))
                except asyncio.TimeoutError:
                    print("!! stalled")
                    break
                kind = event.get("type")
                if kind == "notebook":
                    cells = event["doc"]["cells"]
                    fresh = [c for c in cells if c["id"] not in seen_cells]
                    seen_cells.update(c["id"] for c in cells)
                    for cell in fresh:
                        timeline.append("W" if cell["cell_type"] == "code" else "M")
                elif kind == "cell_running":
                    timeline.append("R")
                elif kind == "agent_status" and event.get("phase") == "idle":
                    break

            nb = (await client.get(f"{BASE}/api/notebook")).json()

    print("timeline (M=markdown W=code R=run):", "".join(timeline))
    print()
    for i, cell in enumerate(nb["cells"]):
        head = (cell.get("source") or "").strip().splitlines()
        first = head[0][:64] if head else "(empty)"
        lines = len(head)
        mark = ""
        if cell["cell_type"] == "code":
            mark = " [ran]" if cell.get("outputs") else " [NO OUTPUT]"
        print(f"{i:2d} {cell['cell_type']:8s} {lines:2d}L{mark}  {first}")

    cells = nb["cells"]
    code = [c for c in cells if c["cell_type"] == "code"]
    md = [c for c in cells if c["cell_type"] == "markdown"]
    unrun = [c["id"] for c in code if not c.get("outputs")]
    biggest = max((len((c.get("source") or "").splitlines()) for c in code), default=0)
    # A person's notebook alternates; a dump has code cells back to back.
    pairs = sum(
        1
        for a, b in zip(cells, cells[1:])
        if a["cell_type"] == "code" and b["cell_type"] == "code"
    )
    print()
    print(f"markdown={len(md)} code={len(code)} 未执行={len(unrun)}")
    print(f"最大 code 单元格行数={biggest} 连续 code 相邻对={pairs}")
    print("每步都跑了" if not unrun else f"有没跑的单元格：{unrun}")


asyncio.run(main())
