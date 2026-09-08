from __future__ import annotations

import json
import time
import traceback
import httpx

from .config import AppSettings
from .events import EventBus
from .kernel_svc import KernelPool, KernelService
from .notebook_store import NotebookDoc, NotebookStore, summarize_outputs

# Batch streamed tokens instead of pushing a websocket frame per token.
DELTA_FLUSH_SECONDS = 0.08

# Streaming keeps the connection open for the whole round.
STREAM_TIMEOUT = httpx.Timeout(300.0, connect=20.0)

# How much of a notebook's conversation to carry into the next question.
MAX_QA_ROUNDS = 5

SYSTEM_PROMPT = """你是 Wello AI Jupyter 里的助手。用户在网页上提任何适合用 Notebook 做的事，你把讲解和代码写进真实的单元格，在本机 Python 内核里执行，根据运行结果继续改，直到做完或确实做不下去。

需求可能是任何形式，别预设是数据科学任务，例如：
- 数据处理与分析：清洗、统计、透视、合并、导出
- 图表可视化：画一张图、调样式、做成一页报告
- 机器学习 / 深度学习：训练、评估、调参、推理
- 科学计算与数学：数值求解、符号推导（sympy）、仿真、概率模拟
- 算法与代码验证：实现一个算法、跑测试用例、比较两种写法的性能
- 文本处理：正则、解析日志、分词、编码转换
- 文件批处理：批量重命名、格式转换（csv/json/excel/图片）、遍历目录
- 抓数据：调 HTTP 接口、解析返回、抓网页
- 学习与教学：讲清一个概念并用可运行的例子演示、探索某个库的用法
- 临时脚本：算一笔账、生成一批测试数据、查一下环境和版本

按需求的实际大小来做：一个小问题就一两步，别硬凑成一份长报告；一个大任务才拆成多步。

## 怎么用 Notebook（最重要的部分）

人用 Notebook 不是把代码一次写完再跑，而是**写一块、跑一块、看结果、再写下一块**。你也要这样：
每次只调一次 add_step，写一小段说明加一个 code 单元格，等真实输出回来，读完再决定下一步。
add_step 会自动执行刚写的单元格并把输出给你，所以你永远是「看着上一步的结果」在写下一步。

一步应该有多小：一步只干一件能单独看结果的事。比如
「读数据 → 看形状和前几行」是一步，「清洗缺失值 → 看还剩多少」是下一步，
「画图」再一步。不要在一步里既加载数据又训练又画图。
代码超过 25 行左右就说明这一步太大了，拆开：比如「定义模型 → 打印结构和参数量」是一步，
「训练 → 看 loss 下降」是下一步，「在测试集上评估 → 看准确率」再一步。

上一步的输出会影响下一步：形状不对就先查，值不合预期就先打印出来看，报错就先修。
不要假装没看见输出，按原计划往下写。

## markdown 单元格写什么

markdown 和代码一样是产物的一部分，不是过程播报。它是这个 notebook 的文档：
标题撑起结构，正文写给之后打开这个 .ipynb 的人看。

- 第一步的 markdown 用 `#` 写 notebook 标题，再用一两句交代这份 notebook 要解决什么问题、思路是什么、数据从哪来。
- 之后每一步用 `##` 写小节标题，名词性的、连起来能当目录读，例如
  `## 加载 MNIST 数据集`、`## 定义卷积网络`、`## 训练一个 epoch`、`## 在测试集上评估`。
- 小节正文讲方法和取舍：这节用什么做法、为什么选它、关键参数什么含义、结果该怎么读。
  需要就写公式、列表、表格，该多写就多写，该一句就一句。
- 关键结果出来之后（准确率、耗时、样本量这类），用 replace_cell 把真实数字补进对应小节或小结里。

markdown 里不要出现这些：
- 行动播报：「接下来我要…」「我们先看看…」「这一步确认一下…」「让我…」
- 对自己的交代：「要从输出里看什么」「如果报错我再改」
- 编号废话：`## 步骤 3` 这种没有信息量的标题
- 调试痕迹：报错和修复过程不写进 notebook，直接改 code 单元格就行

判断标准：把对话记录全部删掉，只把这个 .ipynb 发给别人，他能顺着标题读懂做了什么、结论是什么。
你自己的过程性说明（我在做什么、遇到什么错、怎么修）说在对话回复里，不写进单元格。

硬性规则：
1. Notebook 是唯一真相源。markdown 单元格是文档，code 单元格是代码，两者都是交付物；不要只在对话里贴大段代码。
2. 用 add_step 加新步骤，一轮只加一步。不要一次插好几个单元格再统一跑。
3. 需要第三方库时，单独一步做安装（优先 `%pip install -q 包名`），并写成已安装则很快结束。
4. 报错就用 replace_cell 改源码再 run_cell 重跑，不要空谈，也不要跳过去写下一步。
5. 只能基于真实输出下结论。没跑出来就别说「已经训练完成 / 准确率是多少 / 文件已经转换好了」。
6. 讲解默认中文；代码标识符用英文。
7. 内核的工作目录就是项目的 `notebooks/`，相对路径都从这里算。要读写用户的本地文件时，先确认路径是否存在再动手。
8. 只做用户要求的事。删除、覆盖、移动本机文件，或执行 shell 命令去改环境之前，先用 ask_user 问清楚；不要自作主张清理目录。
9. 涉及训练之类的重活时，默认用 PyTorch，并用很少的 epoch / 小 subset，保证在 CPU 上也能较快跑完；用户明确要更大规模再放开。
10. 已有 Notebook 内容时，优先追加或修改，不要无故清空整本。若只有一个空 code 单元格，可以 replace 它。
11. 信息不够时用 ask_user，不要瞎猜关键路径、字段名或业务口径。
12. 长任务要有 print 进度。matplotlib 已经是 inline 后端，直接 `plt.show()` 即可出图；内核启动时已经按本机可用字体配好中文显示，不要再自己设 `font.sans-serif`（写死 SimHei 之类会在 macOS 上刷一屏找不到字体的告警）。
13. 每一步的代码都要有可看的输出：print 关键值、显示 DataFrame、画图，别写完一块什么都不显示。
14. 全部做完后，用 add_step 加一个只有 markdown 的 `## 小结`，写清做了什么、真实结果是多少、有什么结论或局限，然后停止调用工具。
"""

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "read_notebook",
            "description": "读取当前 Notebook 的单元格源码、类型和最近输出摘要。",
            "parameters": {
                "type": "object",
                "properties": {
                    "cell_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "可选。不传则返回全部单元格。",
                    }
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "add_step",
            "description": (
                "往 Notebook 里加一步：先一个 markdown 说明单元格，再一个 code 单元格，"
                "然后立刻执行这个 code 单元格，把真实输出返回给你。"
                "一次只能加一步，一步只能有一个 code 单元格——"
                "必须先看到这一步的输出，才能决定下一步写什么。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "markdown": {
                        "type": "string",
                        "description": (
                            "这一节的文档，是产物的一部分：`##` 小节标题 + 面向读者的方法说明。"
                            "第一步用 `#` 写 notebook 标题。"
                            "不要写「接下来我要…」这类过程播报。"
                        ),
                    },
                    "code": {
                        "type": "string",
                        "description": "这一步的代码，控制在一个单元格能读完的量。纯讲解的步骤可以不给。",
                    },
                    "after_cell_id": {
                        "type": "string",
                        "description": "可选。插到该单元格之后；默认追加到末尾。",
                    },
                },
                "required": ["markdown"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "replace_cell",
            "description": "替换已有单元格的源码，可选改类型。",
            "parameters": {
                "type": "object",
                "properties": {
                    "cell_id": {"type": "string"},
                    "source": {"type": "string"},
                    "cell_type": {"type": "string", "enum": ["markdown", "code"]},
                },
                "required": ["cell_id", "source"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "delete_cells",
            "description": "删除单元格。",
            "parameters": {
                "type": "object",
                "properties": {"cell_ids": {"type": "array", "items": {"type": "string"}}},
                "required": ["cell_ids"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_cell",
            "description": "在 Python 内核中执行一个 code 单元格，返回完整输出/报错。",
            "parameters": {
                "type": "object",
                "properties": {"cell_id": {"type": "string"}},
                "required": ["cell_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "ask_user",
            "description": "向用户提一个必须先回答的问题并暂停本轮。",
            "parameters": {
                "type": "object",
                "properties": {"question": {"type": "string"}},
                "required": ["question"],
            },
        },
    },
]


class AgentService:
    """The agent for one notebook: its own document, kernel, chat and run state.

    Nothing here reads "which notebook is on screen", so a run keeps going into
    its own file while the user reads or edits another one.
    """

    def __init__(self, doc: NotebookDoc, kernels: KernelPool, bus: EventBus, notebook: str) -> None:
        self.doc = doc
        self.kernels = kernels
        self.bus = bus
        self.notebook = notebook
        self.history: list[dict] = []
        self.running = False
        self.stop_requested = False
        self.phase = "idle"
        self._settings_provider = None

    def bind_settings(self, provider) -> None:
        self._settings_provider = provider

    def settings(self) -> AppSettings:
        return self._settings_provider() if self._settings_provider else AppSettings()

    async def kernel(self) -> KernelService:
        """This notebook's kernel, booted on demand."""
        return await self.kernels.get(self.notebook)

    def kernel_status(self) -> str:
        return self.kernels.status(self.notebook)

    def _trim_history(self) -> None:
        """Keep only the last MAX_QA_ROUNDS question/answer pairs."""
        starts = [i for i, m in enumerate(self.history) if m.get("role") == "user"]
        if len(starts) > MAX_QA_ROUNDS:
            del self.history[: starts[-MAX_QA_ROUNDS]]

    async def stop(self) -> None:
        self.stop_requested = True
        live = self.kernels.peek(self.notebook)
        if live:
            live.interrupt()
        await self._set_phase("stopping", "正在停止…")

    def reset_chat(self) -> str:
        self.history.clear()
        return self.notebook

    async def _publish(self, event: dict) -> None:
        """Stamp chat events with their notebook so other panels can ignore them."""
        event.setdefault("notebook", self.notebook)
        await self.bus.publish(event)

    async def run(self, user_message: str) -> None:
        if self.running:
            raise RuntimeError("agent already running")
        settings = self.settings()
        if not settings.api_key:
            await self._publish(
                {
                    "type": "agent_message",
                    "role": "assistant",
                    "content": "还没有配置模型。请复制项目根目录的 .env.example 为 .env，填入 AIJUPYTER_API_BASE / AIJUPYTER_API_KEY / AIJUPYTER_MODEL 后重启后端；或打开独立配置页 http://127.0.0.1:5173/#config。",
                }
            )
            return

        self.running = True
        self.stop_requested = False
        self.history.append({"role": "user", "content": user_message})
        self._trim_history()
        await self._publish({"type": "agent_message", "role": "user", "content": user_message})

        messages: list[dict] = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "system",
                "content": self._runtime_context(settings),
            },
            *self.history,
        ]

        repair_used = 0
        max_steps = 40
        final_text = ""
        streamed_text = ""

        try:
            await self._set_phase("planning", "正在规划…")
            for _ in range(max_steps):
                if self.stop_requested:
                    final_text = "已停止。"
                    break
                await self._set_phase("thinking", "正在调用模型…")
                message = await self._chat(settings, messages)
                tool_calls = message.get("tool_calls") or []
                content = (message.get("content") or "").strip()
                # Turn the live streaming bubble into a permanent message, keeping
                # the round's thinking with it so it can be folded away, not lost.
                await self._publish(
                    {
                        "type": "agent_round_end",
                        "content": content,
                        "reasoning": (message.get("reasoning") or "").strip(),
                    }
                )
                if content:
                    streamed_text = content
                if content and not tool_calls:
                    final_text = content
                    break
                if not tool_calls:
                    if content:
                        final_text = content
                    break

                messages.append(
                    {
                        "role": "assistant",
                        "content": message.get("content") or "",
                        "tool_calls": tool_calls,
                    }
                )

                stop_after_ask = False
                steps_this_round = 0
                for call in tool_calls:
                    if self.stop_requested:
                        break
                    fn = call.get("function") or {}
                    name = fn.get("name") or ""
                    raw_args = fn.get("arguments") or "{}"
                    try:
                        args = json.loads(raw_args) if raw_args else {}
                    except json.JSONDecodeError:
                        args = {}

                    # One step per round, so the next step is written against a
                    # result the model has actually seen.
                    if name == "add_step":
                        steps_this_round += 1
                        if steps_this_round > 1:
                            messages.append(
                                {
                                    "role": "tool",
                                    "tool_call_id": call.get("id"),
                                    "content": (
                                        "这一步没有写入：一次只能加一步。"
                                        "先读上一步的输出，再决定下一步写什么。"
                                    ),
                                }
                            )
                            continue

                    await self._set_phase(self._phase_for(name), f"工具：{name}")
                    result, failed_run = await self._dispatch(name, args, settings)
                    if failed_run:
                        repair_used += 1
                        if repair_used > settings.max_repair_rounds:
                            result += (
                                f"\n已达到最大自动修复轮数（{settings.max_repair_rounds}）。"
                                "请停止自动修复，把问题告诉用户，由用户接手。"
                            )
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": call.get("id"),
                            "content": result,
                        }
                    )
                    if name == "ask_user":
                        stop_after_ask = True
                        final_text = args.get("question") or content or "需要你补充一点信息。"
                if stop_after_ask:
                    break
            else:
                final_text = final_text or "本轮步骤已达上限，先停在这里。你可以继续追问。"
        except Exception as exc:
            final_text = f"Agent 出错：{exc}"
            traceback.print_exc()
        finally:
            await self._publish({"type": "agent_round_end", "content": ""})
            if final_text:
                self.history.append({"role": "assistant", "content": final_text})
                # Skip the echo when the text already reached the UI as deltas.
                if final_text != streamed_text:
                    await self._publish(
                        {"type": "agent_message", "role": "assistant", "content": final_text}
                    )
            self.running = False
            self.stop_requested = False
            # This notebook may not be the one on screen, so nothing else will
            # checkpoint it. Persist the work as soon as the run is over.
            try:
                self.doc.save()
                await self._broadcast_notebook()
            except Exception:
                pass
            await self._set_phase("idle", "")

    def _runtime_context(self, settings: AppSettings) -> str:
        snap = self.doc.snapshot()
        auto = "开启：写入 code 后必须 run_cell" if settings.auto_execute else "关闭：只写单元格，不要调用 run_cell"
        lines = [
            f"自动执行：{auto}",
            f"最大自动修复轮数：{settings.max_repair_rounds}",
            f"内核状态：{self.kernel_status()}",
            f"当前文件：{snap.get('name')}",
            f"单元格数量：{len(snap.get('cells') or [])}",
            "当前 Notebook 摘要：",
            self._notebook_brief(snap),
        ]
        return "\n".join(lines)

    def _notebook_brief(self, snap: dict) -> str:
        rows = []
        for i, cell in enumerate(snap.get("cells") or []):
            src = (cell.get("source") or "").strip().replace("\n", " ")
            if len(src) > 120:
                src = src[:120] + "…"
            rows.append(f"{i}. {cell['id']} [{cell['cell_type']}] {src or '(empty)'}")
        return "\n".join(rows) or "(empty notebook)"

    async def _set_phase(self, phase: str, detail: str) -> None:
        self.phase = phase
        await self._publish({"type": "agent_status", "phase": phase, "detail": detail})

    def _phase_for(self, name: str) -> str:
        return {
            "add_step": "writing",
            "replace_cell": "repair",
            "delete_cells": "writing",
            "run_cell": "executing",
            "read_notebook": "reading",
            "ask_user": "waiting",
        }.get(name, "thinking")

    async def _chat(self, settings: AppSettings, messages: list[dict]) -> dict:
        """Stream one model round, pushing text to the UI as it arrives.

        Returns the assembled message (content + tool_calls) so the caller sees
        the same shape a non-streaming call would produce.
        """
        url = settings.api_base.rstrip("/") + "/chat/completions"
        headers = {
            "Authorization": f"Bearer {settings.api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": settings.model,
            "messages": messages,
            "tools": TOOLS,
            "tool_choice": "auto",
            "temperature": 0.2,
            "stream": True,
        }

        content_parts: list[str] = []
        reasoning_parts: list[str] = []
        tool_calls: dict[int, dict] = {}
        pending: dict[str, list[str]] = {"content": [], "reasoning": []}
        last_flush = time.monotonic()

        async def flush(force: bool = False) -> None:
            nonlocal last_flush
            if not force and time.monotonic() - last_flush < DELTA_FLUSH_SECONDS:
                return
            last_flush = time.monotonic()
            for kind in ("reasoning", "content"):
                text = "".join(pending[kind])
                if not text:
                    continue
                pending[kind].clear()
                await self._publish({"type": "agent_delta", "kind": kind, "text": text})

        async with httpx.AsyncClient(timeout=STREAM_TIMEOUT) as client:
            async with client.stream("POST", url, headers=headers, json=payload) as resp:
                if resp.status_code >= 400:
                    body = (await resp.aread()).decode("utf-8", "replace")
                    raise RuntimeError(f"LLM HTTP {resp.status_code}: {body[:800]}")
                async for line in resp.aiter_lines():
                    if self.stop_requested:
                        break
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if not data or data == "[DONE]":
                        continue
                    try:
                        chunk = json.loads(data)
                    except json.JSONDecodeError:
                        continue
                    choice = (chunk.get("choices") or [{}])[0]
                    delta = choice.get("delta") or {}

                    reasoning = delta.get("reasoning_content")
                    if reasoning:
                        reasoning_parts.append(reasoning)
                        pending["reasoning"].append(reasoning)
                    piece = delta.get("content")
                    if piece:
                        content_parts.append(piece)
                        pending["content"].append(piece)
                    for call in delta.get("tool_calls") or []:
                        idx = call.get("index", 0)
                        slot = tool_calls.setdefault(
                            idx, {"id": "", "type": "function", "function": {"name": "", "arguments": ""}}
                        )
                        if call.get("id"):
                            slot["id"] = call["id"]
                        fn = call.get("function") or {}
                        if fn.get("name"):
                            slot["function"]["name"] = fn["name"]
                        if fn.get("arguments"):
                            slot["function"]["arguments"] += fn["arguments"]
                    await flush()

        await flush(force=True)
        return {
            "content": "".join(content_parts),
            "reasoning": "".join(reasoning_parts),
            "tool_calls": [tool_calls[i] for i in sorted(tool_calls)],
        }

    async def _dispatch(self, name: str, args: dict, settings: AppSettings) -> tuple[str, bool]:
        try:
            if name == "read_notebook":
                return await self._read_notebook(args.get("cell_ids")), False
            if name == "add_step":
                return await self._add_step(args, settings)
            if name == "replace_cell":
                return await self._replace_cell(args), False
            if name == "delete_cells":
                return await self._delete_cells(args), False
            if name == "run_cell":
                if not settings.auto_execute:
                    return "自动执行已关闭，没有真正运行。请告诉用户手动点运行。", False
                return await self._run_cell(args.get("cell_id") or "")
            if name == "ask_user":
                return f"已向用户提问：{args.get('question')}", False
            return f"未知工具：{name}", False
        except Exception as exc:
            return f"工具失败：{exc}", False

    async def _broadcast_notebook(self) -> None:
        await self._publish({"type": "notebook", "doc": self.doc.snapshot()})

    async def _read_notebook(self, cell_ids: list[str] | None) -> str:
        snap = self.doc.snapshot()
        cells = snap["cells"]
        if cell_ids:
            wanted = set(cell_ids)
            cells = [c for c in cells if c["id"] in wanted]
        payload = []
        for i, cell in enumerate(cells):
            item = {
                "index": self.doc.cell_index(cell["id"]),
                "id": cell["id"],
                "cell_type": cell["cell_type"],
                "source": cell.get("source") or "",
                "execution_count": cell.get("execution_count"),
                "status": cell.get("status"),
            }
            if cell["cell_type"] == "code":
                item["outputs_summary"] = summarize_outputs(cell.get("outputs") or [])
            payload.append(item)
        return json.dumps(payload, ensure_ascii=False)

    async def _add_step(self, args: dict, settings: AppSettings) -> tuple[str, bool]:
        """Write one explain-then-code step and immediately run it.

        Fusing the write and the run is what keeps the notebook cadence: the
        model cannot compose the next step without having read this one's output.
        """
        markdown = (args.get("markdown") or "").strip()
        code = (args.get("code") or "").strip()
        # Every step gets an explanation, or the notebook turns into a code dump.
        if not markdown:
            return (
                "这一步没有写入：add_step 必须带 markdown 说明。"
                "用一两句写清这一步要做什么、为什么、要从输出里看什么，然后重试。"
            ), False

        index = None
        after = args.get("after_cell_id")
        if after:
            pos = self.doc.cell_index(after)
            index = len(self.doc.payload["cells"]) if pos < 0 else pos + 1

        # A brand-new notebook is one blank code cell. Drop it *after* inserting,
        # since the store backfills a blank cell whenever it would go empty.
        cells = self.doc.payload["cells"]
        stale_blank = ""
        if len(cells) == 1 and cells[0]["cell_type"] == "code" and not (cells[0].get("source") or "").strip():
            stale_blank = cells[0]["id"]

        created = []
        code_id = ""
        for cell_type, source in (("markdown", markdown), ("code", code)):
            if not source:
                continue
            cell = self.doc.insert_cell(index, cell_type, source, metadata={"ai_generated": True})
            if index is not None:
                index += 1
            created.append(f"{cell['id']}[{cell_type}]")
            if cell_type == "code":
                code_id = cell["id"]
        if stale_blank:
            self.doc.delete_cell(stale_blank)
        await self._broadcast_notebook()

        head = "已写入 " + " ".join(created)
        if not code_id:
            return f"{head}。这一步只有说明，没有代码要跑。", False
        if not settings.auto_execute:
            return f"{head}。自动执行已关闭，没有运行，请让用户手动点运行。", False

        await self._set_phase("executing", "运行刚写入的单元格")
        result, failed = await self._run_cell(code_id)
        return f"{head}，并已执行：\n{result}", failed

    async def _replace_cell(self, args: dict) -> str:
        cell = self.doc.replace_cell(args.get("cell_id"), args.get("source"), args.get("cell_type"))
        if not cell:
            return "cell not found"
        await self._broadcast_notebook()
        return json.dumps({"id": cell["id"], "cell_type": cell["cell_type"]}, ensure_ascii=False)

    async def _delete_cells(self, args: dict) -> str:
        ids = args.get("cell_ids") or []
        deleted = [cid for cid in ids if self.doc.delete_cell(cid)]
        await self._broadcast_notebook()
        return json.dumps({"deleted": deleted}, ensure_ascii=False)

    async def _run_cell(self, cell_id: str) -> tuple[str, bool]:
        cell = self.doc.get_cell(cell_id)
        if not cell:
            return "cell not found", False
        if cell["cell_type"] != "code":
            return "not a code cell", False
        self.doc.set_status(cell_id, "running")
        kernel = await self.kernel()
        result = await kernel.enqueue(cell_id, cell.get("source") or "")
        status = "ok" if result.ok else "error"
        self.doc.set_outputs(cell_id, result.outputs, result.execution_count, status)
        await self._broadcast_notebook()
        summary = summarize_outputs(result.outputs)
        text = (
            f"cell_id={cell_id}\nstatus={'ok' if result.ok else 'error'}\n"
            f"execution_count={result.execution_count}\nelapsed_ms={result.elapsed_ms}\n"
            f"kernel={kernel.status}\noutputs:\n{summary}"
        )
        return text, (not result.ok)


