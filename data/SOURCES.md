# V1 数据来源

项目当前登记 12 个原始文件：10 份公司年报 PDF、1 份 DOCX 研究报告和 1 份 XLSX 财务分析表。来源数量按文件计算，不按报表中的年度列计算。

## 公司年报

| 仓库内文件 | 来源 URL |
| --- | --- |
| `data/annual_reports/raw/apple/apple_2024_10k.pdf` | `https://www.annualreports.com/HostedData/AnnualReportArchive/a/NASDAQ_AAPL_2024.pdf` |
| `data/annual_reports/raw/apple/2022/apple_2022_10k.pdf` | `https://www.annualreports.com/HostedData/AnnualReportArchive/a/NASDAQ_AAPL_2022.pdf` |
| `data/annual_reports/raw/apple/2023/apple_2023_10k.pdf` | `https://www.annualreports.com/HostedData/AnnualReportArchive/a/NASDAQ_AAPL_2023.pdf` |
| `data/annual_reports/raw/microsoft/microsoft_2023_annual_report.pdf` | `https://www.annualreports.com/HostedData/AnnualReportArchive/m/NASDAQ_MSFT_2023.pdf` |
| `data/annual_reports/raw/microsoft/2021/microsoft_2021_annual_report.pdf` | `https://www.annualreports.com/HostedData/AnnualReportArchive/m/NASDAQ_MSFT_2021.pdf` |
| `data/annual_reports/raw/microsoft/2022/microsoft_2022_annual_report.pdf` | `https://www.annualreports.com/HostedData/AnnualReportArchive/m/NASDAQ_MSFT_2022.pdf` |
| `data/annual_reports/raw/tcs/tcs_2022_annual_report.pdf` | `https://www.annualreports.com/HostedData/AnnualReportArchive/t/OTC_TCS_2022.pdf` |
| `data/annual_reports/raw/tcs/tcs_2023_annual_report.pdf` | `https://www.annualreports.com/HostedData/AnnualReportArchive/t/OTC_TCS_2023.pdf` |
| `data/annual_reports/raw/tcs/tcs_2024_annual_report.pdf` | `https://www.annualreports.com/HostedData/AnnualReportArchive/t/OTC_TCS_2024.pdf` |
| `data/annual_reports/raw/tcs/2021/tcs_2021_annual_report.pdf` | `https://www.annualreports.com/HostedData/AnnualReportArchive/t/OTC_TCS_2021.pdf` |

Apple 年报原件覆盖 2022–2024 连续报告年份；Microsoft 年报原件覆盖 2021–2023 连续报告年份。Apple 2024 Form 10-K 另包含 2024、2023、2022 对比列；Microsoft 2023 Annual Report 的主要期间报表包含 2023、2022、2021。四份 TCS 年报覆盖 FY2021–FY2024 连续报告年份。

## DOCX 与 XLSX

| 仓库内文件 | 来源 URL |
| --- | --- |
| `data/annual_reports/raw/tcs/tcs_fy2022_2024_equity_research_report.docx` | `https://raw.githubusercontent.com/nealaaustin/-TCS_Financial_Analysis.xlsx-TCS_Analyst_Report.docx/main/TCS%20Equity%20Research%20report%20-%20Neal%20Aausrtin.docx` |
| `data/annual_reports/raw/tcs/tcs_fy2022_2024_financial_analysis.xlsx` | `https://raw.githubusercontent.com/nealaaustin/-TCS_Financial_Analysis.xlsx-TCS_Analyst_Report.docx/main/TCS%20Financial%20Analysis%20-%20Neal%20.xlsx` |

## 派生内容

- `data/processed/annual_reports/v1_single_company_chunks/`：12 个源文件共 6266 个切块，其中 Apple 2022/2023 与 Microsoft 2021/2022 本轮新增 1337 个。
- `xianlian/collection/annual_report_v1_single_company/storage.sqlite`：对应的 BGE-M3 向量和 payload，共 6266 points。
- 派生数据保留 `document_id`、`company_id`、`fiscal_year`、源格式、页码或表格定位和来源 URL，用于引用核对。

## 文件完整性

| 文件 | SHA-256 |
| --- | --- |
| `apple_2024_10k.pdf` | `ec74758cd767465362c641cf2fd5d8fedd477aed2bf7f62117e67610435dcb88` |
| `apple_2022_10k.pdf` | `40f4a5169ac49f1c3011b8287afe28364138cd4f3e4db36c8d75c9da0f6b2a93` |
| `apple_2023_10k.pdf` | `068176ab665682096a79859d73b805c7b2fda05331e503f26d893b582353464e` |
| `microsoft_2023_annual_report.pdf` | `3c318819680126f6817c2fbb3b8ffa7c01eae31dcfea7e174406e4fa70a5cf29` |
| `microsoft_2021_annual_report.pdf` | `71b4d3a9c8aa98d2b0a0b1165e979fc8ac3084925b2e778fc143b50badad1461` |
| `microsoft_2022_annual_report.pdf` | `50507a219c93a452c1a15e1c5bb5d01d53a97d75c1ce91ea0a9703ef7debca95` |
| `tcs_2021_annual_report.pdf` | `fb4a079ba19a3029b476a50cedefe96fdaf34991ccae805b9788b7db770b60d6` |
| `tcs_2022_annual_report.pdf` | `357e394ff5446fd0cef08f2704a205fa8c4a0a396be3f93a1e4779b14ced06e2` |
| `tcs_2023_annual_report.pdf` | `2e23cd1b5989f16eda6327bae2737ca1307b900a61faddb523cefad9e734ae77` |
| `tcs_2024_annual_report.pdf` | `bd5911dc402c55e8ec2516c634244e5509a40953ad3bd38bb443612afff8ee8a` |
| `tcs_fy2022_2024_equity_research_report.docx` | `cfadae0bd73907e1a638f869a2fbe765508178e6871e4973353800713ef3d45b` |
| `tcs_fy2022_2024_financial_analysis.xlsx` | `f2ccbe4c086e6dcf8ced3c2642f052519f2bf907e95ec319e66f2bcdf787f691` |

第三方材料及派生内容不适用根目录的 MIT 代码许可，说明见 [THIRD_PARTY_NOTICES.md](../THIRD_PARTY_NOTICES.md)。
