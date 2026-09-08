from __future__ import annotations

import re
import uuid
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path

import nbformat
from nbformat.v4 import new_code_cell, new_markdown_cell, new_notebook, new_output

from .config import NOTEBOOKS_DIR

ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def new_id() -> str:
    return uuid.uuid4().hex[:12]


def source_to_str(source) -> str:
    if source is None:
        return ""
    if isinstance(source, list):
        return "".join(source)
    return str(source)


def empty_notebook_payload(name: str = "Untitled.ipynb") -> dict:
    cell = {
        "id": new_id(),
        "cell_type": "code",
        "source": "",
        "outputs": [],
        "execution_count": None,
        "metadata": {},
        "status": "idle",
    }
    return {
        "name": name,
        "path": str(NOTEBOOKS_DIR / name),
        "dirty": False,
        "nbformat": 4,
        "nbformat_minor": 5,
        "metadata": default_metadata(),
        "cells": [cell],
    }


def default_metadata() -> dict:
    return {
        "kernelspec": {
            "display_name": "Python 3",
            "language": "python",
            "name": "python3",
        },
        "language_info": {
            "name": "python",
            "pygments_lexer": "ipython3",
        },
        "ai_jupyter": {"created": datetime.now(timezone.utc).isoformat()},
    }


def from_nbformat(nb, name: str, path: str) -> dict:
    cells = []
    for cell in nb.cells:
        cell_type = cell.get("cell_type", "code")
        if cell_type not in ("code", "markdown"):
            cell_type = "markdown"
        item = {
            "id": cell.get("id") or new_id(),
            "cell_type": cell_type,
            "source": source_to_str(cell.get("source")),
            "metadata": dict(cell.get("metadata") or {}),
            "status": "idle",
        }
        if cell_type == "code":
            item["outputs"] = [normalize_output(o) for o in (cell.get("outputs") or [])]
            item["execution_count"] = cell.get("execution_count")
        else:
            item["outputs"] = []
            item["execution_count"] = None
        cells.append(item)
    if not cells:
        cells = empty_notebook_payload(name)["cells"]
    return {
        "name": name,
        "path": path,
        "dirty": False,
        "nbformat": 4,
        "nbformat_minor": 5,
        "metadata": dict(nb.get("metadata") or default_metadata()),
        "cells": cells,
    }


def to_nbformat(payload: dict):
    nb = new_notebook(metadata=payload.get("metadata") or default_metadata())
    nb.nbformat = 4
    nb.nbformat_minor = 5
    cells = []
    for cell in payload.get("cells") or []:
        source = cell.get("source") or ""
        if cell.get("cell_type") == "markdown":
            node = new_markdown_cell(source=source)
        else:
            node = new_code_cell(source=source)
            node["execution_count"] = cell.get("execution_count")
            outputs = []
            for raw in cell.get("outputs") or []:
                try:
                    outputs.append(new_output(**_output_kwargs(raw)))
                except Exception:
                    continue
            node["outputs"] = outputs
        node["id"] = cell.get("id") or new_id()
        node["metadata"] = cell.get("metadata") or {}
        cells.append(node)
    nb.cells = cells
    return nb


def _output_kwargs(raw: dict) -> dict:
    kind = raw.get("output_type") or "stream"
    if kind == "stream":
        return {
            "output_type": "stream",
            "name": raw.get("name") or "stdout",
            "text": raw.get("text") or "",
        }
    if kind == "error":
        return {
            "output_type": "error",
            "ename": raw.get("ename") or "Error",
            "evalue": raw.get("evalue") or "",
            "traceback": raw.get("traceback") or [],
        }
    data = raw.get("data") or {}
    meta = raw.get("metadata") or {}
    if kind == "execute_result":
        return {
            "output_type": "execute_result",
            "data": data,
            "metadata": meta,
            "execution_count": raw.get("execution_count") or 1,
        }
    return {
        "output_type": "display_data",
        "data": data,
        "metadata": meta,
    }


