"""Switching notebooks mid-run: free to do, and nothing leaks.

Starts a real agent task in A, switches to B while it works, uses B, and checks
that every cell, every chat event and every context stayed where it belonged.
Needs a configured model. Run: PYTHONPATH=backend python scripts/test_switch.py
"""

from __future__ import annotations

import asyncio
import json
import sys

import httpx
import websockets

BASE = "http://127.0.0.1:8000"
CHAT_EVENTS = ("agent_delta", "agent_message", "agent_round_end", "agent_status")
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

            await c.post("/api/agent/chat", json={"message": "分两步算一下 1 到 100 的和"})
            await asyncio.sleep(5)

            # Reading another notebook mid-run is just a switch, not a conflict.
            switch = await c.post("/api/notebook/open", json={"name": b})
            check("运行中可以切走", switch.status_code == 200, f"HTTP {switch.status_code}")

            session = (await c.get("/api/agent/session")).json()
            check("切过去看到的是这一本的会话", session["notebook"] == b, str(session["notebook"]))
            check("这一本自己没在跑", session["running"] is False, str(session["running"]))
            check("但知道那边在跑", a in session["running_notebooks"], str(session["running_notebooks"]))

            # And it is a working notebook, not a read-only view.
            probe = (await c.post("/api/cells", json={"cell_type": "code", "source": "print('B works')"})).json()
            ran = (await c.post(f"/api/cells/{probe['id']}/execute")).json()
            check("切过去的那本能照常跑代码", ran["ok"] is True, str(ran))

            # Clearing this notebook's context is fine while the other runs.
            cleared = await c.post("/api/agent/reset")
            check("能清这一本的上下文", cleared.status_code == 200, f"HTTP {cleared.status_code}")

            # Deleting the notebook that is running is the one thing we refuse.
            refused = await c.request("DELETE", f"/api/files/{a}")
            check("不许删正在跑的那本", refused.status_code == 409, f"HTTP {refused.status_code}")

            # Every chat event has to be labelled with the notebook that asked.
            stray = []
            seen = 0
            while True:
                try:
                    ev = json.loads(await asyncio.wait_for(ws.recv(), timeout=180))
                except asyncio.TimeoutError:
                    check("任务正常结束", False, "等事件超时")
                    break
                if ev.get("type") in CHAT_EVENTS:
                    seen += 1
                    if ev.get("notebook") != a:
                        stray.append(f"{ev.get('type')}@{ev.get('notebook')}")
                if ev.get("type") == "agent_status" and ev.get("phase") == "idle":
                    break

            check("chat 事件都带 notebook 标记", not stray and seen > 0, f"{seen} 个事件，{len(stray)} 个错标")

            # The work landed in the notebook that asked, and nowhere else.
            nb_a = (await c.post("/api/notebook/open", json={"name": a})).json()
            sess_a = (await c.get("/api/agent/session")).json()
            code_a = [x for x in nb_a["cells"] if x["cell_type"] == "code" and x.get("outputs")]
            check("发起的那本拿到了单元格", len(code_a) >= 1, f"{len(nb_a['cells'])} cells")
            # Nobody was looking at A, so the run itself has to checkpoint it.
            check("后台跑完自己落盘了", nb_a["dirty"] is False, f"dirty={nb_a['dirty']}")
            on_disk = json.loads(open(nb_a["path"], encoding="utf-8").read())
            check("磁盘上确实写进去了", len(on_disk["cells"]) == len(nb_a["cells"]), f"{len(on_disk['cells'])} cells")
            check("发起的那本有完整问答", len(sess_a["messages"]) >= 2, f"{len(sess_a['messages'])} 条")
            check("跑完后不再占用", sess_a["running_notebooks"] == [], str(sess_a["running_notebooks"]))

            nb_b = (await c.post("/api/notebook/open", json={"name": b})).json()
            sess_b = (await c.get("/api/agent/session")).json()
            ai_cells = [x for x in nb_b["cells"] if (x.get("metadata") or {}).get("ai_generated")]
            check("另一本没被 agent 写入", not ai_cells, f"{len(ai_cells)} 个 AI 单元格")
            check("另一本手写的还在", any(x["id"] == probe["id"] for x in nb_b["cells"]))
            check("另一本上下文是空的", sess_b["messages"] == [], f"{len(sess_b['messages'])} 条")

            for name in (a, b):
                await c.delete(f"/api/files/{name}")

    print("\nall good" if ok else "\nFAILED")
    sys.exit(0 if ok else 1)


asyncio.run(main())
