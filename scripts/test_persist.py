"""Switching notebooks must not throw away unsaved work.

Opening a notebook rereads it from disk, so anything only in memory has to be
flushed first. Run: PYTHONPATH=backend python scripts/test_persist.py
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


def main() -> None:
    with httpx.Client(base_url=BASE, timeout=30) as c:
        a = c.post("/api/notebook/new").json()["name"]
        b = c.post("/api/notebook/new").json()["name"]
        c.post("/api/notebook/open", json={"name": a})

        # Edit without saving, the way typing in a cell does.
        cell = c.post("/api/cells", json={"cell_type": "code", "source": "print('keep me')"}).json()
        c.post("/api/cells", json={"cell_type": "markdown", "source": "## 手写的说明"})
        c.post(f"/api/cells/{cell['id']}/execute")
        nb = c.get("/api/notebook").json()
        check("编辑后是 dirty 的", nb["dirty"] is True, str(nb["dirty"]))
        before = len(nb["cells"])

        # Go away and come back.
        c.post("/api/notebook/open", json={"name": b})
        nb = c.post("/api/notebook/open", json={"name": a}).json()
        check("切回来单元格数量没变", len(nb["cells"]) == before, f"{len(nb['cells'])} / {before}")
        sources = [x.get("source") for x in nb["cells"]]
        check("代码单元格还在", "print('keep me')" in sources, str(sources))
        check("markdown 单元格还在", "## 手写的说明" in sources, str(sources))
        kept = [
            x
            for x in nb["cells"]
            if x["cell_type"] == "code"
            and any("keep me" in (o.get("text") or "") for o in x.get("outputs") or [])
        ]
        check("执行过的输出也留下了", len(kept) == 1, f"{len(kept)} 个带输出")
        check("执行序号也留下了", kept and kept[0].get("execution_count") is not None)
        check("切回来是干净状态", nb["dirty"] is False, str(nb["dirty"]))

        # New notebook must not eat the current one either.
        c.post(f"/api/cells", json={"cell_type": "code", "source": "second = 2"})
        fresh = c.post("/api/notebook/new").json()["name"]
        nb = c.post("/api/notebook/open", json={"name": a}).json()
        check("新建 notebook 前也会保存", "second = 2" in [x.get("source") for x in nb["cells"]])

        for name in (a, b, fresh):
            c.delete(f"/api/files/{name}")

    print("\nall good" if ok else "\nFAILED")
    sys.exit(0 if ok else 1)


main()