def normalize_output(raw) -> dict:
    if hasattr(raw, "items"):
        raw = dict(raw)
    kind = raw.get("output_type") or "stream"
    if kind == "stream":
        return {
            "output_type": "stream",
            "name": raw.get("name") or "stdout",
            "text": source_to_str(raw.get("text")),
        }
    if kind == "error":
        return {
            "output_type": "error",
            "ename": raw.get("ename") or "Error",
            "evalue": str(raw.get("evalue") or ""),
            "traceback": [t if isinstance(t, str) else str(t) for t in (raw.get("traceback") or [])],
        }
    data = {}
    for key, value in dict(raw.get("data") or {}).items():
        if key.startswith("image/"):
            data[key] = source_to_str(value).replace("\n", "")
        else:
            data[key] = source_to_str(value)
    out = {
        "output_type": kind if kind in ("execute_result", "display_data") else "display_data",
        "data": data,
        "metadata": dict(raw.get("metadata") or {}),
    }
    if kind == "execute_result":
        out["execution_count"] = raw.get("execution_count")
    return out


def summarize_outputs(outputs: list[dict], limit: int = 4000) -> str:
    parts: list[str] = []
    for out in outputs:
        kind = out.get("output_type")
        if kind == "stream":
            text = ANSI_RE.sub("", out.get("text") or "")
            parts.append(f"[{out.get('name', 'stdout')}]\n{text}")
        elif kind == "error":
            tb = "\n".join(out.get("traceback") or [])
            parts.append(f"[error] {out.get('ename')}: {out.get('evalue')}\n{tb}")
        else:
            data = out.get("data") or {}
            if any(k.startswith("image/") for k in data):
                parts.append("[display] image output present (image/png or image/jpeg)")
            if "text/plain" in data:
                parts.append(data["text/plain"])
            elif "text/html" in data:
                parts.append("[html output]")
    text = "\n".join(parts).strip() or "(no output)"
    if len(text) > limit:
        return text[:limit] + f"\n...[truncated {len(text) - limit} chars]"
    return text


def safe_notebook_path(name: str) -> Path:
    NOTEBOOKS_DIR.mkdir(parents=True, exist_ok=True)
    filename = Path(name).name
    if not filename.endswith(".ipynb"):
        filename += ".ipynb"
    path = (NOTEBOOKS_DIR / filename).resolve()
    if path.parent != NOTEBOOKS_DIR.resolve():
        raise ValueError("invalid notebook path")
    return path


def list_notebooks() -> list[dict]:
    NOTEBOOKS_DIR.mkdir(parents=True, exist_ok=True)
    items = []
    for path in sorted(NOTEBOOKS_DIR.glob("*.ipynb")):
        stat = path.stat()
        items.append(
            {
                "name": path.name,
                "path": str(path),
                "mtime": datetime.fromtimestamp(stat.st_mtime).isoformat(),
                "size": stat.st_size,
            }
        )
    return items


def unique_name(filename: str) -> Path:
    """A free path for `filename`, adding -1, -2 ... when it is taken."""
    path = safe_notebook_path(filename)
    if not path.exists():
        return path
    stem = path.stem
    n = 1
    while True:
        candidate = path.with_name(f"{stem}-{n}.ipynb")
        if not candidate.exists():
            return candidate
        n += 1


def unique_untitled() -> str:
    NOTEBOOKS_DIR.mkdir(parents=True, exist_ok=True)
    if not (NOTEBOOKS_DIR / "Untitled.ipynb").exists():
        return "Untitled.ipynb"
    n = 1
    while (NOTEBOOKS_DIR / f"Untitled{n}.ipynb").exists():
        n += 1
    return f"Untitled{n}.ipynb"


