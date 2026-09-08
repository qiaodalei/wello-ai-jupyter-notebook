"""Two agent runs at the same time, in two notebooks.

Asks A a question, switches to B and asks another, then watches both to the end:
each run must write only into its own notebook and label only its own chat.
Needs a configured model. Run: PYTHONPATH=backend python scripts/test_parallel_agents.py
"""

from __future__ import annotations

import asyncio
import json
import sys

import httpx
import websockets

BASE = "http://127.0.0.1:8000"
CHAT_EVENTS = ("agent_delta", "agent_message", "agent_round_end")
ok = True


def check(name: str, passed: bool, detail: str = "") -> None:
    global ok
    ok = ok and passed
    print(f"{'PASS' if passed else 'FAIL'}  {name}{f'  — {detail}' if detail else ''}")


async def main() -> None:
    async with websockets.connect("ws://127.0.0.1:8000/ws") as ws:
        async with httpx.AsyncClient(base_url=BASE, timeout=60) as c:
            a = (await c.post("/api/notebook/new")).json()["name"]
            b = (await c.post("/api/notebook/new")).json()["name"]

            await c.post("/api/notebook/open", json={"name": a})
            await c.post("/api/agent/reset")
            start_a = await c.post("/api/agent/chat", json={"message": "用两步算 1 到 100 的和"})
            check("A 开跑", start_a.json().get("notebook") == a, str(start_a.json()))

            # Switch and immediately put the other notebook to work as well.
            await c.post("/api/notebook/open", json={"name": b})
            await c.post("/api/agent/reset")
            start_b = await c.post("/api/agent/chat", json={"message": "用两步算 10 的阶乘"})
            check("B 也能同时开跑", start_b.status_code == 200 and start_b.json().get("notebook") == b, str(start_b.json()))

            session = (await c.get("/api/agent/session")).json()
            check("两本同时在跑", {a, b} <= set(session["running_notebooks"]), str(session["running_notebooks"]))

            # Follow both runs to the end, keeping each notebook's traffic apart.
            counts = {a: 0, b: 0}
            unlabelled = 0
            done: set[str] = set()
            while done != {a, b}:
                try:
                    ev = json.loads(await asyncio.wait_for(ws.recv(), timeout=240))
                except asyncio.TimeoutError:
                    check("两个任务都跑完", False, f"超时，已完成 {done}")
                    break
                kind = ev.get("type")
                where = ev.get("notebook")
                if kind in CHAT_EVENTS:
                    if where in counts:
                        counts[where] += 1
                    else:
                        unlabelled += 1
                if kind == "agent_status" and ev.get("phase") == "idle" and where in counts:
                    done.add(where)

            check("两边都收到自己的流式输出", counts[a] > 0 and counts[b] > 0, str(counts))
            check("没有无归属的 chat 事件", unlabelled == 0, f"{unlabelled} 个")

            # Kernels boot on a notebook's first execution, so check them after.
            health = (await c.get("/api/health")).json()
            check("各自跑在自己的内核里", {a, b} <= set(health["live_kernels"]), str(health["live_kernels"]))

            # Each notebook holds its own work and its own conversation.
            for name, other in ((a, b), (b, a)):
                nb = (await c.post("/api/notebook/open", json={"name": name})).json()
                sess = (await c.get("/api/agent/session")).json()
                ai = [x for x in nb["cells"] if (x.get("metadata") or {}).get("ai_generated")]
                ran = [x for x in nb["cells"] if x.get("outputs")]
                check(f"{name} 有自己的单元格和输出", len(ai) >= 2 and len(ran) >= 1, f"{len(ai)} AI / {len(ran)} 有输出")
                check(f"{name} 的问答只有自己的", len(sess["messages"]) >= 2, f"{len(sess['messages'])} 条")
                asked = next((m["content"] for m in sess["messages"] if m["role"] == "user"), "")
                mine = "100" if name == a else "阶乘"
                check(f"{name} 记的是自己的问题", mine in asked, asked[:40])

            for name in (a, b):
                await c.delete(f"/api/files/{name}")

    print("\nall good" if ok else "\nFAILED")
    sys.exit(0 if ok else 1)


asyncio.run(main())
