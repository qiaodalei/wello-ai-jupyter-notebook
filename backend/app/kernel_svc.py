from __future__ import annotations

import asyncio
import queue
import threading
import time
from dataclasses import dataclass, field

from jupyter_client import KernelManager

from .events import EventBus
from .notebook_store import normalize_output


# How long to keep draining iopub after the shell reply arrived, waiting for the
# trailing "idle" status. Bounded so a dropped message can't wedge a cell.
IDLE_GRACE_SECONDS = 2.0

# Upper bound on draining the startup snippet's own messages.
BOOTSTRAP_DRAIN_SECONDS = 5.0

# How many times to rebuild the client if it can't hear the kernel.
CONNECT_ATTEMPTS = 3

# Each notebook gets its own Python process; cap how many stay resident.
MAX_LIVE_KERNELS = 6

# If a cell produces no message at all for this long while the kernel is alive,
# the channels are desynced; fail loudly instead of hanging forever.
SILENCE_TIMEOUT_SECONDS = 20.0

KERNEL_BOOTSTRAP = """\
%matplotlib inline
try:
    import matplotlib
    from matplotlib import font_manager as _fm

    _have = {f.name for f in _fm.fontManager.ttflist}
    _prefer = [
        n
        for n in (
            "PingFang SC",
            "Heiti SC",
            "Hiragino Sans GB",
            "Songti SC",
            "Microsoft YaHei",
            "Noto Sans CJK SC",
            "Arial Unicode MS",
        )
        if n in _have
    ]
    matplotlib.rcParams["font.sans-serif"] = _prefer + ["DejaVu Sans"]
    matplotlib.rcParams["axes.unicode_minus"] = False
    del _fm, _have, _prefer
except Exception:
    pass
"""


@dataclass
class ExecutionResult:
    ok: bool
    execution_count: int | None
    outputs: list[dict] = field(default_factory=list)
    status: str = "ok"
    elapsed_ms: int = 0
    error: str | None = None


