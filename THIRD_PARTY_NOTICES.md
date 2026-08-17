# 第三方内容说明

本仓库根目录 LICENSE 仅授权本项目自行编写的源代码和文档，不改变第三方年报、研究报告、电子表格、模型文件及其派生内容的版权归属。

## 年报与分析文件

下列文件用于非商业的检索、引用与求职项目演示。原始内容版权归对应公司、发布机构或原作者所有，本项目不对这些文件重新授权。

| 文件 | SHA-256 |
| --- | --- |
| apple_2022_10k.pdf | 40f4a5169ac49f1c3011b8287afe28364138cd4f3e4db36c8d75c9da0f6b2a93 |
| apple_2023_10k.pdf | 068176ab665682096a79859d73b805c7b2fda05331e503f26d893b582353464e |
| apple_2024_10k.pdf | ec74758cd767465362c641cf2fd5d8fedd477aed2bf7f62117e67610435dcb88 |
| microsoft_2023_annual_report.pdf | 3c318819680126f6817c2fbb3b8ffa7c01eae31dcfea7e174406e4fa70a5cf29 |
| tcs_2022_annual_report.pdf | 357e394ff5446fd0cef08f2704a205fa8c4a0a396be3f93a1e4779b14ced06e2 |
| tcs_2023_annual_report.pdf | 2e23cd1b5989f16eda6327bae2737ca1307b900a61faddb523cefad9e734ae77 |
| tcs_2024_annual_report.pdf | bd5911dc402c55e8ec2516c634244e5509a40953ad3bd38bb443612afff8ee8a |
| tcs_fy2022_2024_equity_research_report.docx | cfadae0bd73907e1a638f869a2fbe765508178e6871e4973353800713ef3d45b |
| tcs_fy2022_2024_financial_analysis.xlsx | f2ccbe4c086e6dcf8ced3c2642f052519f2bf907e95ec319e66f2bcdf787f691 |

其中 7 份文件是当前活动集合来源；Apple 2022、Apple 2023 两份 PDF 是已保留切块对应的补充原件，未进入当前活动集合。

完整来源 URL 见 data/SOURCES.md。公开发布这些原始文件前，仓库维护者仍应自行确认来源网站和权利人的再分发条款；本说明不构成版权许可。

## 派生数据和向量数据库

data/processed 下的切块与 xianlian 下的 Qdrant 数据库由上述第三方文件派生，可能包含原文片段，因此同样不纳入本项目 MIT 许可。若需要规避再分发风险，可以从公开仓库中移除原始文件、派生切块和向量数据库，仅保留来源清单与本地构建流程。

## BGE-M3

Tokenizer 来自 BAAI/bge-m3。模型信息与许可入口：

- https://huggingface.co/BAAI/bge-m3

ONNX 权重不提交到 GitHub；本地使用者应从有权使用的来源获取，并遵守上游模型许可。模型放置位置和校验值见 models/README.md。

## Python 与前端依赖

第三方 Python 包各自适用其上游许可。requirements-rag.txt 中的版本锁定不表示这些依赖改用本项目许可证。frontend 使用 Node.js 标准库，无额外 npm 运行依赖。
