# Qdrant Local 活动数据库

该目录只保留已经通过 V1 验收的 `annual_report_v1_single_company` 集合。

- Vector size：1024
- Distance：Cosine
- Points：3959
- Storage：`collection/annual_report_v1_single_company/storage.sqlite`

`.lock`、SQLite WAL/SHM 和旧的 TCS 回退集合不进入发布副本。
