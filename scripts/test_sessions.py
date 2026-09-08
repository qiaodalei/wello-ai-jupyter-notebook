"""Per-notebook chat context: isolation, the 5-round window, and clearing.

The session logic is driven in-process (no model calls, no disk writes); the
HTTP surface is then checked against the running backend.

Run: PYTHONPATH=backend python scripts/test_sessions.py
"""

from __future__ import annotations

import sys

import httpx

BASE = "http://127.0.0.1:8000"
ok = True


def check(name: str, passed: bool, detail: str = "") -> None:
    global ok
    ok = ok and passed
    print(f"{'PASS' if passed else 'FAIL'}  {name}{f'  — {detail}' if detail else ''}")


def test_logic() -> None:
    from app.agent import MAX_QA_ROUNDS, AgentPool
    from app.notebook_store import NotebookDoc, NotebookStore, empty_notebook_payload

    store = NotebookStore()
    pool = AgentPool(store, kernels=None, bus=None)

    def agent_for(name: str):
        """Open a notebook the way the HTTP layer would, then take its agent."""
        if name not in store.docs:
            store.docs[name] = NotebookDoc(empty_notebook_payload(name))
        store.active = name
        return pool.get()

    def say(agent, text: str) -> None:
        """Append a QA round the way a finished agent run would."""
        agent.history.append({"role": "user", "content": text})
        agent._trim_history()
        agent.history.append({"role": "assistant", "content": f"回答：{text}"})

    a = agent_for("A.ipynb")
    check("新 notebook 上下文是空的", a.history == [], a.notebook)

    for i in range(1, 4):
        say(a, f"A 的问题 {i}")
    check("3 轮 QA 存了 6 条", len(a.history) == 6, str(len(a.history)))

    b = agent_for("B.ipynb")
    check("切走之后是另一份上下文", b.history == [], b.notebook)
    say(b, "B 的问题")
    check("另一本自己记 1 轮", len(b.history) == 2)

    a = agent_for("A.ipynb")
    check("切回来上下文还在", len(a.history) == 6, str(len(a.history)))
    check("内容属于这一本", a.history[0]["content"].startswith("A "))
    check("两本是不同的 agent", a is not b)

    # The window: only the last MAX_QA_ROUNDS rounds survive.
    for i in range(4, 10):
        say(a, f"A 的问题 {i}")
    users = [m for m in a.history if m["role"] == "user"]
    check(
        f"只保留最近 {MAX_QA_ROUNDS} 轮",
        len(users) == MAX_QA_ROUNDS,
        f"{len(users)} 轮 / {len(a.history)} 条",
    )
    check("留下的是最新几轮", users[-1]["content"] == "A 的问题 9", users[-1]["content"])
    check("最老的已经丢掉", all(u["content"] != "A 的问题 1" for u in users))
    check("裁剪后仍从 user 开始", a.history[0]["role"] == "user")

    # Clearing touches only the current notebook.
    check("清空返回当前 notebook", a.reset_chat() == "A.ipynb")
    check("当前上下文清空了", a.history == [])
    check("另一本没被清掉", len(agent_for("B.ipynb").history) == 2)

    # Renaming carries the context along; deleting drops it.
    store.docs["B2.ipynb"] = store.docs.pop("B.ipynb")
    store.docs["B2.ipynb"].payload["name"] = "B2.ipynb"
    pool.rename("B.ipynb", "B2.ipynb")
    renamed = agent_for("B2.ipynb")
    check("上下文跟着改名走", len(renamed.history) == 2)
    check("改名后 agent 指向同一份文档", renamed.doc is store.docs["B2.ipynb"])
    pool.forget("B2.ipynb")
    check("删掉之后不留残余", agent_for("B2.ipynb").history == [])

    c = agent_for("C.ipynb")
    say(c, "只有一轮")
    check("一轮也不会被裁掉", len(c.history) == 2)

    # Several notebooks can be mid-run at the same time.
    a.running = True
    c.running = True
    check("并发运行都被记下", set(pool.running_notebooks()) == {"A.ipynb", "C.ipynb"}, str(pool.running_notebooks()))
    a.running = False
    check("单独结束只影响自己", pool.running_notebooks() == ["C.ipynb"], str(pool.running_notebooks()))


def test_http() -> None:
    with httpx.Client(base_url=BASE, timeout=30) as http:
        name = http.post("/api/notebook/new").json()["name"]
        session = http.get("/api/agent/session").json()
        check("session 接口报出当前 notebook", session["notebook"] == name, session["notebook"])
        check("session 接口返回消息列表", isinstance(session["messages"], list))
        check("session 接口报出运行中的本子", isinstance(session["running_notebooks"], list))

        reset = http.post("/api/agent/reset").json()
        check("reset 只清当前 notebook", reset["notebook"] == name, reset["notebook"])

        http.delete(f"/api/files/{name}")


def main() -> None:
    test_logic()
    print()
    test_http()
    print("\nall good" if ok else "\nFAILED")
    sys.exit(0 if ok else 1)


main()