class KernelService:
    """One Python process, owned by one notebook."""

    def __init__(self, bus: EventBus, notebook: str) -> None:
        self.bus = bus
        self.notebook = notebook
        self._km: KernelManager | None = None
        self._kc = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._exec_lock = threading.Lock()
        self.status = "dead"
        self._queue: asyncio.Queue | None = None
        self._worker_task: asyncio.Task | None = None
        self._cwd: str | None = None
        # Bumped whenever the client is replaced. An execution from an older
        # generation stops waiting for messages that will never arrive.
        self._generation = 0

    def _emit(self, event: dict) -> None:
        if not self._loop:
            return
        event.setdefault("notebook", self.notebook)
        asyncio.run_coroutine_threadsafe(self.bus.publish(event), self._loop)

    async def _publish(self, event: dict) -> None:
        event.setdefault("notebook", self.notebook)
        await self.bus.publish(event)

    async def start(self, cwd: str | None = None) -> None:
        self._loop = asyncio.get_running_loop()
        self._cwd = cwd
        self.status = "starting"
        await self._publish({"type": "kernel_status", "status": "starting"})
        await asyncio.to_thread(self._start_sync, cwd)
        self._queue = asyncio.Queue()
        self._worker_task = asyncio.create_task(self._worker())

    def _start_sync(self, cwd: str | None) -> None:
        self._km = KernelManager(kernel_name="python3")
        if cwd:
            self._km.cwd = cwd
        self._km.start_kernel()
        # The client's sockets are not thread-safe, so every touch of self._kc
        # (start, restart, bootstrap, execute) runs under this lock.
        with self._exec_lock:
            self._generation += 1
            self._connect_client()
        self.status = "idle"
        self._emit({"type": "kernel_status", "status": "idle"})

    async def shutdown(self) -> None:
        if self._worker_task:
            self._worker_task.cancel()
        await asyncio.to_thread(self._shutdown_sync)

    def _shutdown_sync(self) -> None:
        self._generation += 1
        got_lock = self._exec_lock.acquire(timeout=5)
        try:
            if self._kc:
                self._kc.stop_channels()
            if self._km:
                self._km.shutdown_kernel(now=True)
        except Exception:
            pass
        finally:
            if got_lock:
                self._exec_lock.release()
        self.status = "dead"

    async def restart(self) -> None:
        self.status = "starting"
        await self._publish({"type": "kernel_status", "status": "starting"})
        await asyncio.to_thread(self._restart_sync)
        self.status = "idle"
        await self._publish({"type": "kernel_status", "status": "idle"})

    def _restart_sync(self) -> None:
        if not self._km or self._kc is None:
            self._start_sync(self._cwd)
            return
        # A cell may still be sitting in _execute_sync holding the lock; bumping
        # the generation first makes it give up (it re-checks every 0.2s) instead
        # of blocking the restart.
        self._generation += 1
        self._km.restart_kernel(now=True)
        got_lock = self._exec_lock.acquire(timeout=15)
        try:
            # Keep the existing client. A restart reuses the same ports and the
            # channels reconnect on their own; a client built after the restart
            # never becomes ready, which used to leave the kernel wedged.
            self._kc.wait_for_ready(timeout=30)
            self._bootstrap_sync()
        finally:
            if got_lock:
                self._exec_lock.release()

    def _connect_client(self) -> None:
        """Attach a client and don't return until the channels are proven live.

        Must be called with _exec_lock held.
        """
        assert self._km is not None
        for attempt in range(CONNECT_ATTEMPTS):
            client = self._km.client()
            client.start_channels()
            try:
                client.wait_for_ready(timeout=30)
            except RuntimeError:
                client.stop_channels()
                continue
            self._kc = client
            if self._bootstrap_sync() or attempt == CONNECT_ATTEMPTS - 1:
                return
            # Channels are half-deaf; throw this client away and build another.
            try:
                client.stop_channels()
            except Exception:
                pass
            self._kc = None

    def _bootstrap_sync(self) -> bool:
        """Run the startup snippet and confirm we can hear the kernel.

        Returns True when the snippet's own iopub "idle" came back. A False here
        means the SUB socket missed the traffic (the classic ZMQ slow-joiner),
        which would otherwise show up later as a cell stuck at "running".
        """
        if not self._kc:
            return False
        try:
            msg_id = self._kc.execute(
                KERNEL_BOOTSTRAP,
                silent=True,
                store_history=False,
                allow_stdin=False,
            )
        except Exception:
            return False
        # Drain the bootstrap's own reply/iopub traffic so it can't be mistaken
        # for a later cell's messages. Bounded: the bootstrap is best-effort and
        # must never hold up kernel start or restart.
        deadline = time.time() + BOOTSTRAP_DRAIN_SECONDS
        while time.time() < deadline:
            try:
                self._kc.get_shell_msg(timeout=0)
            except Exception:
                pass
            try:
                msg = self._kc.get_iopub_msg(timeout=0.2)
            except Exception:
                continue
            if msg.get("parent_header", {}).get("msg_id") != msg_id:
                continue
            content = msg.get("content") or {}
            if msg.get("msg_type") == "status" and content.get("execution_state") == "idle":
                return True
        return False

    def interrupt(self) -> None:
        if self._km and self._km.is_alive():
            self._km.interrupt_kernel()

    def is_alive(self) -> bool:
        return bool(self._km and self._km.is_alive())

    async def enqueue(self, cell_id: str, code: str) -> ExecutionResult:
        if not self._queue:
            raise RuntimeError("kernel not started")
        loop = asyncio.get_running_loop()
        fut: asyncio.Future = loop.create_future()
        await self._queue.put((cell_id, code, fut))
        return await fut

    async def _worker(self) -> None:
        assert self._queue is not None
        while True:
            cell_id, code, fut = await self._queue.get()
            try:
                result = await self._run(cell_id, code)
                if not fut.done():
                    fut.set_result(result)
            except Exception as exc:
                if not fut.done():
                    fut.set_exception(exc)

    async def _run(self, cell_id: str, code: str) -> ExecutionResult:
        self.status = "busy"
        await self._publish({"type": "kernel_status", "status": "busy"})
        await self._publish({"type": "cell_running", "cell_id": cell_id})
        result = await asyncio.to_thread(self._execute_sync, cell_id, code)
        self.status = "idle" if self.is_alive() else "dead"
        await self._publish(
            {
                "type": "cell_finished",
                "cell_id": cell_id,
                "ok": result.ok,
                "execution_count": result.execution_count,
                "outputs": result.outputs,
                "elapsed_ms": result.elapsed_ms,
                "status": "ok" if result.ok else "error",
            }
        )
        await self._publish({"type": "kernel_status", "status": self.status})
        return result

    def _execute_sync(self, cell_id: str, code: str) -> ExecutionResult:
        started = time.time()
        if not self._kc or not self._km:
            return ExecutionResult(ok=False, execution_count=None, status="error", error="kernel missing")
        with self._exec_lock:
            generation = self._generation
            msg_id = self._kc.execute(code, store_history=True, allow_stdin=False, stop_on_error=True)
            outputs: list[dict] = []
            execution_count = None
            status = "ok"
            reply: dict | None = None
            drain_until: float | None = None
            heard_anything = False
            while True:
                if self._generation != generation:
                    status = "error"
                    outputs.append(
                        {
                            "output_type": "error",
                            "ename": "Aborted",
                            "evalue": "Kernel restarted during execution",
                            "traceback": [],
                        }
                    )
                    break
                if not self._km.is_alive():
                    status = "error"
                    outputs.append(
                        {
                            "output_type": "error",
                            "ename": "KernelDied",
                            "evalue": "Python kernel died during execution",
                            "traceback": [],
                        }
                    )
                    break
                # The shell reply is the authoritative "this cell is done" signal.
                # Without it, a dropped iopub idle message would hang the cell forever.
                if reply is None:
                    try:
                        candidate = self._kc.get_shell_msg(timeout=0)
                    except queue.Empty:
                        candidate = None
                    if candidate and candidate.get("parent_header", {}).get("msg_id") == msg_id:
                        reply = candidate
                        drain_until = time.time() + IDLE_GRACE_SECONDS
                try:
                    msg = self._kc.get_iopub_msg(timeout=0.2)
                except queue.Empty:
                    if drain_until is not None and time.time() > drain_until:
                        break
                    if not heard_anything and time.time() - started > SILENCE_TIMEOUT_SECONDS:
                        status = "error"
                        outputs.append(
                            {
                                "output_type": "error",
                                "ename": "KernelUnreachable",
                                "evalue": (
                                    "内核没有回应这次执行（消息通道失联）。"
                                    "代码可能已经在内核里跑了，请点 Restart 重启内核后重试。"
                                ),
                                "traceback": [],
                            }
                        )
                        break
                    continue
                if msg.get("parent_header", {}).get("msg_id") != msg_id:
                    continue
                heard_anything = True
                msg_type = msg.get("msg_type")
                content = msg.get("content") or {}
                if msg_type == "status" and content.get("execution_state") == "idle":
                    break
                if msg_type == "stream":
                    chunk = {
                        "output_type": "stream",
                        "name": content.get("name") or "stdout",
                        "text": content.get("text") or "",
                    }
                    _merge_stream(outputs, chunk)
                    self._emit(
                        {
                            "type": "stream",
                            "cell_id": cell_id,
                            "name": chunk["name"],
                            "text": chunk["text"],
                        }
                    )
                elif msg_type in ("execute_result", "display_data", "error"):
                    raw = {"output_type": msg_type, **content}
                    normalized = normalize_output(raw)
                    outputs.append(normalized)
                    if msg_type == "error":
                        status = "error"
                    if msg_type == "execute_result":
                        execution_count = content.get("execution_count")
                    self._emit({"type": "output", "cell_id": cell_id, "output": normalized})
                elif msg_type == "execute_input":
                    execution_count = content.get("execution_count", execution_count)
            if reply is None:
                try:
                    candidate = self._kc.get_shell_msg(timeout=2)
                except queue.Empty:
                    candidate = None
                if candidate and candidate.get("parent_header", {}).get("msg_id") == msg_id:
                    reply = candidate
            if reply is not None:
                rc = reply.get("content") or {}
                execution_count = rc.get("execution_count", execution_count)
                if rc.get("status") == "error":
                    status = "error"
                elif rc.get("status") == "aborted":
                    status = "error"
                    outputs.append(
                        {
                            "output_type": "error",
                            "ename": "Aborted",
                            "evalue": "Execution was interrupted",
                            "traceback": [],
                        }
                    )
            elapsed = int((time.time() - started) * 1000)
            return ExecutionResult(
                ok=status == "ok",
                execution_count=execution_count,
                outputs=outputs,
                status=status,
                elapsed_ms=elapsed,
            )


