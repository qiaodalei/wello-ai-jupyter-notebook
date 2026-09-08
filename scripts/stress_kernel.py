"""Hammer the kernel service: repeated execution plus restarts under load.

Guards against the class of bug where a lost message leaves a cell stuck at
"running" and the kernel permanently "busy".
"""
import json
import sys
import threading
import time
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:8000"
failures = []


def call(path, payload=None, method=None, timeout=30):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(
        BASE + path,
        data=data,
        method=method or ("POST" if data is not None else "GET"),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = resp.read()
    return json.loads(body) if body else None


def check(name, ok, detail=""):
    if not ok:
        failures.append(name)
    print(f"{'PASS' if ok else 'FAIL'}  {name}{('  — ' + str(detail)) if detail else ''}")


NOTEBOOKS = __import__("pathlib").Path(__file__).resolve().parents[1] / "notebooks"
existing = {p.name for p in NOTEBOOKS.glob("*.ipynb")}

call("/api/notebook/new", {})
cell = call("/api/cells", {"cell_type": "code", "source": "print('ping')"})
cid = cell["id"]

# 1) many sequential executions never wedge
worst = 0
ok = True
for i in range(30):
    t = time.time()
    try:
        res = call(f"/api/cells/{cid}/execute", {}, timeout=20)
    except Exception as exc:  # noqa: BLE001
        ok = False
        print(f"   run {i} failed: {exc}")
        break
    worst = max(worst, time.time() - t)
    if not res.get("ok"):
        ok = False
        print(f"   run {i} not ok: {res}")
        break
check("30 sequential executions", ok, f"slowest {worst * 1000:.0f}ms")
check("kernel idle afterwards", call("/api/health")["kernel"] == "idle")

# 2) back-to-back restarts stay fast and the kernel keeps working
slowest = 0.0
ok = True
for i in range(6):
    t = time.time()
    try:
        call("/api/kernel/restart", {}, timeout=60)
    except Exception as exc:  # noqa: BLE001
        ok = False
        print(f"   restart {i} failed: {exc}")
        break
    slowest = max(slowest, time.time() - t)
    res = call(f"/api/cells/{cid}/execute", {}, timeout=30)
    if not res.get("ok"):
        ok = False
        print(f"   execute after restart {i} not ok: {res}")
        break
check("6 restarts, each followed by an execution", ok, f"slowest restart {slowest:.1f}s")
check("restarts stay under 15s", slowest < 15, f"{slowest:.1f}s")

# 3) restart while a long cell is running: the request must return, the cell
#    must not stay "running", and the kernel must accept work again
slow = call("/api/cells", {"cell_type": "code", "source": "import time; time.sleep(60)"})
for round_no in range(3):
    result = {}

    def run_slow():
        try:
            result["res"] = call(f"/api/cells/{slow['id']}/execute", {}, timeout=90)
        except Exception as exc:  # noqa: BLE001
            result["err"] = str(exc)

    th = threading.Thread(target=run_slow, daemon=True)
    th.start()
    time.sleep(2)
    t = time.time()
    call("/api/kernel/restart", {}, timeout=60)
    check(f"restart #{round_no + 1} returns", time.time() - t < 45, f"{time.time() - t:.1f}s")
    th.join(timeout=30)
    check(f"restart #{round_no + 1} releases the running cell", not th.is_alive(), result)

    nb = call("/api/notebook")
    stuck = [c["id"] for c in nb["cells"] if c.get("status") == "running"]
    check(f"restart #{round_no + 1} leaves no cell stuck", not stuck, stuck)

    res = call(f"/api/cells/{cid}/execute", {}, timeout=30)
    check(f"kernel usable after restart #{round_no + 1}", res.get("ok"), res)

for path in NOTEBOOKS.glob("*.ipynb"):
    if path.name not in existing:
        path.unlink(missing_ok=True)

print()
print(f"{'FAILED: ' + ', '.join(failures) if failures else 'all good'}")
sys.exit(1 if failures else 0)
