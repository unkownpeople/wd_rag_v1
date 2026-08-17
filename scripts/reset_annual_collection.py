from __future__ import annotations

"""删除未完成的年报向量集合；只允许操作 annual_report_chunks_v1。"""

from pathlib import Path
import shutil

from qdrant_client import QdrantClient


ROOT = Path(__file__).resolve().parents[1]
COLLECTION = "annual_report_chunks_v1"
COLLECTION_STORAGE = ROOT / "xianlian" / "collection" / COLLECTION
client = QdrantClient(path=str(ROOT / "xianlian"))
try:
    names = {item.name for item in client.get_collections().collections}
    before = client.count(COLLECTION, exact=True).count if COLLECTION in names else 0
    if COLLECTION in names:
        client.delete_collection(COLLECTION)
    after_names = {item.name for item in client.get_collections().collections}
    print({"collection": COLLECTION, "points_before": before, "deleted": COLLECTION not in after_names})
finally:
    client.close()

# Qdrant Local 删除集合后可能仍保留对应的物理 storage.sqlite；该目录
# 只允许由本清理脚本处理，避免下一次同名集合复用失败运行的旧点。
physical_deleted = False
if COLLECTION_STORAGE.is_dir():
    shutil.rmtree(COLLECTION_STORAGE)
    physical_deleted = True
print({"physical_storage": str(COLLECTION_STORAGE), "physical_deleted": physical_deleted})