def _merge_stream(outputs: list[dict], chunk: dict) -> None:
    if outputs and outputs[-1].get("output_type") == "stream" and outputs[-1].get("name") == chunk["name"]:
        outputs[-1]["text"] += chunk["text"]
        return
    outputs.append(dict(chunk))


class KernelPool:
    """A kernel per notebook, started the first time that notebook runs code.

    Separate processes are what make two notebooks usable at once: their globals
    can't collide and a long cell in one doesn't block the other's queue.
    """

    def __init__(self, bus: EventBus, cwd: str | None = None) -> None:
        self.bus = bus
        self.cwd = cwd
        self.kernels: dict[str, KernelService] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._used: dict[str, float] = {}
        self._warming: dict[str, asyncio.Task] = {}
        # Notebooks that must keep their kernel (on screen, or mid-run).
        self._protected = lambda: set()

    def protect_with(self, provider) -> None:
        self._protected = provider

    async def get(self, notebook: str) -> KernelService:
        """The notebook's kernel, booting it if this is its first execution."""
        lock = self._locks.setdefault(notebook, asyncio.Lock())
        async with lock:
            self._used[notebook] = time.time()
            svc = self.kernels.get(notebook)
            if svc is None or not svc.is_alive():
                if svc is not None:
                    await svc.shutdown()
                svc = KernelService(self.bus, notebook)
                self.kernels[notebook] = svc
                await svc.start(cwd=self.cwd)
                self._evict_stale(notebook)
            return svc

    def _evict_stale(self, just_started: str) -> None:
        """Keep the process count bounded; a browsed-and-forgotten notebook
        should not hold a Python process forever.

        Only untouched, idle, unprotected kernels go. If nothing qualifies we
        stay over the cap rather than kill work someone is waiting on.
        """
        if len(self.kernels) <= MAX_LIVE_KERNELS:
            return
        safe = self._protected() | {just_started}
        stale = sorted(
            (
                name
                for name, svc in self.kernels.items()
                if name not in safe and svc.status != "busy"
            ),
            key=lambda name: self._used.get(name, 0.0),
        )
        for name in stale[: len(self.kernels) - MAX_LIVE_KERNELS]:
            self.discard(name)

    def warm(self, notebook: str) -> None:
        """Bring a notebook's kernel up in the background, and say where it stands.

        Called when a notebook comes on screen: the switch stays instant, and
        the status bar shows this notebook's kernel instead of the last one's.
        """
        self._used[notebook] = time.time()
        live = self.kernels.get(notebook)
        if live is not None:
            asyncio.create_task(live._publish({"type": "kernel_status", "status": live.status}))
            return
        task = asyncio.create_task(self.get(notebook))
        self._warming[notebook] = task
        task.add_done_callback(lambda _: self._warming.pop(notebook, None))

    def peek(self, notebook: str) -> KernelService | None:
        return self.kernels.get(notebook)

    def status(self, notebook: str) -> str:
        svc = self.kernels.get(notebook)
        if svc:
            return svc.status
        return "starting" if notebook in self._warming else "idle"

    def is_alive(self, notebook: str) -> bool:
        """A notebook with no kernel yet is fine: one starts on its first run."""
        svc = self.kernels.get(notebook)
        return svc.is_alive() if svc else True

    def live_names(self) -> list[str]:
        return [name for name, svc in self.kernels.items() if svc.is_alive()]

    def rename(self, old: str, new: str) -> None:
        svc = self.kernels.pop(old, None)
        if svc is None:
            return
        svc.notebook = new
        self.kernels[new] = svc
        self._locks.pop(old, None)
        self._used[new] = self._used.pop(old, time.time())

    def discard(self, notebook: str) -> None:
        """Drop a kernel now, let the process die in the background.

        Callers are usually answering a UI action (delete a file, recycle an
        idle kernel) and shouldn't wait on `shutdown_kernel`.
        """
        warming = self._warming.pop(notebook, None)
        if warming:
            warming.cancel()
        svc = self.kernels.pop(notebook, None)
        self._locks.pop(notebook, None)
        self._used.pop(notebook, None)
        if svc:
            asyncio.create_task(svc.shutdown())

    async def close(self, notebook: str) -> None:
        svc = self.kernels.get(notebook)
        self.discard(notebook)
        if svc:
            await asyncio.to_thread(svc._shutdown_sync)

    async def shutdown_all(self) -> None:
        for svc in list(self.kernels.values()):
            try:
                await svc.shutdown()
            except Exception:
                pass
        self.kernels.clear()
