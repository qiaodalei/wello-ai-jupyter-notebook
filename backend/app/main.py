from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import nbformat
from fastapi import (
    FastAPI,
    File,
    HTTPException,
    Request,
    UploadFile,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .agent import AgentPool, AgentService
from .config import (
    NOTEBOOKS_DIR,
    ROOT,
    AppSettings,
    SettingsUpdate,
    load_settings,
    save_settings,
)
from .events import EventBus
from .kernel_svc import KernelPool, KernelService
from .notebook_store import (
    NotebookDoc,
    NotebookStore,
    list_notebooks,
    safe_notebook_path,
    unique_name,
)


class CellInsert(BaseModel):
    index: int | None = None
    after_cell_id: str | None = None
    cell_type: str = "code"
    source: str = ""


class CellPatch(BaseModel):
    source: str | None = None
    cell_type: str | None = None


class ChatBody(BaseModel):
    message: str = Field(min_length=1)


class OpenBody(BaseModel):
    name: str


class SaveBody(BaseModel):
    name: str | None = None


class MoveBody(BaseModel):
    delta: int


class NotebookRef(BaseModel):
    notebook: str | None = None


def get_state(request: Request):
    return request.app.state


@asynccontextmanager
async def lifespan(app: FastAPI):
    NOTEBOOKS_DIR.mkdir(parents=True, exist_ok=True)
    settings = load_settings()
    bus = EventBus()
    store = NotebookStore()
    kernels = KernelPool(bus, cwd=str(NOTEBOOKS_DIR))
    agents = AgentPool(store, kernels, bus)
    agents.bind_settings(lambda: app.state.settings)
    # Never recycle the kernel of the notebook on screen or of a live run.
    kernels.protect_with(lambda: {store.active, *agents.running_notebooks()})

    app.state.settings = settings
    app.state.bus = bus
    app.state.store = store
    app.state.kernels = kernels
    app.state.agents = agents

    # The notebook on screen gets its kernel up front; the rest boot when they
    # first run something, so browsing files costs nothing.
    await kernels.get(store.active)
    yield
    await kernels.shutdown_all()


app = FastAPI(title="Wello AI Jupyter", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/health")
async def health(request: Request):
    store: NotebookStore = request.app.state.store
    kernels: KernelPool = request.app.state.kernels
    return {
        "ok": True,
        "kernel": kernels.status(store.active),
        "alive": kernels.is_alive(store.active),
        "live_kernels": kernels.live_names(),
    }


@app.get("/api/settings")
async def get_settings(request: Request):
    return request.app.state.settings.public_dict()


@app.put("/api/settings")
async def put_settings(body: SettingsUpdate, request: Request):
    current: AppSettings = request.app.state.settings
    data = current.model_dump()
    updates = body.model_dump(exclude_unset=True)
    if updates.get("api_key") == "" or (isinstance(updates.get("api_key"), str) and updates["api_key"].startswith("••••")):
        updates.pop("api_key", None)
    if isinstance(updates.get("api_key"), str) and "•" in updates["api_key"]:
        updates.pop("api_key", None)
    data.update({k: v for k, v in updates.items() if v is not None})
    settings = AppSettings(**data)
    save_settings(settings)
    request.app.state.settings = settings
    return settings.public_dict()


@app.get("/api/files")
async def files():
    return {"files": list_notebooks()}


async def _push(request: Request, doc: NotebookDoc) -> dict:
    """Broadcast one document, tagged so only its own view picks it up."""
    snap = doc.snapshot()
    await request.app.state.bus.publish({"type": "notebook", "notebook": doc.name, "doc": snap})
    return snap


def _cell_doc(request: Request, cell_id: str) -> NotebookDoc:
    store: NotebookStore = request.app.state.store
    doc = store.doc_for_cell(cell_id)
    if doc is None:
        raise HTTPException(404, "cell not found")
    return doc


def _save_if_dirty(request: Request, skip: str | None = None) -> None:
    """Checkpoint the open notebook to disk before the file list changes."""
    store: NotebookStore = request.app.state.store
    if not store.payload.get("dirty"):
        return
    if skip and store.payload.get("name") == skip:
        return
    try:
        store.save_file()
    except Exception:
        # Never block a switch on a failed save; the in-memory copy is still shown.
        pass


async def _switch_after_file_change(request: Request, prefer: str | None) -> dict:
    """Open `prefer`, falling back to any remaining notebook, then a new one."""
    store: NotebookStore = request.app.state.store
    candidates = [prefer] if prefer else []
    candidates += [f["name"] for f in list_notebooks()]
    for name in candidates:
        if not name:
            continue
        try:
            snap = store.load_file(name)
            break
        except (OSError, ValueError, AttributeError):
            continue
    else:
        snap = store.new_file()
    request.app.state.kernels.warm(store.active)
    await _push(request, store.doc())
    return snap


@app.delete("/api/files/{name}")
async def delete_file(name: str, request: Request):
    store: NotebookStore = request.app.state.store
    agents: AgentPool = request.app.state.agents
    try:
        path = safe_notebook_path(name)
    except ValueError:
        raise HTTPException(400, "invalid notebook name")
    if not path.exists():
        raise HTTPException(404, "notebook not found")
    if agents.is_running(path.name):
        raise HTTPException(409, f"Agent 正在 {path.name} 里跑，先点「停止」再删。")
    _save_if_dirty(request, skip=path.name)
    was_open = store.active == path.name
    try:
        path.unlink()
    except OSError as exc:
        raise HTTPException(500, f"delete failed: {exc}")
    agents.forget(path.name)
    request.app.state.kernels.discard(path.name)
    store.close(path.name)
    snap = await _switch_after_file_change(request, None) if was_open else store.snapshot()
    return {"files": list_notebooks(), "notebook": snap, "switched": was_open}


@app.post("/api/files/import")
async def import_files(request: Request, files: list[UploadFile] = File(...)):
    store: NotebookStore = request.app.state.store
    _save_if_dirty(request)
    imported: list[str] = []
    for upload in files:
        raw = await upload.read()
        try:
            nb = nbformat.reads(raw.decode("utf-8"), as_version=4)
            nbformat.validate(nb)
        except Exception as exc:
            raise HTTPException(400, f"{upload.filename or 'file'} 不是有效的 .ipynb：{exc}")
        target = unique_name(upload.filename or "Imported.ipynb")
        nbformat.write(nb, target)
        # A notebook of this name may have been open earlier and deleted from
        # disk since; the copy we hold must not shadow what we just wrote.
        store.forget(target.name)
        request.app.state.kernels.discard(target.name)
        request.app.state.agents.forget(target.name)
        imported.append(target.name)
    if not imported:
        raise HTTPException(400, "没有可导入的文件")
    snap = await _switch_after_file_change(request, imported[-1])
    return {"files": list_notebooks(), "notebook": snap, "imported": imported}


@app.get("/api/notebook")
async def get_notebook(request: Request):
    return request.app.state.store.snapshot()


@app.post("/api/notebook/new")
async def new_notebook(request: Request):
    store: NotebookStore = request.app.state.store
    _save_if_dirty(request)
    snap = store.new_file()
    request.app.state.kernels.warm(store.active)
    await _push(request, store.doc())
    return snap


@app.post("/api/notebook/open")
async def open_notebook(body: OpenBody, request: Request):
    store: NotebookStore = request.app.state.store
    _save_if_dirty(request)
    try:
        snap = store.load_file(body.name)
    except FileNotFoundError:
        raise HTTPException(404, "notebook not found")
    except Exception as exc:
        raise HTTPException(400, str(exc))
    request.app.state.kernels.warm(store.active)
    await _push(request, store.doc())
    return snap


@app.post("/api/notebook/save")
async def save_notebook(body: SaveBody, request: Request):
    store: NotebookStore = request.app.state.store
    was = store.payload.get("name") or ""
    snap = store.save_file(body.name)
    now = snap.get("name") or ""
    if now != was:
        request.app.state.kernels.rename(was, now)
        request.app.state.agents.rename(was, now)
    await _push(request, store.doc())
    return snap


@app.get("/api/notebook/export")
async def export_notebook(request: Request):
    store: NotebookStore = request.app.state.store
    if store.payload.get("dirty") or not store.payload.get("path"):
        store.save_file()
    path = store.payload["path"]
    return FileResponse(path, filename=store.payload["name"], media_type="application/x-ipynb+json")


@app.post("/api/notebook/clear-outputs")
async def clear_all_outputs(request: Request):
    doc = request.app.state.store.doc()
    for cell in list(doc.payload["cells"]):
        if cell.get("cell_type") == "code":
            doc.clear_outputs(cell["id"])
    return await _push(request, doc)


@app.post("/api/cells")
async def insert_cell(body: CellInsert, request: Request):
    store: NotebookStore = request.app.state.store
    doc = store.doc_for_cell(body.after_cell_id) if body.after_cell_id else store.doc()
    doc = doc or store.doc()
    index = body.index
    if body.after_cell_id:
        pos = doc.cell_index(body.after_cell_id)
        index = len(doc.payload["cells"]) if pos < 0 else pos + 1
    cell = doc.insert_cell(index, body.cell_type, body.source)
    await _push(request, doc)
    return cell


@app.patch("/api/cells/{cell_id}")
async def patch_cell(cell_id: str, body: CellPatch, request: Request):
    doc = _cell_doc(request, cell_id)
    cell = None
    if body.cell_type:
        cell = doc.set_cell_type(cell_id, body.cell_type)
    if body.source is not None:
        cell = doc.update_cell_source(cell_id, body.source)
    if not cell:
        raise HTTPException(404, "cell not found")
    return cell


@app.delete("/api/cells/{cell_id}")
async def delete_cell(cell_id: str, request: Request):
    doc = _cell_doc(request, cell_id)
    if not doc.delete_cell(cell_id):
        raise HTTPException(404, "cell not found")
    await _push(request, doc)
    return {"ok": True}


@app.post("/api/cells/{cell_id}/move")
async def move_cell(cell_id: str, body: MoveBody, request: Request):
    doc = _cell_doc(request, cell_id)
    if not doc.move_cell(cell_id, body.delta):
        raise HTTPException(400, "cannot move")
    return await _push(request, doc)


@app.post("/api/cells/{cell_id}/merge")
async def merge_cell(cell_id: str, request: Request):
    doc = _cell_doc(request, cell_id)
    if not doc.merge_with_next(cell_id):
        raise HTTPException(400, "cannot merge")
    return await _push(request, doc)


@app.post("/api/cells/{cell_id}/clear")
async def clear_cell(cell_id: str, request: Request):
    doc = _cell_doc(request, cell_id)
    cell = doc.clear_outputs(cell_id)
    if not cell:
        raise HTTPException(404, "cell not found")
    await _push(request, doc)
    return cell


@app.post("/api/cells/{cell_id}/execute")
async def execute_cell(cell_id: str, request: Request):
    result = await _execute(request.app, cell_id)
    return {
        "ok": result.ok,
        "execution_count": result.execution_count,
        "elapsed_ms": result.elapsed_ms,
        "status": result.status,
    }


class RunRange(BaseModel):
    from_cell_id: str | None = None
    all: bool = False


@app.post("/api/execute")
async def execute_range(body: RunRange, request: Request):
    store: NotebookStore = request.app.state.store
    doc = store.doc_for_cell(body.from_cell_id) if body.from_cell_id else store.doc()
    doc = doc or store.doc()
    cells = doc.payload["cells"]
    start = 0
    if body.from_cell_id:
        start = max(0, doc.cell_index(body.from_cell_id))
    ids = [c["id"] for c in cells[start:] if c["cell_type"] == "code"]
    results = []
    for cid in ids:
        result = await _execute(request.app, cid)
        results.append({"cell_id": cid, "ok": result.ok})
        if not result.ok and not body.all:
            break
    return {"results": results}


@app.post("/api/kernel/interrupt")
async def interrupt(request: Request):
    live = request.app.state.kernels.peek(request.app.state.store.active)
    if live:
        live.interrupt()
    return {"ok": True}


@app.post("/api/kernel/restart")
async def restart(request: Request):
    """Restart only the open notebook's kernel; the others keep their state."""
    store: NotebookStore = request.app.state.store
    kernels: KernelPool = request.app.state.kernels
    kernel = await kernels.get(store.active)
    await kernel.restart()
    doc = store.doc()
    stale = [c for c in doc.payload["cells"] if c.get("status") == "running"]
    if stale:
        for cell in stale:
            cell["status"] = "idle"
        await _push(request, doc)
    return {"ok": True, "status": kernel.status}


@app.post("/api/agent/chat")
async def agent_chat(body: ChatBody, request: Request):
    """Ask the open notebook's agent. Other notebooks can be running already."""
    agent: AgentService = request.app.state.agents.get()
    if agent.running:
        raise HTTPException(409, f"{agent.notebook} 还在跑上一个需求，等它结束或者先点「停止」。")
    asyncio.create_task(agent.run(body.message.strip()))
    return {"ok": True, "notebook": agent.notebook}


@app.post("/api/agent/stop")
async def agent_stop(request: Request, body: NotebookRef | None = None):
    """Stop one notebook's run: the open one unless told otherwise."""
    agents: AgentPool = request.app.state.agents
    name = (body.notebook if body else None) or request.app.state.store.active
    agent = agents.peek(name)
    if agent:
        await agent.stop()
    return {"ok": True, "notebook": name}


@app.get("/api/agent/session")
async def agent_session(request: Request):
    """The open notebook's conversation, so the UI can follow file switches."""
    agents: AgentPool = request.app.state.agents
    agent = agents.get()
    return {
        "notebook": agent.notebook,
        "messages": agent.history,
        "running": agent.running,
        "phase": agent.phase,
        "running_notebooks": agents.running_notebooks(),
    }


@app.post("/api/agent/reset")
async def agent_reset(request: Request):
    agent: AgentService = request.app.state.agents.get()
    if agent.running:
        raise HTTPException(409, "这本正在运行，先点「停止」再清空上下文。")
    return {"ok": True, "notebook": agent.reset_chat()}


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await ws.accept()
    bus: EventBus = ws.app.state.bus
    queue = await bus.subscribe()

    async def pump() -> None:
        while True:
            event = await queue.get()
            await ws.send_json(event)

    pump_task = asyncio.create_task(pump())
    try:
        await ws.send_json(
            {
                "type": "hello",
                "notebook": ws.app.state.store.active,
                "doc": ws.app.state.store.snapshot(),
                "kernel": ws.app.state.kernels.status(ws.app.state.store.active),
                "agent_phase": ws.app.state.agents.get().phase,
                "running_notebooks": ws.app.state.agents.running_notebooks(),
            }
        )
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        pump_task.cancel()
        await bus.unsubscribe(queue)


async def _execute(app: FastAPI, cell_id: str):
    store: NotebookStore = app.state.store
    doc = store.doc_for_cell(cell_id)
    if doc is None:
        raise HTTPException(404, "cell not found")
    cell = doc.get_cell(cell_id)
    if cell["cell_type"] != "code":
        raise HTTPException(400, "not a code cell")
    kernel: KernelService = await app.state.kernels.get(doc.name)
    doc.set_status(cell_id, "running")
    result = await kernel.enqueue(cell_id, cell.get("source") or "")
    doc.set_outputs(cell_id, result.outputs, result.execution_count, "ok" if result.ok else "error")
    return result


dist = ROOT / "frontend" / "dist"
if dist.exists():
    app.mount("/", StaticFiles(directory=str(dist), html=True), name="ui")
