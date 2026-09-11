# README 参考与结构说明

日期：2026-09-11。

## 实际参考

本次通过 GitHub API 读取以下项目 README，借鉴信息组织方式，功能表述以本仓库实现为准。

| 项目 | 观察到的组织方式 | 本项目采用 |
| --- | --- | --- |
| [RAGFlow](https://github.com/infiniflow/ragflow/blob/main/README.md) | 项目定义、Get Started、核心特性、系统架构、部署前提、配置、文档入口 | 首页先说明用途，架构与部署前提分开，详细资料用链接承接 |
| [AnythingLLM](https://github.com/Mintplex-Labs/anything-llm/blob/master/README.md) | 产品概览、能力、支持组件、技术概览、自托管、开发、隐私 | 先回答能做什么，再讲技术；说明远程 API 的数据流向 |
| [kotaemon](https://github.com/Cinnamon/kotaemon/blob/main/README.md) | 一句话定位、预览、用户与开发者入口、特性、安装与模型配置 | 保留真实界面，给出按步骤的启动入口和模型前置条件 |

读取到的 README blob：RAGFlow `3418232ddb20a68cc1acc5c3da0060cf19777754`；AnythingLLM `2ab224f2e145880263509fed538ffbc4c4de5b6a`；kotaemon `e73f7baea4b6f1e2533d6d9989128661b27b96c7`。上游页面会继续更新。

## 本次调整

README 按“用途 → 能力 → 界面 → 快速开始 → 复合问题 → 数据与架构 → 测试边界 → 目录与许可”组织，继续使用简洁中文。

逐文件来源和哈希保留在数据说明；历史测试保留在历史边界文档；工程完成度独立成表。首页提供真实测试摘要与入口，明确样本规模、计算局限和权重准备条件。

配置模板同步当前代码中的上下文、回答与规划预算：64000 字符、8000 tokens、4000 tokens。已有 `.env` 不覆盖，新安装可直接从模板获得当前预算。截图标明原记录日期，避免误认为本次重新录制的 V1.3 界面验收。
