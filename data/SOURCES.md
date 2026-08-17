# V1 数据来源

本目录列出活动集合 `annual_report_v1_single_company` 实际使用的 7 份源文件，以及为已保留 PDF 切块补充的 Apple 2022、Apple 2023 两份原始 PDF。补充 PDF 未进入当前活动集合。文件用于求职项目的检索与引用演示，原始内容版权归各公司或原作者所有；本仓库不对第三方内容重新授权。

根目录 `LICENSE` 不覆盖这些第三方文件及其派生内容。完整边界见 `THIRD_PARTY_NOTICES.md`；公开发布前仍需由仓库维护者确认原始文件的再分发权限。

## 活动数据库来源

### 公司年报 PDF

- Apple 2024 Form 10-K：`https://www.annualreports.com/HostedData/AnnualReportArchive/a/NASDAQ_AAPL_2024.pdf`
- Microsoft 2023 Annual Report：`https://www.annualreports.com/HostedData/AnnualReportArchive/m/NASDAQ_MSFT_2023.pdf`
- TCS 2022 Annual Report：`https://www.annualreports.com/HostedData/AnnualReportArchive/t/OTC_TCS_2022.pdf`
- TCS 2023 Annual Report：`https://www.annualreports.com/HostedData/AnnualReportArchive/t/OTC_TCS_2023.pdf`
- TCS 2024 Annual Report：`https://www.annualreports.com/HostedData/AnnualReportArchive/t/OTC_TCS_2024.pdf`

### DOCX 与 XLSX

- TCS Equity Research Report：`https://raw.githubusercontent.com/nealaaustin/-TCS_Financial_Analysis.xlsx-TCS_Analyst_Report.docx/main/TCS%20Equity%20Research%20report%20-%20Neal%20Aausrtin.docx`
- TCS Financial Analysis：`https://raw.githubusercontent.com/nealaaustin/-TCS_Financial_Analysis.xlsx-TCS_Analyst_Report.docx/main/TCS%20Financial%20Analysis%20-%20Neal%20.xlsx`

来源数量按入库原文件计算，不按报表中的年度列计算。Microsoft 活动来源只有 Microsoft 2023 Annual Report 一份，但其利润表、综合收益表、现金流量表和股东权益表包含 2023、2022、2021 三年数据，资产负债表包含 2023、2022 两个时点。三份 TCS 年报的主要财务表也分别包含 FY2022/FY2021、FY2023/FY2022、FY2024/FY2023 对比数据。

## 补充原始 PDF

- Apple 2022 Form 10-K：`https://www.annualreports.com/HostedData/AnnualReportArchive/a/NASDAQ_AAPL_2022.pdf`
- Apple 2023 Form 10-K：`https://www.annualreports.com/HostedData/AnnualReportArchive/a/NASDAQ_AAPL_2023.pdf`

这两份 PDF 分别对应仓库中已保留的 Apple 2022、Apple 2023 PDF 切块和完整性评分，不属于当前 3959 points 的活动集合来源。当前活动集合中的 Apple 数据仅来自 Apple 2024 Form 10-K；该年报自身包含 2023、2022 对比列。

## 派生内容

- `data/processed/annual_reports/v1_single_company_chunks/` 是以上 7 份文件的解析、清洗和切块派生结果。
- `xianlian/collection/annual_report_v1_single_company/storage.sqlite` 是这些 chunks 的 BGE-M3 向量与 payload 持久化结果。
- 派生结果保留 `document_id`、`company_id`、`fiscal_year`、源格式、页码/表格定位和来源 URL，用于引用审计。

## 文件完整性

| 文件 | SHA-256 |
| --- | --- |
| `apple_2022_10k.pdf` | `40f4a5169ac49f1c3011b8287afe28364138cd4f3e4db36c8d75c9da0f6b2a93` |
| `apple_2023_10k.pdf` | `068176ab665682096a79859d73b805c7b2fda05331e503f26d893b582353464e` |
| `apple_2024_10k.pdf` | `ec74758cd767465362c641cf2fd5d8fedd477aed2bf7f62117e67610435dcb88` |
| `microsoft_2023_annual_report.pdf` | `3c318819680126f6817c2fbb3b8ffa7c01eae31dcfea7e174406e4fa70a5cf29` |
| `tcs_2022_annual_report.pdf` | `357e394ff5446fd0cef08f2704a205fa8c4a0a396be3f93a1e4779b14ced06e2` |
| `tcs_2023_annual_report.pdf` | `2e23cd1b5989f16eda6327bae2737ca1307b900a61faddb523cefad9e734ae77` |
| `tcs_2024_annual_report.pdf` | `bd5911dc402c55e8ec2516c634244e5509a40953ad3bd38bb443612afff8ee8a` |
| `tcs_fy2022_2024_equity_research_report.docx` | `cfadae0bd73907e1a638f869a2fbe765508178e6871e4973353800713ef3d45b` |
| `tcs_fy2022_2024_financial_analysis.xlsx` | `f2ccbe4c086e6dcf8ced3c2642f052519f2bf907e95ec319e66f2bcdf787f691` |
