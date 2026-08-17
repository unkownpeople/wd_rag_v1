from __future__ import annotations

"""打印项目 F 盘 Qdrant Local 集合点数。"""

from pathlib import Path

from qdrant_client import QdrantClient


root = Path(__file__).resolve().parents[1]
client = QdrantClient(path=str(root / "xianlian"))
try:
    for item in client.get_collections().collections:
        print(item.name, client.count(item.name, exact=True).count)
finally:
    client.close()
