# Wello AI Jupyter

带 Agent 闭环的 Jupyter：在网页里用自然语言提需求，模型把说明和代码写入单元格，在本机 Python 内核里执行，再根据输出继续修改。同时保留手工 Notebook 能力。

> An AI agent notebook on a real Jupyter kernel: it writes one markdown + code cell, runs it,
> reads the actual output, then writes the next. Several notebooks run in parallel,
> each with its own kernel and chat context.

`examples/` 里是三份 Agent 真实跑出来的 notebook（pandas 入门、销售数据按月柱状图、sympy 解三次方程），
GitHub 上可以直接点开看它写成什么样。

## 启动

```bash
chmod +x scripts/dev.sh
./scripts/dev.sh
```

浏览器打开 [http://127.0.0.1:5173](http://127.0.0.1:5173)。

## 配置模型（不在 Notebook 页面里）

复制 `.env.example` 为 `.env`，三项都要填：

```
AIJUPYTER_API_BASE=https://api.deepseek.com
AIJUPYTER_API_KEY=sk-...
AIJUPYTER_MODEL=deepseek-v4-pro
```

对应 OpenAI SDK 里的 `base_url` / `api_key` / `model`，任何 OpenAI 兼容接口都行（OpenAI 用 `https://api.openai.com/v1`）。模型需要支持 function calling，Agent 靠它写单元格。

改完 `.env` 要重启后端。不想重启就打开独立配置页 [http://127.0.0.1:5173/#config](http://127.0.0.1:5173/#config)（底部状态栏的 ⚙ Model 也能进），保存即时生效。密钥不会写入 `.ipynb`，也不会出现在 Notebook 界面。

## 试一试

1. 在右侧对话里点示例，或输入：  
   `如何使用 MNIST 训练一个数字识别模型，用 PyTorch，只训练 1 个 epoch，解释用中文。`
2. 中间画布会出现 Markdown / Code 单元格并自动执行。
3. 报错时 Agent 会改代码再跑（有轮数上限）。
4. 你也可以自己改单元格，用 `Shift+Enter` 运行。

Agent 是**一步一步**做的，跟人用 Notebook 一样：写一节文档 + 一个 code 单元格，执行完读到真实输出，
才动手写下一步。它没法一次把所有单元格写完再统一跑——一轮只允许加一步，而且每步必须带 markdown，
这是后端强制的，不是靠提示词自觉。所以既能当讲解看，也能当调试过程看：哪一步的输出不对，就停在哪一步。

**markdown 是产物，不是过程播报。** `#` 是 notebook 标题，`##` 是各小节，连起来就是这份 notebook 的目录；
正文写方法和取舍，关键结果（准确率、耗时这类）跑出来之后会被回填进对应小节。
「我接下来要…」「报错了我来修」这类过程话说在右边的对话里，不进单元格——
把对话全删掉，只把 `.ipynb` 发给别人，他也能顺着标题读懂做了什么、结论是什么。

## 对话与上下文

- 模型输出是流式的，思考过程边想边显示；一轮结束后**折叠**成「思考过程 · N 字」收起来，点开还能看全文，不会消失。
- **每个 notebook 有自己的会话**，切文件就切上下文，另存改名会跟着走，删文件会一起清掉。
  一次运行会**绑定在发起它的那本**上：它的单元格、上下文、流式输出都只属于那一本，别的 notebook 的对话面板看不到。
- 只带**最近 5 轮** QA 进模型，久了不会越问越慢、越问越跑偏。
- 对话面板右上角的 ⌫ 清空当前 notebook 的上下文（只清对话，不动单元格）。

## 同时用几个 notebook

几本可以**一起开着、一起跑**。在 A 里提了需求，不用等它跑完：直接点侧栏切到 B，看它、改它、跑它，
甚至给 B 也提一个需求，两边各跑各的。切换是即时的，不会打断谁，也不会问你要不要停。

每本各有：
- **自己的内核**。变量不互串（A 里 `import pandas` 到 B 里不算数），一本在跑长任务不挡另一本执行。
  哪本在跑，侧栏那行就有个蓝点。想清空一本的变量，切过去点 Restart，只重启这一本。
- **自己的会话上下文**，和自己的流式输出。切走再切回来，对话还在原处接着长。
- **自己的文档**。切换只是换看哪一本，内存里的内容不重读磁盘，未保存的改动不会丢；Agent 写入的是它自己那本，
  跟你正在看哪一本无关。

内核是按需起的（一本第一次执行时才启动），最多常驻 6 个；超了就回收最久没用过的那本——
正在看的和正在跑的永远不会被回收。唯一不允许的操作是**删除正在跑的那本**，会提示先停止。

需求不限于数据科学——凡是适合在 Notebook 里做的都行：清洗和分析数据、画图、训练模型、数值与符号计算、
实现并验证一个算法、处理文本、批量转换文件、调接口抓数据、试某个库怎么用，或者只是算一笔账。
Agent 会按需求本身的大小来做，小问题就一两步，不会硬凑成一份长报告。

内核的工作目录是 `notebooks/`，代码里的相对路径都从这里算。涉及删除或覆盖本机文件时，Agent 会先反问确认。

手工 Jupyter：`+ Code` / `+ Markdown`、Run All、中断、重启内核、保存/导出 `.ipynb`。

## 界面

按 VS Code Notebook 排：顶部标签页 + 工具栏，左边文件列表，中间单元格，右边对话，底部状态栏。
左侧栏和对话面板都能关，关了之后从**底部状态栏**再点开（状态记在 localStorage，刷新后保持）。

左侧栏管理 `notebooks/` 目录：表头的 ⬆ 导入本机已有的 `.ipynb`（可多选，重名自动加 `-1`），＋ 新建，
每行悬停出现 ✕ 删除（会二次确认；删掉的若是当前打开的那本，会自动切到另一本）。

命令模式快捷键（焦点不在编辑器里时）：

| 键 | 作用 |
|---|---|
| `Shift+Enter` / `Cmd+Enter` | 运行并跳到下一格 / 原地运行 |
| `A` / `B` | 在上方 / 下方插入单元格 |
| `M` / `Y` | 转成 Markdown / Code |
| `D D` | 删除当前单元格 |
| `↑` `↓` / `K` `J` | 切换选中单元格 |

## 测试

```bash
python3 scripts/run_tests.py     # 后端与内核功能点
python3 scripts/test_files.py    # 导入 / 删除 .ipynb，含重名与路径穿越
python3 scripts/test_sessions.py # 每本独立上下文、5 轮窗口、清空与改名
python3 scripts/test_switch.py   # 运行中切走：事件不串本、单元格不写错地方、那本照常能用
python3 scripts/test_concurrent.py      # 两本各自的内核：变量不串、互不阻塞、单独重启、超量回收
python3 scripts/test_parallel_agents.py # 两个 Agent 同时跑，各写各的 notebook
python3 scripts/test_persist.py  # 切换 notebook 不丢未保存的编辑与输出
python3 scripts/stress_kernel.py # 连续执行 + 反复重启 + 重启打断长任务
node scripts/ui_test.mjs         # 浏览器里跑布局与交互、每本内核独立（需先启动 dev.sh）
node scripts/chat_ui_test.mjs    # 思考折叠、切换换上下文、运行中切走、两本同时跑（会真的调模型）
python3 scripts/seed_demo.py     # 造一份带图表和报错的演示 notebook
python3 scripts/cadence_probe.py "需求"  # 看 Agent 是否一步一跑、markdown 结构是否成型
python3 scripts/stream_probe.py "需求"   # 流式时间线 + 思考是否完整保留
```

`ui_test.mjs` 会在 1440 / 1280 / 1024 / 900 / 820 五个宽度下检查工具栏不折行、页面不出现横向溢出、面板不遮挡单元格区。

## 许可

AGPL-3.0，见 [LICENSE](LICENSE)。
