# 年报问答 RAG V1.3

面向单公司、多财年的年报问答项目。用中文提出复合财务问题，系统拆分检索需求，从年报中召回证据，再生成带来源引用的回答。

基于 **BGE-M3 + Qdrant Local + BM25 + DeepSeek + FastAPI**。当前版本为 `v1.3.0`，提供本地网页和 HTTP API。

[快速开始](#快速开始) · [问题示例](#问题示例) · [测试与边界](#测试与边界) · [更新说明](docs/V1.3更新说明.md) · [模块完成度](docs/V1.3模块完成度.md)

## 能做什么

- 查询同一家公司的连续年度收入、利润、现金流与报告分部数据。
- 将复合问题拆成多个检索任务，同时保留原问题召回；结合向量语义检索与 BM25 精确匹配。
- 对候选证据做 RRF 融合、去重和条件重排，补充跨块表格及可比年度上下文。
- 生成数据表、计算和综合分析，并通过引用编号查看证据原文、文档页码或工作表范围。
- 记录查询规划、排名、耗时和数值诊断，用于回放和定位问题。

当前以单用户本地使用为主，一次查询处理一家公司的材料。财务计算和分析仍需核对原文。

## 界面

![年报问答主界面](docs/screenshots/前端真实测试-主界面-2026-08-17.png)

![检索过程与证据界面](docs/screenshots/前端真实测试-线索对齐-2026-08-15.png)

以上为仓库已有界面截图，分别记录于 2026-08-17 和 2026-08-15；V1.3 的检索与回答结果见下方测试记录。

## 快速开始

需要 Python 3.13、Node.js、Git、DeepSeek API Key，以及与现有向量库匹配的 BGE-M3 ONNX 权重。以下命令在 Windows PowerShell 中执行。

### 1. 获取代码并安装依赖

```powershell
git clone --branch codex/v1.3.0 https://github.com/unkownpeople/wd_rag_v1.git
cd wd_rag_v1
py -3.13 -m venv .venv_rag
.\.venv_rag\Scripts\python.exe -m pip install -r requirements-rag.txt
Copy-Item .env.example .env
```

已有项目升级时保留自己的 `.env`，对照 [.env.example](.env.example) 更新配置。

### 2. 配置模型

在项目根目录 `.env` 中填写 `DEEPSEEK_API_KEY`。也可分别配置 `DEEPSEEK_PLANNER_API_KEY` 和 `DEEPSEEK_ANSWER_API_KEY`。Planner 与 Answer 默认使用 `deepseek-v4-flash`，thinking disabled。

将 BGE-M3 ONNX int8 权重放到：

```text
models/bge-m3-embedding/model_quantized.onnx
```

权重超过 GitHub 普通 Git 的单文件限制，需要另行准备；Tokenizer 已包含在仓库中。模型规格与 SHA-256 见 [模型说明](models/README.md)。目前尚未提供一键权重下载，因此只有克隆仓库还不能启动完整问答。

复合题的模板预算为 `RETRIEVAL_MAX_CONTEXT_CHARS=64000`、`LLM_MAX_TOKENS=8000`、`PLANNER_MAX_TOKENS=4000`。这些是上限，不是每次请求的固定用量。

### 3. 启动服务

终端一，启动后端：

```powershell
.\.venv_rag\Scripts\python.exe -m uvicorn rag_api.app:app --host 127.0.0.1 --port 8001
```

终端二，在项目根目录启动前端：

```powershell
$env:RAG_API_ORIGIN='http://127.0.0.1:8001'
node frontend/server.mjs
```

打开 [问答页面](http://127.0.0.1:3000/)。[健康检查](http://127.0.0.1:8001/health) 返回服务及配置状态，[API 文档](http://127.0.0.1:8001/docs) 提供请求参数说明。

Qdrant Local 由单个后端进程持有；重建向量库前先停止后端。规划、条件重排和回答会调用远程模型，相关问题及召回片段可能发送至 DeepSeek，并产生 API 费用。

## 问题示例

可以从这道跨年度复合题开始：

> 用微软 FY2021—FY2023 年报评价三个报告分部的增长质量。列出各年收入和营业利润，计算收入两年 CAGR、营业利润率，以及 FY2022→FY2023 对公司增量的贡献，并将分部合计与公司总额勾稽。核查服务器和网络设备使用寿命变更对公司营业利润的影响，给出扣除该影响后的敏感性结果；最后解释为何 Intelligent Cloud 收入增长最快，不代表盈利效率改善最大，以及 Azure 增速为何不能替代整个分部增速。

完整题目和判据见 [复合问题压力测试](docs/V2复合问题压力测试.md)，已有输出见 [微软三年分析实测](data/evaluation/compound_live_2.md)。

## 数据与处理链路

| 公司 / 材料 | 独立报告年份 | 格式 |
| --- | --- | --- |
| Apple | 2022—2024 | 3 份 PDF |
| Microsoft | 2021—2023 | 3 份 PDF |
| TCS | FY2021—FY2024 | 4 份 PDF |
| TCS 分析材料 | FY2022—FY2024 | 1 份 DOCX、1 份 XLSX |

共 12 个来源、6266 个切块及向量点，活动集合为 `annual_report_v1_single_company`。来源 URL、文件与哈希见 [数据来源](data/SOURCES.md)。单份年报内的年度对比列与独立报告年份分别记录。

```text
入库：PDF / DOCX / XLSX -> 解析清洗 -> 结构化切块 -> BGE-M3 向量化 -> Qdrant
查询：用户问题 -> Planner 拆分 + 原问题
               -> Dense / BM25 并行召回 -> RRF 融合 -> 去重与条件重排
               -> Evidence 上下文 -> 单次回答 -> 引用校验 -> 答案与来源
```

公司、文档等范围过滤应用于两路召回。数值检查以诊断方式记录，不自动改写回答。配置定义见 [settings.py](rag_api/settings.py)。

## 测试与边界

| 验证内容 | 已记录结果 | 解释 |
| --- | --- | --- |
| 离线回归 | 115 项通过，其中核心模块 98 项 | 行为与回归检查，不等于模型回答准确率 |
| 实际 API 检索 | 12 题各运行两次，24/24 Hit@5；MRR@10=0.833 | 本地代理标签、小样本重复测试 |
| 复合检索 | Apple 15 项、Microsoft 18 项原始输入均召回 | 同时检查税费、财年周数、使用寿命变更等证据 |
| 微软复合回答 | 52.64 秒，18 项分部原始值及各分析章节保留 | 仍存在 CAGR 小数计算偏差与表述问题 |

复现离线测试：

```powershell
.\.venv_rag\Scripts\python.exe -m unittest discover
```

检索结果见 [API 测试记录](data/evaluation/hybrid_api_acceptance.json) 和 [复合检索记录](data/evaluation/compound_retrieval_acceptance.json)。历史题集见 [V1 问题案例与测试结果边界](docs/V1问题案例与测试结果边界.md)。

当前主要边界：

- 文档重建已有旧块清理与数量检查，尚缺原子切换、失败回滚和完整解析器版本治理。
- 重排使用 LLM 相关性排序；独立人工标注、触发收益与更广语料验证尚不充分。
- 引用有效只说明引用编号和元数据检查通过，不保证逐项事实、计算或因果推理正确；误拒、漏拒仍需系统评测。
- 当前提供本地单用户流程，不包含多租户鉴权和外部知识库同步。

逐项依据、剩余工作和统计口径见 [模块完成度](docs/V1.3模块完成度.md)。

## 项目结构

```text
parsers/      PDF、DOCX、XLSX 解析
cleaners/     清洗与统一中间数据
chunking/     结构分块、表格处理、质量检查
embeddings/   BGE-M3、Qdrant、BM25、RRF
rag_api/      FastAPI、规划、重排、回答及诊断
prompts/      模型提示词
frontend/     本地问答页面与 Node 代理
scripts/      入库、重建和评测脚本
data/         来源、切块、题集和结果
xianlian/     Qdrant Local 数据库
docs/         使用、版本和验收文档
```

`v1` 保存更新前版本，`codex/v1.3.0` 为当前更新分支，`main` 保持原有默认分支。版本变化见 [更新说明](docs/V1.3更新说明.md)。

## 许可

项目代码采用 [MIT License](LICENSE)。年报、分析材料、模型及 Tokenizer 的权利归各自权利人，详见 [第三方说明](THIRD_PARTY_NOTICES.md)。
