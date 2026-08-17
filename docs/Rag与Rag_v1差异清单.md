# Rag 与 Rag_v1 差异清单

对比日期：2026-08-17

对比目录：

- 工作目录：`F:\新建文件夹\Rag`
- GitHub 发布副本：`F:\新建文件夹\Rag_v1`

## 结论

用户关于“近期基本没有修改业务代码”的判断成立。发布整理开始时，两边纳入源码范围的文件各 126 个，其中 123 个完全一致；没有 Python、前端、提示词或核心 RAG 业务逻辑差异。

发布补充完成后，两边源码范围仍各 126 个，118 个完全一致，8 个文件不同。没有任何源码文件缺失或只存在于一侧。差异由发布配置、发布说明、构建脚本适配、前端证据区滚动优化和 Tokenizer 错误提示相对路径化组成。

## 源码范围

本次源码对比包含：

- `chunking/`
- `cleaners/`
- `embeddings/`
- `parsers/`
- `prompts/`
- `rag_api/`
- `scripts/`
- `frontend/`
- 根目录 `.env.example`、`.gitignore`、`README.md`、`requirements-rag.txt`

对比排除了 `__pycache__`、`.pyc`、`.env`、模型权重、CodeGraph 索引和其他本机运行产物。

## 补充前差异

| 指标 | 数量 |
| --- | ---: |
| `Rag` 文件 | 126 |
| `Rag_v1` 文件 | 126 |
| 完全一致 | 123 |
| 不同 | 3 |

补充前仅以下文件不同：

- `.env.example`：发布副本使用项目内相对默认路径，端口改为 8001，避免绑定原工作目录绝对路径。
- `.gitignore`：发布副本排除真实密钥、本地模型权重、CodeGraph 索引和 Qdrant 运行时文件。
- `README.md`：发布副本改为面向 GitHub 的项目介绍、运行方式和验收边界。

## 补充后差异

| 指标 | 数量 |
| --- | ---: |
| `Rag` 文件 | 126 |
| `Rag_v1` 文件 | 126 |
| 完全一致 | 118 |
| 不同 | 8 |

发布补充后的 8 个差异：

- `.env.example`：发布副本使用项目内相对默认路径，端口改为 8001，避免绑定原工作目录绝对路径。
- `.gitignore`：发布副本排除真实密钥、本地模型权重、CodeGraph 索引和 Qdrant 运行时文件。
- `README.md`：发布副本改为面向 GitHub 的项目介绍、运行方式、验收边界和审核文件入口。
- `scripts/build_v1_single_company_collection.py`：发布副本直接从 `v1_single_company_chunks` 构建或校验 `annual_report_v1_single_company`。默认复用当前 3959 points；显式使用 `--recreate` 时才从 3959 chunks 全量重新编码。该脚本不再读取未上传的 `tcs_real_multiformat_v2_chunks`、`multicompany_eval_v1_chunks` 或旧 Qdrant 集合。
- `frontend/app.js`：线索页证据/来源区域按最新前端交互要求同步布局状态。
- `frontend/index.html`：线索页证据原文与来源索引结构增加等高布局挂钩。
- `frontend/styles.css`：桌面与移动端证据原文、来源索引等高，长内容改为区域内部滚动，避免撑高页面。
- `chunking/tokenizer.py`：缺少 Tokenizer 时的错误提示改为项目相对命令，不再显示原工作目录绝对路径。

其中构建脚本适配会改变发布副本的重建入口；前端三文件只改变线索页呈现和滚动；Tokenizer 只改变错误提示。其他差异均为发布配置和说明。

## 关键产物核对

| 产物 | 结果 | SHA-256 |
| --- | --- | --- |
| `data/evaluation/test_set_v1_1.jsonl` | 两边一致 | `a0f68a0cf1e5d41f3a8cb34d11f64b96f4d10afb925227137d844437fa2e81b9` |
| `data/processed/annual_reports/v1_single_company_reports/embedding.manifest.json` | 两边一致 | `6607269135c4057fd39bac0cb5b42efd5ff98b860e6f97670877373745ae320a` |
| `xianlian/collection/annual_report_v1_single_company/storage.sqlite` | 两边一致 | `c33d796178f80980deb5b990d53425615735f8b977e5fec3cee6ead310686f73` |
| `models/bge-m3-embedding/model_quantized.onnx` | 两边一致，仅本机保留 | `0826f8c1ab9edf1801db86c61919d4d108e8bfc0b809ec823ad366882ff0b77d` |

另外核对结果：

- 7 份实际入库源文件全部与工作目录一致。
- `v1_single_company_chunks` 下 7 个 JSONL 全部与工作目录一致。
- 当前活动 Qdrant 数据库与工作目录逐字节一致。

## 发布副本新增材料

以下内容是 GitHub 发布配套，不属于从工作目录遗漏的业务代码：

- `LICENSE`
- `THIRD_PARTY_NOTICES.md`
- `.gitattributes`
- `.github/workflows/unit-tests.yml`
- `GitHub上传审核信息.md`
- `docs/前端手工提问问题合集.md`
- 本差异清单
- `data/evaluation/test_set_v1_1.jsonl`

`.env` 和 ONNX 权重已经复制到发布副本供本机启动，但由 `.gitignore` 排除，不能上传。

## 本轮验证边界

本轮只执行静态检查、V1.2 契约加载、单元测试、健康检查和页面打开检查。不提交真实问题，不重新运行 21 题固定集，不调用 DeepSeek。真实问答由维护者启动后在前端亲自完成。
