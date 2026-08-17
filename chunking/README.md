# chunking 模块

本目录只负责“清洗结果 → 检索块”，不负责解析、清洗、Embedding 或 Qdrant 写入。

默认 Tokenizer 是本地 `BAAI/bge-m3` fast Tokenizer，只加载 Tokenizer 文件，
不加载模型权重、不生成 Embedding、不写入 Qdrant。旧的 `heuristic-regex-v1`
只保留为注释和历史基线，不再参与默认切块。

## 文件职责

| 文件 | 职责 |
|---|---|
| `boundaries.py` | token、句子、段落和字符范围边界识别 |
| `tokenizer.py` | Tokenizer 适配契约、正则实现和可选 Hugging Face 实现 |
| `config.py` | 长度、重叠和表格行数参数 |
| `models.py` | 来源范围等内部数据模型 |
| `identity.py` | 来源 ID、块 ID和公共元数据转换 |
| `text.py` | 标题、段落、句子和超长正文切分逻辑 |
| `tables.py` | 小表整表、大表逻辑行组和 `raw_matrix` 保留 |
| `pipeline.py` | 按 `record_type` 分派正文/表格流程 |
| `io.py` | UTF-8 JSONL 输入输出 |
| `score.py` | 与清洗源内容的离线完整性比较 |
| `cli.py` | 批量执行和报告落盘 |
| `run_chunking.py` | 稳定的命令入口 |
| `chunker.py` | 旧导入路径的兼容门面，不承载核心逻辑 |

## 执行

在项目根目录运行：

```powershell
F:\python\Python313\python.exe -m chunking.run_chunking
```

输入：`data\processed\cleaned\*.cleaned.jsonl`

输出：`data\processed\chunks\`

当前 V1 已验收的切块单独保存在
`data\processed\annual_reports\v1_single_company_chunks\`，通用切块命令不会覆盖它。

解析和清洗仍分别由 `parsers\`、`cleaners\` 目录负责。切分阶段不会修改清洗源文件，也不会写入 `xianlian`。

## 边界和检索约定

- `min_tokens` 是软下限：当前块未达到它时，优先继续合并；超过 `max_tokens` 时仍强制落块。
- `sentence_overlap` 只允许出现在相邻块之间；同一块内部不能重复同一来源 Token。
- 表格的 `raw_matrix`、`row_matrix` 保留原始空白；`search_text` 只用于检索，并为续行补齐继承的 `Task`/`Input`。
- `score.json` 是清洗内容到切块的离线完整性评分，不是 Recall@K、MRR 或问答质量。

显式使用真实 Tokenizer 时，代码示例：

```python
from chunking import ChunkConfig, HuggingFaceTokenizer

config = ChunkConfig(tokenizer=HuggingFaceTokenizer("BAAI/bge-m3"))
```
