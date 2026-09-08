"""Import/delete notebook files through the API."""
import json
import sys
import urllib.request
import uuid
from pathlib import Path

BASE = "http://127.0.0.1:8000"
NOTEBOOKS = Path(__file__).resolve().parents[1] / "notebooks"
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


def upload(files, timeout=30):
    """files: list of (filename, bytes)."""
    boundary = uuid.uuid4().hex
    parts = []
    for filename, content in files:
        parts.append(
            f'--{boundary}\r\nContent-Disposition: form-data; name="files"; filename="{filename}"\r\n'
            f"Content-Type: application/json\r\n\r\n".encode() + content + b"\r\n"
        )
    body = b"".join(parts) + f"--{boundary}--\r\n".encode()
    req = urllib.request.Request(
        BASE + "/api/files/import",
        data=body,
        method="POST",
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())


def check(name, ok, detail=""):
    if not ok:
        failures.append(name)
    print(f"{'PASS' if ok else 'FAIL'}  {name}{('  — ' + str(detail)) if detail else ''}")


NB = json.dumps(
    {
        "cells": [
            {"cell_type": "markdown", "metadata": {}, "source": "# 导入进来的"},
            {
                "cell_type": "code",
                "metadata": {},
                "execution_count": 3,
                "source": "print('imported')",
                "outputs": [{"output_type": "stream", "name": "stdout", "text": "imported\n"}],
            },
        ],
        "metadata": {"kernelspec": {"name": "python3", "display_name": "Python 3"}},
        "nbformat": 4,
        "nbformat_minor": 5,
    }
).encode()

before = {p.name for p in NOTEBOOKS.glob("*.ipynb")}

# import
res = upload([("ImportTest.ipynb", NB)])
check("import returns the new file", res["imported"] == ["ImportTest.ipynb"], res["imported"])
check("imported file is on disk", (NOTEBOOKS / "ImportTest.ipynb").exists())
check("imported notebook is opened", res["notebook"]["name"] == "ImportTest.ipynb", res["notebook"]["name"])
cells = res["notebook"]["cells"]
check("imported cells survive", len(cells) == 2 and cells[0]["cell_type"] == "markdown", len(cells))
check("imported outputs survive", bool(cells[1].get("outputs")), cells[1].get("outputs"))

# same name twice -> no clobber
res2 = upload([("ImportTest.ipynb", NB)])
check("re-import does not overwrite", res2["imported"] == ["ImportTest.ipynb-1.ipynb"]
      or res2["imported"] == ["ImportTest-1.ipynb"], res2["imported"])
dup = res2["imported"][0]

# multiple at once
res3 = upload([("Multi-A.ipynb", NB), ("Multi-B.ipynb", NB)])
check("multi-file import", res3["imported"] == ["Multi-A.ipynb", "Multi-B.ipynb"], res3["imported"])
check("last import is opened", res3["notebook"]["name"] == "Multi-B.ipynb", res3["notebook"]["name"])

# a name whose file was removed behind the backend's back must not be served
# from the copy still open in memory
first = json.dumps(
    {
        "cells": [{"cell_type": "markdown", "source": "# 第一版", "metadata": {}}],
        "metadata": {},
        "nbformat": 4,
        "nbformat_minor": 5,
    },
    ensure_ascii=False,
).encode()
second = first.replace("第一版".encode(), "第二版".encode())
upload([("Shadow.ipynb", first)])
(NOTEBOOKS / "Shadow.ipynb").unlink()
res_shadow = upload([("Shadow.ipynb", second)])
shown = res_shadow["notebook"]["cells"][0]["source"]
check("re-import reads the new file, not the open copy", "第二版" in shown, shown)
call("/api/files/Shadow.ipynb", method="DELETE")
call("/api/notebook/open", {"name": "Multi-B.ipynb"})

# rubbish is rejected
try:
    upload([("bad.ipynb", b"not a notebook at all")])
    check("invalid file rejected", False, "no error raised")
except urllib.error.HTTPError as exc:
    check("invalid file rejected", exc.code == 400, f"HTTP {exc.code}")
check("invalid file not written", not (NOTEBOOKS / "bad.ipynb").exists())

# delete a file that is not open
res4 = call(f"/api/files/{dup}", method="DELETE")
check("delete removes the file", not (NOTEBOOKS / dup).exists())
check("delete leaves current notebook alone", res4["switched"] is False, res4["switched"])
check("delete returns the new list", dup not in [f["name"] for f in res4["files"]])

# delete the notebook that is currently open -> must switch to something valid
opened = call("/api/notebook")["name"]
res5 = call(f"/api/files/{opened}", method="DELETE")
check("deleting the open notebook switches away", res5["switched"] is True, res5)
check("switched to an existing notebook", res5["notebook"]["name"] != opened, res5["notebook"]["name"])
check("kernel still fine after switch", call("/api/health")["alive"])

# missing / traversal names are refused (the traversal one never even routes)
for bad in (f"nope-{uuid.uuid4().hex}.ipynb", "..%2F..%2Fetc%2Fpasswd"):
    try:
        call(f"/api/files/{bad}", method="DELETE")
        check(f"delete {bad[:22]} rejected", False, "no error")
    except urllib.error.HTTPError as exc:
        check(f"delete {bad[:22]} rejected", 400 <= exc.code < 500, f"HTTP {exc.code}")

# an import can't write outside notebooks/
res6 = upload([("../../escaped.ipynb", NB)])
landed = NOTEBOOKS / res6["imported"][0]
check("import stays inside notebooks/", landed.resolve().parent == NOTEBOOKS.resolve(), landed)
check("nothing written above notebooks/", not (NOTEBOOKS.parent.parent / "escaped.ipynb").exists())

for path in NOTEBOOKS.glob("*.ipynb"):
    if path.name not in before:
        path.unlink(missing_ok=True)
if not list(NOTEBOOKS.glob("*.ipynb")):
    call("/api/notebook/new", {})

print()
print("FAILED: " + ", ".join(failures) if failures else "all good")
sys.exit(1 if failures else 0)