class NotebookDoc:
    """One open notebook. Cell edits and outputs land here, not in a global."""

    def __init__(self, payload: dict) -> None:
        self.payload = payload

    @property
    def name(self) -> str:
        return self.payload.get("name") or "untitled"

    def snapshot(self) -> dict:
        return deepcopy(self.payload)

    def get_cell(self, cell_id: str) -> dict | None:
        for cell in self.payload["cells"]:
            if cell["id"] == cell_id:
                return cell
        return None

    def cell_index(self, cell_id: str) -> int:
        for i, cell in enumerate(self.payload["cells"]):
            if cell["id"] == cell_id:
                return i
        return -1

    def update_cell_source(self, cell_id: str, source: str) -> dict | None:
        cell = self.get_cell(cell_id)
        if not cell:
            return None
        cell["source"] = source
        self.payload["dirty"] = True
        return deepcopy(cell)

    def set_cell_type(self, cell_id: str, cell_type: str) -> dict | None:
        cell = self.get_cell(cell_id)
        if not cell:
            return None
        cell["cell_type"] = cell_type
        if cell_type != "code":
            cell["outputs"] = []
            cell["execution_count"] = None
        self.payload["dirty"] = True
        return deepcopy(cell)

    def insert_cell(self, index: int | None, cell_type: str, source: str = "", metadata: dict | None = None) -> dict:
        cells = self.payload["cells"]
        if index is None:
            index = len(cells)
        index = max(0, min(index, len(cells)))
        cell = {
            "id": new_id(),
            "cell_type": cell_type if cell_type in ("code", "markdown") else "code",
            "source": source,
            "outputs": [],
            "execution_count": None,
            "metadata": metadata or {},
            "status": "idle",
        }
        cells.insert(index, cell)
        self.payload["dirty"] = True
        return deepcopy(cell)

    def replace_cell(self, cell_id: str, source: str | None = None, cell_type: str | None = None) -> dict | None:
        cell = self.get_cell(cell_id)
        if not cell:
            return None
        if source is not None:
            cell["source"] = source
        if cell_type:
            cell["cell_type"] = cell_type
            if cell_type != "code":
                cell["outputs"] = []
                cell["execution_count"] = None
        cell["status"] = "idle"
        self.payload["dirty"] = True
        return deepcopy(cell)

    def delete_cell(self, cell_id: str) -> bool:
        idx = self.cell_index(cell_id)
        if idx < 0:
            return False
        self.payload["cells"].pop(idx)
        if not self.payload["cells"]:
            self.insert_cell(0, "code", "")
        self.payload["dirty"] = True
        return True

    def move_cell(self, cell_id: str, delta: int) -> bool:
        idx = self.cell_index(cell_id)
        if idx < 0:
            return False
        new_idx = idx + delta
        cells = self.payload["cells"]
        if new_idx < 0 or new_idx >= len(cells):
            return False
        cells[idx], cells[new_idx] = cells[new_idx], cells[idx]
        self.payload["dirty"] = True
        return True

    def merge_with_next(self, cell_id: str) -> dict | None:
        idx = self.cell_index(cell_id)
        cells = self.payload["cells"]
        if idx < 0 or idx >= len(cells) - 1:
            return None
        a, b = cells[idx], cells[idx + 1]
        if a["cell_type"] != b["cell_type"]:
            return None
        sep = "\n\n" if a["cell_type"] == "markdown" else "\n"
        a["source"] = (a.get("source") or "").rstrip() + sep + (b.get("source") or "").lstrip()
        if a["cell_type"] == "code":
            a["outputs"] = []
            a["execution_count"] = None
        cells.pop(idx + 1)
        self.payload["dirty"] = True
        return deepcopy(a)

    def set_outputs(self, cell_id: str, outputs: list[dict], execution_count, status: str) -> dict | None:
        cell = self.get_cell(cell_id)
        if not cell:
            return None
        cell["outputs"] = outputs
        cell["execution_count"] = execution_count
        cell["status"] = status
        self.payload["dirty"] = True
        return deepcopy(cell)

    def set_status(self, cell_id: str, status: str) -> None:
        cell = self.get_cell(cell_id)
        if cell:
            cell["status"] = status

    def clear_outputs(self, cell_id: str) -> dict | None:
        cell = self.get_cell(cell_id)
        if not cell:
            return None
        cell["outputs"] = []
        cell["execution_count"] = None
        cell["status"] = "idle"
        self.payload["dirty"] = True
        return deepcopy(cell)

    def save(self, name: str | None = None) -> dict:
        filename = name or self.payload.get("name") or unique_untitled()
        path = safe_notebook_path(filename)
        nbformat.write(to_nbformat(self.payload), path)
        self.payload["name"] = path.name
        self.payload["path"] = str(path)
        self.payload["dirty"] = False
        return self.snapshot()


