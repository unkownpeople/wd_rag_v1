# GitHub 上传审核信息

审核日期：2026-08-17

本文件用于创建远端仓库前审核。当前本地仓库尚无提交、尚未配置远端，也未执行任何推送。

## 发布结论

- 本次确定首次创建为 `Private`：当前内容可以直接上传。
- 若直接创建为 `Public`：应先确认 7 份活动来源、2 份补充原件及其派生 chunks、Qdrant 数据库的再分发权限。
- 无法确认第三方内容再分发权限时，不应公开提交原始文件、派生 chunks、包含完整 Evidence 的评测报告、向量数据库和未核验许可的 Tokenizer；项目 MIT License 只覆盖自行编写的代码和文档。
- 当前 `.gitignore` 是私有完整包边界，不是公开瘦身边界；Public 仓库不得直接对本目录执行 `git add --all`。

## GitHub 仓库创建信息

| 项目 | 建议值 |
| --- | --- |
| Repository name | `annual-report-rag-v1` |
| Description | `Evidence-first annual report RAG for single-company Q&A using BGE-M3, Qdrant, hybrid retrieval, DeepSeek planning, and citation validation.` |
| Visibility | `Private` |
| Initialize this repository with a README | 不勾选 |
| Add .gitignore | 不选择 |
| Choose a license | 不选择 |
| Default branch | `main` |

建议 Topics：

```text
rag
retrieval-augmented-generation
qdrant
bge-m3
fastapi
deepseek
hybrid-search
annual-report
document-ai
financial-analysis
```

本地已经包含 `README.md`、`.gitignore` 和 `LICENSE`。远端仓库应创建为空仓库，避免首次推送产生无关合并提交。

## 初始版本信息

| 项目 | 建议值 |
| --- | --- |
| Initial commit | `Initial release: annual report RAG v1` |
| Tag | `v1.0.0` |
| Release title | `V1.0.0 - Single-company annual report Q&A` |

Release 摘要：

```text
Single-company annual report Q&A for Apple, Microsoft, and TCS, with PDF/DOCX/XLSX ingestion, BGE-M3 embeddings, Qdrant Local, BM25 + Dense Hybrid/RRF retrieval, DeepSeek planning and answer generation, citation validation, fixed evaluation sets, and an evidence-first frontend.

Scope: one company per query. Cross-company comparison, arbitrary-company generalization, production deployment, and access control are outside V1.
```

## 本次上传内容

- 年报问答 RAG 的解析、清洗、切块、向量化、检索、FastAPI 和前端源码。
- Apple、Microsoft、TCS 共 7 份实际入库源文件，以及 Apple 2022、Apple 2023 两份补充原始 PDF。
- 3959 个活动 chunks 和 `annual_report_v1_single_company` Qdrant Local 数据库。
- V1.2 固定 21 题、中文别名 6 题及最终验收报告。
- 39 道前端手工提问问题合集和真实前端测试记录。
- 统一的问题案例与测试结果边界记录。
- MIT License、第三方内容声明、数据来源说明和无需 DeepSeek Key 的离线 CI。

当前 Git 忽略规则生效后的发布候选：197 个文件，合计 147,107,428 字节；最大文件为 61,026,304 字节的 `storage.sqlite`。

## 上传前核对结果

- CodeGraph 已在 `F:\新建文件夹\Rag_v1` 初始化。
- V1.2 契约成功加载 `21` 道题。
- 单元测试：`63/63` 通过。
- `frontend/app.js` 和 `frontend/server.mjs` Node 语法检查通过。
- 可提交文本的密钥扫描：`0` 个命中。
- `.env`、ONNX 权重、`.codegraph` 和 Qdrant 运行时文件忽略规则通过。
- 2026-08-14/15 的冻结记录中，后端 `/health` 返回 `ok`、活动集合为 `annual_report_v1_single_company`，真实前端回归通过；2026-08-17 本轮只读探测时 8001/3000 均未启动，本轮没有为发布审核启动服务。
- 本轮没有提交真实问题、没有重新运行 21 题或 6 题完整问答评测，也没有调用 DeepSeek；README 中的完整链路成绩来自 2026-08-14 冻结报告。
- 中文别名完整链路为 `6/6`，但绕过 Planner 的 retrieval-only coverage 为 `2/6`，已作为公开边界记录。

## 不上传内容

- `.env` 和任何真实 DeepSeek Key。
- `.venv_rag/`、缓存、日志、临时文件和 `.codegraph/`。
- `models/bge-m3-embedding/model_quantized.onnx`：569,694,530 字节，超过 GitHub 普通 Git 的 100 MiB 单文件限制。
- Qdrant 锁、WAL 等运行时文件。
- 旧集合、旧数据库、早期学习数据和失败评测产物。

## 大文件与第三方权利提醒

- 最大可提交文件为 `xianlian/collection/annual_report_v1_single_company/storage.sqlite`，61,026,304 字节。
- 该文件低于 GitHub 普通 Git 的 100 MiB 硬限制，但超过 50 MiB 建议阈值，推送时可能收到警告。
- 应使用本地 Git 命令推送，不使用 GitHub 网页逐文件上传。
- `LICENSE` 不重新授权年报、研究报告、电子表格、Tokenizer、派生 chunks 或向量数据库，具体来源和边界见 `THIRD_PARTY_NOTICES.md` 与 `data/SOURCES.md`。
- 生成报告和 chunks 中保留的 `F:\\新建文件夹\\Rag` 是原始验收环境的审计字段，不是克隆后的运行依赖。

## 创建远端后的命令

先在 GitHub 创建空仓库，再在 `F:\新建文件夹\Rag_v1` 执行：

```powershell
git remote add origin <GitHub仓库地址>
git remote -v
git add --all
git status --short
git commit -m "Initial release: annual report RAG v1"
git push -u origin main
git tag -a v1.0.0 -m "V1.0.0 - Single-company annual report Q&A"
git push origin v1.0.0
```

推送前应再次确认 `git status --short` 中没有 `.env`、ONNX 权重、`.codegraph` 或 Qdrant 运行时文件。Release 可在 GitHub 的 `Releases` 页面基于 `v1.0.0` 创建，并使用本文件中的标题和摘要。

## 当前本地状态

- Branch：`main`。
- Commit：无。
- Remote：无。
- Commit / push / tag / release：均未执行，等待本文件审核和 GitHub 仓库地址。
