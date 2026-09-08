"""Fill a scratch notebook with representative content for visual QA."""
import json
import time
import urllib.request
from pathlib import Path

BASE = "http://127.0.0.1:8000"


def call(path, payload=None, method=None):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(
        BASE + path,
        data=data,
        method=method or ("POST" if data is not None else "GET"),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req) as resp:
        body = resp.read()
    return json.loads(body) if body else None


nb = call("/api/notebook/new", {})
originals = [c["id"] for c in nb["cells"]]
scratch_name = nb["name"]

CELLS = [
    ("markdown", "# MNIST 数字识别\n\n先看数据分布，再训练一个小模型。**目标**：跑通闭环。"),
    ("code", "import numpy as np\nimport pandas as pd\n\nrng = np.random.default_rng(0)\ndf = pd.DataFrame({\"x\": rng.normal(size=200), \"y\": rng.normal(size=200)})\nprint(f\"shape = {df.shape}\")\ndf.head(3)"),
    ("code", "import matplotlib.pyplot as plt\n\nt = np.linspace(0, 8, 200)\nfig, ax = plt.subplots(figsize=(5, 2.4))\nax.plot(t, np.sin(t))\nax.set_title(\"sine\")\nplt.show()"),
    ("code", "labels = df[\"x\"] > 0\nprint(labels.value_counts())\nraise ValueError(\"label column is not binary encoded\")"),
]

for kind, source in CELLS:
    cell = call("/api/cells", {"cell_type": kind, "source": source})
    if kind == "code":
        call(f"/api/cells/{cell['id']}/execute", {})
        time.sleep(0.4)

for cell_id in originals:
    call(f"/api/cells/{cell_id}", method="DELETE")

time.sleep(2)
call("/api/notebook/save", {"name": "QA-Demo.ipynb"})

# /api/notebook/new already wrote an empty file to disk; drop it.
scratch = Path(__file__).resolve().parents[1] / "notebooks" / scratch_name
if scratch_name != "QA-Demo.ipynb":
    scratch.unlink(missing_ok=True)
print("ready")