class AgentPool:
    """One AgentService per notebook, so several can run at the same time."""

    def __init__(self, store: NotebookStore, kernels: KernelPool, bus: EventBus) -> None:
        self.store = store
        self.kernels = kernels
        self.bus = bus
        self.agents: dict[str, AgentService] = {}
        self._settings_provider = None

    def bind_settings(self, provider) -> None:
        self._settings_provider = provider
        for agent in self.agents.values():
            agent.bind_settings(provider)

    def get(self, notebook: str | None = None) -> AgentService:
        """The agent for a notebook, created on first use."""
        name = notebook or self.store.active
        agent = self.agents.get(name)
        if agent is None:
            agent = AgentService(self.store.doc(name), self.kernels, self.bus, name)
            agent.bind_settings(self._settings_provider)
            self.agents[name] = agent
        elif self.store.is_open(name):
            # `open` may have rebuilt the document object for this name. Never
            # repoint an agent at a fallback document.
            agent.doc = self.store.doc(name)
        return agent

    def peek(self, notebook: str) -> AgentService | None:
        return self.agents.get(notebook)

    def running_notebooks(self) -> list[str]:
        return [name for name, agent in self.agents.items() if agent.running]

    def is_running(self, notebook: str) -> bool:
        agent = self.agents.get(notebook)
        return bool(agent and agent.running)

    def rename(self, old: str, new: str) -> None:
        """Follow the chat and run state when a notebook is saved under a new name."""
        if not old or not new or old == new:
            return
        agent = self.agents.pop(old, None)
        if agent is None:
            return
        agent.notebook = new
        agent.doc = self.store.doc(new)
        self.agents[new] = agent

    def forget(self, notebook: str) -> None:
        self.agents.pop(notebook, None)
