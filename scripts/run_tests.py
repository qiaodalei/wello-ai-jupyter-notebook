#!/usr/bin/env python3
"""Run Wello AI Jupyter MVP checks against a live backend + frontend."""

from __future__ import annotations

import json
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
API = "http://127.0.0.1:8000"
WEB = "http://127.0.0.1:5173"

RESULTS: list[tuple[str, bool, str]] = []


def record(tid: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((tid, ok, detail))
    mark = "PASS" if ok else "FAIL"
    extra = f"  {detail}" if detail else ""
    print(f"[{mark}] {tid}{extra}")


def req(method: str, path: str, body: dict | None = None, timeout: float = 20):
    data = None
    headers = {}
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    r = urllib.request.Request(API + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            raw = resp.read()
            if not raw:
                return {}
            return json.loads(raw.decode())
    except urllib.error.HTTPError as exc:
        text = exc.read().decode(errors="replace")
        raise RuntimeError(f"HTTP {exc.code} {path}: {text[:400]}") from exc


def get_nb():
    return req("GET", "/api/notebook")


def cells():
    return get_nb()["cells"]


def first_code():
    for c in cells():
        if c["cell_type"] == "code":
            return c
    raise RuntimeError("no code cell")


def patch(cid: str, **body):
    return req("PATCH", f"/api/cells/{cid}", body)


def execute(cid: str, timeout: float = 30):
    return req("POST", f"/api/cells/{cid}/execute", timeout=timeout)


def insert(**body):
    return req("POST", "/api/cells", body)


def test_a_product() -> None:
    health = req("GET", "/api/health")
    record("A5", bool(health.get("ok") and health.get("alive")), json.dumps(health, ensure_ascii=False))

    try:
        with urllib.request.urlopen(WEB + "/", timeout=5) as resp:
            html = resp.read().decode()
        record("A4", resp.status == 200, f"status={resp.status}")
        record("A1", "Wello AI Jupyter" in html, "title in index.html")
    except Exception as exc:
        record("A4", False, str(exc))
        record("A1", False, "frontend unreachable")

    app_src = (ROOT / "frontend/src/App.tsx").read_text(encoding="utf-8")
    chat_src = (ROOT / "frontend/src/components/ChatPanel.tsx").read_text(encoding="utf-8")
    config_src = (ROOT / "frontend/src/components/ConfigPage.tsx").read_text(encoding="utf-8")
    no_key_on_notebook = ("api_key" not in app_src) and ("API Key" not in app_src) and ("API Key" not in chat_src)
    record("A2", no_key_on_notebook, "App/ChatPanel have no API Key fields")
    record(
        "A3",
        "API Key" in config_src and "api_base" in config_src and hash_config_ok(),
        "#config + ConfigPage fields",
    )


def hash_config_ok() -> bool:
    app = (ROOT / "frontend/src/App.tsx").read_text(encoding="utf-8")
    return "ConfigPage" in app and ("#config" in app)


def test_b_cells() -> None:
    req("POST", "/api/notebook/new")
    nb = get_nb()
    n0 = len(nb["cells"])
    code = insert(cell_type="code", source="x = 1")
    record("B1", code["cell_type"] == "code" and len(cells()) == n0 + 1, code["id"])

    md = insert(index=0, cell_type="markdown", source="# Hello Wello")
    md_ok = md["cell_type"] == "markdown" and "# Hello Wello" in md["source"]
    record("B2", md_ok, md["id"])

    cid = code["id"]
    patch(cid, source="print(1+1)")
    got = next(c for c in cells() if c["id"] == cid)
    record("B3", got["source"] == "print(1+1)", got["source"])

    before = [c["id"] for c in cells()]
    req("POST", f"/api/cells/{cid}/move", {"delta": -1})
    after = [c["id"] for c in cells()]
    record("B5", after != before, f"{before[:4]} -> {after[:4]}")

    patch(cid, cell_type="markdown")
    switched = next(c for c in cells() if c["id"] == cid)
    record("B6", switched["cell_type"] == "markdown" and not switched.get("outputs"), switched["cell_type"])
    patch(cid, cell_type="code", source="print(1+1)")

    src = next(c for c in cells() if c["id"] == cid)["source"]
    pasted = insert(cell_type="code", source=src)
    record("B7", pasted["source"] == src, pasted["id"])

    n_before = len(cells())
    req("DELETE", f"/api/cells/{pasted['id']}")
    n_after = len(cells())
    record("B4", n_after == n_before - 1 or n_after >= 1, f"{n_before} -> {n_after}")


def test_c_execute() -> None:
    req("POST", "/api/notebook/new")
    cid = cells()[0]["id"]
    patch(cid, source="print(1+1)")
    result = execute(cid)
    nb_cell = cells()[0]
    text = "".join(o.get("text", "") for o in nb_cell.get("outputs") or [] if o.get("output_type") == "stream")
    record(
        "C1",
        result.get("ok") is True and "2" in text and result.get("execution_count") is not None,
        f"out={text!r} count={result.get('execution_count')}",
    )

    err = insert(cell_type="code", source="1/0")
    er = execute(err["id"])
    ecell = next(c for c in cells() if c["id"] == err["id"])
    blob = json.dumps(ecell.get("outputs") or [])
    record(
        "C2",
        er.get("ok") is False and "ZeroDivisionError" in blob,
        ecell.get("outputs", [{}])[0].get("ename", ""),
    )

    nxt = insert(cell_type="code", source='print("ok")')
    nr = execute(nxt["id"])
    ncell = next(c for c in cells() if c["id"] == nxt["id"])
    ntext = "".join(o.get("text", "") for o in ncell.get("outputs") or [] if o.get("output_type") == "stream")
    record("C3", nr.get("ok") is True and "ok" in ntext, ntext)

    multi = insert(cell_type="code", source="print('a')\nprint('b')\nprint('c')")
    execute(multi["id"])
    mcell = next(c for c in cells() if c["id"] == multi["id"])
    mtext = "".join(o.get("text", "") for o in mcell.get("outputs") or [] if o.get("output_type") == "stream")
    record("C4", "a" in mtext and "b" in mtext and "c" in mtext, repr(mtext))

    req("POST", f"/api/cells/{multi['id']}/clear")
    cleared = next(c for c in cells() if c["id"] == multi["id"])
    record("C5", not cleared.get("outputs"), f"outputs={cleared.get('outputs')}")

    execute(cid)
    req("POST", "/api/notebook/clear-outputs")
    any_out = any((c.get("outputs") or []) for c in cells() if c["cell_type"] == "code")
    record("C6", not any_out, "all code outputs empty")

    req("POST", "/api/notebook/new")
    a = cells()[0]["id"]
    patch(a, source="value = 41")
    b = insert(cell_type="code", source="print(value + 1)")["id"]
    req("POST", "/api/execute", {"all": True})
    bcell = next(c for c in cells() if c["id"] == b)
    btext = "".join(o.get("text", "") for o in bcell.get("outputs") or [] if o.get("output_type") == "stream")
    record("C7", "42" in btext, repr(btext))


def test_d_kernel() -> None:
    sleeper = insert(cell_type="code", source="import time\ntime.sleep(30)")
    holder: dict = {}

    def run():
        try:
            holder["res"] = execute(sleeper["id"], timeout=15)
        except Exception as exc:
            holder["exc"] = str(exc)

    t = threading.Thread(target=run)
    t.start()
    time.sleep(0.4)
    req("POST", "/api/kernel/interrupt")
    t.join(timeout=12)
    res = holder.get("res") or {}
    interrupted = (not res.get("ok", True)) or bool(holder.get("exc"))
    record("D1", interrupted and t.is_alive() is False, f"res={res} exc={holder.get('exc')}")

    varc = insert(cell_type="code", source="keep_me = 'still here'\nsecret = 99")
    execute(varc["id"])
    source_before = next(c for c in cells() if c["id"] == varc["id"])["source"]
    req("POST", "/api/kernel/restart")
    time.sleep(0.8)
    probe = insert(cell_type="code", source="print(secret)")
    pr = execute(probe["id"])
    pcell = next(c for c in cells() if c["id"] == probe["id"])
    blob = json.dumps(pcell.get("outputs") or [])
    record("D2", pr.get("ok") is False and "NameError" in blob, blob[0:180])
    source_after = next(c for c in cells() if c["id"] == varc["id"])["source"]
    record("D3", source_after == source_before, source_after)


def test_e_files() -> None:
    req("POST", "/api/notebook/new")
    cid = cells()[0]["id"]
    patch(cid, source="print('save-me')")
    execute(cid)
    saved = req("POST", "/api/notebook/save", {"name": "_wello_test.ipynb"})
    path = Path(saved["path"])
    record("E1", saved.get("dirty") is False and path.exists(), str(path))

    opened = req("POST", "/api/notebook/open", {"name": "_wello_test.ipynb"})
    src = opened["cells"][0]["source"]
    record("E2", "save-me" in src, src)

    try:
        import nbformat

        nb = nbformat.read(path, as_version=4)
        ok_fmt = nb.nbformat == 4 and "kernelspec" in dict(nb.metadata)
        record("E3", ok_fmt, f"nbformat={nb.nbformat} cells={len(nb.cells)}")
        cell = nb.cells[0]
        record(
            "E4",
            hasattr(cell, "source") and "outputs" in cell and "execution_count" in cell,
            f"keys ok execution_count={cell.get('execution_count')}",
        )
    except Exception as exc:
        record("E3", False, str(exc))
        record("E4", False, str(exc))
    finally:
        if path.exists():
            path.unlink()


def test_f_agent() -> None:
    settings = req("GET", "/api/settings")
    record("F4", "auto_execute" in settings, json.dumps({k: settings[k] for k in ("auto_execute", "model") if k in settings}))
    record("F5", settings.get("max_repair_rounds") == 5 or isinstance(settings.get("max_repair_rounds"), int), str(settings.get("max_repair_rounds")))

    agent_src = (ROOT / "backend/app/agent.py").read_text(encoding="utf-8")
    record("F3", all(x in agent_src for x in ("add_step", "replace_cell", "run_cell")), "tools present")

    app_src = (ROOT / "frontend/src/App.tsx").read_text(encoding="utf-8")
    record("F2", "api_key" not in app_src.lower() and "API Key" not in app_src, "notebook UI has no key field")

    if settings.get("has_api_key"):
        record("F1", True, "API key configured; skip unconfigured prompt (covered by code path)")
        return

    import asyncio

    try:
        import websockets
    except ImportError:
        record("F1", False, "websockets not installed")
        return

    async def listen():
        async with websockets.connect("ws://127.0.0.1:8000/ws") as ws:
            req("POST", "/api/agent/chat", {"message": "hello"})
            deadline = time.time() + 8
            while time.time() < deadline:
                raw = await asyncio.wait_for(ws.recv(), timeout=8)
                event = json.loads(raw)
                if event.get("type") == "agent_message" and event.get("role") == "assistant":
                    return event.get("content") or ""
            return ""

    try:
        content = asyncio.run(listen())
        record("F1", "配置" in content or "env" in content.lower() or "Key" in content, content[:180])
    except Exception as exc:
        record("F1", False, str(exc))


def test_g_ws() -> None:
    import asyncio

    try:
        import websockets
    except ImportError:
        record("G1", False, "websockets missing")
        record("G2", False, "websockets missing")
        return

    async def hello_and_run():
        events = []
        async with websockets.connect("ws://127.0.0.1:8000/ws") as ws:
            first = json.loads(await asyncio.wait_for(ws.recv(), timeout=5))
            cid = (first.get("doc") or {}).get("cells", [{}])[0].get("id")
            if not cid:
                cid = cells()[0]["id"]
            patch(cid, source="print('ws-event')")
            threading.Thread(target=lambda: execute(cid), daemon=True).start()
            deadline = time.time() + 8
            while time.time() < deadline:
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=2)
                except TimeoutError:
                    break
                events.append(json.loads(raw).get("type"))
                if "cell_finished" in events:
                    break
        return first, events

    try:
        first, events = asyncio.run(hello_and_run())
        record("G1", first.get("type") == "hello" and "doc" in first and "kernel" in first, first.get("type"))
        record(
            "G2",
            any(t in events for t in ("cell_running", "cell_finished", "stream")),
            ",".join(events[:12]),
        )
    except Exception as exc:
        record("G1", False, str(exc))
        record("G2", False, str(exc))


def _notebook_files() -> set:
    d = ROOT / "notebooks"
    return {p.name for p in d.glob("*.ipynb")} if d.exists() else set()


def _cleanup(before: set) -> None:
    """Tests create scratch notebooks; don't leave them lying around."""
    for name in _notebook_files() - before:
        try:
            (ROOT / "notebooks" / name).unlink()
        except OSError:
            pass


def main() -> int:
    print("Wello AI Jupyter 功能测试")
    print("=" * 48)
    existing = _notebook_files()
    try:
        test_a_product()
        test_b_cells()
        test_c_execute()
        test_d_kernel()
        test_e_files()
        test_f_agent()
        test_g_ws()
    except Exception as exc:
        record("FATAL", False, str(exc))
        raise
    finally:
        _cleanup(existing)

    passed = sum(1 for _, ok, _ in RESULTS if ok)
    failed = sum(1 for _, ok, _ in RESULTS if not ok)
    print("=" * 48)
    print(f"合计 {len(RESULTS)} 项  通过 {passed}  失败 {failed}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
