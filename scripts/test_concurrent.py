"""Two notebooks open at once: separate kernels, separate queues, live switching.

No model calls, so this runs without an API key. Needs the backend up.
Run: python scripts/test_concurrent.py
"""

from __future__ import annotations

import asyncio
import json
import sys
import threading
import time

import httpx

BASE = "http://127.0.0.1:8000"
ok = True


def check(name: str, passed: bool, detail: str = "") -> None:
    global ok
    ok = ok and passed
    print(f"{'PASS' if passed else 'FAIL'}  {name}{f'  — {detail}' if detail else ''}")


def outputs_text(cell: dict) -> str:
    return json.dumps(cell.get("outputs") or [], ensure_ascii=False)


def test_eviction() -> None:
    """The resident-kernel cap, driven with stubs so no processes are spawned."""
    try:
        import app.kernel_svc as ks
    except ImportError:
        print("skip 回收策略（需要 PYTHONPATH=backend）")
        return

    class FakeKernel:
        def __init__(self, bus, notebook):
            self.notebook, self.status, self.dead = notebook, "idle", False

        async def start(self, cwd=None):
            self.status = "idle"

        async def shutdown(self):
            self.dead = True

        def is_alive(self):
            return not self.dead

    real, ks.KernelService = ks.KernelService, FakeKernel
    try:

        async def run() -> None:
            pool = ks.KernelPool(bus=None, cwd=None)
            protected = {"on-screen", "running"}
            pool.protect_with(lambda: set(protected))
            for name in ("on-screen", "running", "a", "b", "c", "d"):
                await pool.get(name)
            check("到上限前都留着", len(pool.kernels) == ks.MAX_LIVE_KERNELS, str(len(pool.kernels)))

            pool.kernels["b"].status = "busy"  # a cell is mid-flight here
            await pool.get("e")
            await asyncio.sleep(0)
            live = set(pool.kernels)
            check("超了上限就回收", len(live) == ks.MAX_LIVE_KERNELS, str(sorted(live)))
            check("回收的是最久没用的那本", "a" not in live, str(sorted(live)))
            check("正在看的和正在跑的不回收", protected <= live)
            check("忙着的不回收", "b" in live)

            await pool.get("c")  # touching it makes it recent again
            await pool.get("f")
            await asyncio.sleep(0)
            check("用过的会被往后排", "c" in pool.kernels, str(sorted(pool.kernels)))

        asyncio.run(run())
    finally:
        ks.KernelService = real


def main() -> None:
    with httpx.Client(base_url=BASE, timeout=120) as c:

        def open_nb(name: str) -> dict:
            return c.post("/api/notebook/open", json={"name": name}).json()

        def add(source: str) -> str:
            return c.post("/api/cells", json={"cell_type": "code", "source": source}).json()["id"]

        def run(cell_id: str) -> dict:
            return c.post(f"/api/cells/{cell_id}/execute").json()

        def cell(cell_id: str, notebook: str) -> dict:
            nb = open_nb(notebook)
            return next(x for x in nb["cells"] if x["id"] == cell_id)

        a = c.post("/api/notebook/new").json()["name"]
        a_set = add("shared_marker = 'from A'")
        run(a_set)

        b = c.post("/api/notebook/new").json()["name"]
        b_probe = add("print(shared_marker)")
        b_res = run(b_probe)
        check(
            "两本的变量不互串",
            b_res["ok"] is False and "NameError" in outputs_text(cell(b_probe, b)),
            outputs_text(cell(b_probe, b))[:80],
        )

        health = c.get("/api/health").json()
        check("每本一个内核", {a, b} <= set(health["live_kernels"]), str(health["live_kernels"]))

        # A slow cell in one notebook must not hold up the other's queue.
        open_nb(a)
        a_slow = add("import time\ntime.sleep(6)\nprint('A done')")
        open_nb(b)
        b_quick = add("print('B done')")

        timings: dict[str, float] = {}
        started = time.time()

        def run_slow() -> None:
            run(a_slow)
            timings["a"] = time.time() - started

        t = threading.Thread(target=run_slow)
        t.start()
        time.sleep(0.5)
        run(b_quick)
        timings["b"] = time.time() - started
        check(
            "一本在跑不挡另一本",
            timings["b"] < 3 and "B done" in outputs_text(cell(b_quick, b)),
            f"B {timings['b']:.1f}s",
        )

        # Switching while A is busy: B is fully usable, A keeps its own state.
        open_nb(b)
        b_var = add("b_only = 7\nprint(b_only)")
        run(b_var)
        check("忙着的时候另一本能正常写和跑", "7" in outputs_text(cell(b_var, b)))

        t.join(timeout=30)
        check("先起的那本自己跑完", "A done" in outputs_text(cell(a_slow, a)), f"A {timings.get('a', -1):.1f}s")

        # Restarting one kernel leaves the other's variables alone.
        open_nb(a)
        c.post("/api/kernel/restart")
        a_probe = add("print(shared_marker)")
        check("重启后本本自己的变量清了", run(a_probe)["ok"] is False)
        open_nb(b)
        b_again = add("print(b_only)")
        run(b_again)
        check("重启一本不影响另一本", "7" in outputs_text(cell(b_again, b)))

        for name in (a, b):
            c.delete(f"/api/files/{name}")
        gone = {f["name"] for f in c.get("/api/files").json()["files"]}
        check("删除后内核也回收了", not ({a, b} & gone) and not ({a, b} & set(c.get("/api/health").json()["live_kernels"])))

    print()
    test_eviction()

    print("\nall good" if ok else "\nFAILED")
    sys.exit(0 if ok else 1)


main()