class NotebookStore:
    """Every notebook the session has opened, all live at the same time.

    Switching files only moves `active`; the other documents keep their cells and
    outputs in memory, so an agent can keep writing into one while the user reads
    another.
    """

    def __init__(self) -> None:
        NOTEBOOKS_DIR.mkdir(parents=True, exist_ok=True)
        self.docs: dict[str, NotebookDoc] = {}
        self.active: str = ""
        # Start on the default notebook, reading it if a previous session left
        # one behind — a placeholder under the same name would shadow the file.
        default = NOTEBOOKS_DIR / "Untitled.ipynb"
        if default.exists():
            try:
                self.load_file(default.name)
                return
            except Exception:
                pass
        first = NotebookDoc(empty_notebook_payload(unique_untitled()))
        self.docs[first.name] = first
        self.active = first.name

    def snapshot(self) -> dict:
        return self.doc().snapshot()

    def doc(self, name: str | None = None) -> NotebookDoc:
        """The named document, or the one on screen. Falls back to the active."""
        if name is None:
            return self.docs[self.active]
        found = self.docs.get(name)
        return found if found is not None else self.docs[self.active]

    def doc_for_cell(self, cell_id: str) -> NotebookDoc | None:
        """Find a cell's document by id, so an edit lands in the file it came
        from even if the user has already switched away."""
        for doc in self.docs.values():
            if doc.get_cell(cell_id):
                return doc
        return None

    def is_open(self, name: str) -> bool:
        return name in self.docs

    @property
    def payload(self) -> dict:
        return self.docs[self.active].payload

    def load_file(self, name: str) -> dict:
        """Show a notebook, reusing the live copy when it is already open."""
        path = safe_notebook_path(name)
        existing = self.docs.get(path.name)
        if existing is not None:
            self.active = path.name
            return existing.snapshot()
        nb = nbformat.read(path, as_version=4)
        doc = NotebookDoc(from_nbformat(nb, path.name, str(path)))
        self.docs[path.name] = doc
        self.active = path.name
        return doc.snapshot()

    def save_file(self, name: str | None = None) -> dict:
        doc = self.doc()
        snap = doc.save(name)
        self._rekey(doc)
        return snap

    def new_file(self) -> dict:
        name = unique_untitled()
        doc = NotebookDoc(empty_notebook_payload(name))
        self.docs[name] = doc
        self.active = name
        return doc.save(name)

    def forget(self, name: str) -> None:
        """Drop an open document without picking a replacement.

        Used when a file is (re)written behind our back: the next `load_file`
        must read the new bytes instead of serving the copy we already hold.
        """
        self.docs.pop(name, None)

    def close(self, name: str) -> None:
        """Forget a notebook (it was deleted); never leave zero documents."""
        self.docs.pop(name, None)
        if not self.docs:
            self.new_file()
        elif self.active == name:
            self.active = next(iter(self.docs))

    def _rekey(self, doc: NotebookDoc) -> None:
        """Re-index after a rename so `docs` stays keyed by filename."""
        for key, value in list(self.docs.items()):
            if value is doc and key != doc.name:
                del self.docs[key]
        self.docs[doc.name] = doc
        self.active = doc.name
