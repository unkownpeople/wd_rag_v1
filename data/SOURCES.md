# V1 数据来源

活动集合 `annual_report_v1_single_company` 使用 7 个原始文件：5 份公司年报 PDF、1 份 DOCX 研究报告和 1 份 XLSX 财务分析表。来源数量按入库文件计算，不按报表中的年度列计算。

## 公司年报

| 仓库内文件 | 来源 URL |
| --- | --- |
| `data/annual_reports/raw/apple/apple_2024_10k.pdf` | `https://www.annualreports.com/HostedData/AnnualReportArchive/a/NASDAQ_AAPL_2024.pdf` |
| `data/annual_reports/raw/microsoft/microsoft_2023_annual_report.pdf` | `https://www.annualreports.com/HostedData/AnnualReportArchive/m/NASDAQ_MSFT_2023.pdf` |
| `data/annual_reports/raw/tcs/tcs_2022_annual_report.pdf` | `https://www.annualreports.com/HostedData/AnnualReportArchive/t/OTC_TCS_2022.pdf` |
| `data/annual_reports/raw/tcs/tcs_2023_annual_report.pdf` | `https://www.annualreports.com/HostedData/AnnualReportArchive/t/OTC_TCS_2023.pdf` |
| `data/annual_reports/raw/tcs/tcs_2024_annual_report.pdf` | `https://www.annualreports.com/HostedData/AnnualReportArchive/t/OTC_TCS_2024.pdf` |

Apple 2024 Form 10-K 包含 2024、2023、2022 对比列。Microsoft 2023 Annual Report 的主要期间报表包含 2023、2022、2021，资产负债表包含 2023、2022。三份 TCS 年报的主要财务表分别包含 FY2022/FY2021、FY2023/FY2022、FY2024/FY2023。

## DOCX 与 XLSX

| 仓库内文件 | 来源 URL |
| --- | --- |
| `data/annual_reports/raw/tcs/tcs_fy2022_2024_equity_research_report.docx` | `https://raw.githubusercontent.com/nealaaustin/-TCS_Financial_Analysis.xlsx-TCS_Analyst_Report.docx/main/TCS%20Equity%20Research%20report%20-%20Neal%20Aausrtin.docx` |
| `data/annual_reports/raw/tcs/tcs_fy2022_2024_financial_analysis.xlsx` | `https://raw.githubusercontent.com/nealaaustin/-TCS_Financial_Analysis.xlsx-TCS_Analyst_Report.docx/main/TCS%20Financial%20Analysis%20-%20Neal%20.xlsx` |

## 派生内容

- `data/processed/annual_reports/v1_single_company_chunks/`：7 个源文件的活动切块，共 3959 个。
- `xianlian/collection/annual_report_v1_single_company/storage.sqlite`：对应的 BGE-M3 向量和 payload，共 3959 points。
- 派生数据保留 `document_id`、`company_id`、`fiscal_year`、源格式、页码或表格定位和来源 URL，用于引用核对。

## 文件完整性

| 文件 | SHA-256 |
| --- | --- |
| `apple_2024_10k.pdf` | `ec74758cd767465362c641cf2fd5d8fedd477aed2bf7f62117e67610435dcb88` |
| `microsoft_2023_annual_report.pdf` | `3c318819680126f6817c2fbb3b8ffa7c01eae31dcfea7e174406e4fa70a5cf29` |
| `tcs_2022_annual_report.pdf` | `357e394ff5446fd0cef08f2704a205fa8c4a0a396be3f93a1e4779b14ced06e2` |
| `tcs_2023_annual_report.pdf` | `2e23cd1b5989f16eda6327bae2737ca1307b900a61faddb523cefad9e734ae77` |
| `tcs_2024_annual_report.pdf` | `bd5911dc402c55e8ec2516c634244e5509a40953ad3bd38bb443612afff8ee8a` |
| `tcs_fy2022_2024_equity_research_report.docx` | `cfadae0bd73907e1a638f869a2fbe765508178e6871e4973353800713ef3d45b` |
| `tcs_fy2022_2024_financial_analysis.xlsx` | `f2ccbe4c086e6dcf8ced3c2642f052519f2bf907e95ec319e66f2bcdf787f691` |

第三方材料及派生内容不适用根目录的 MIT 代码许可，说明见 [THIRD_PARTY_NOTICES.md](../THIRD_PARTY_NOTICES.md)。
