from __future__ import annotations

import sys
from importlib.metadata import version
from pathlib import Path

import qdrant_client
from qdrant_client import QdrantClient, models


def main() -> None:
    storage = Path(__file__).resolve().parents[1] / "xianlian"
    client = QdrantClient(path=str(storage))
    collection = "rag_chunks"
    names = [item.name for item in client.get_collections().collections]
    created = False
    if collection not in names:
        client.create_collection(
            collection_name=collection,
            vectors_config=models.VectorParams(size=1024, distance=models.Distance.COSINE),
        )
        created = True
    info = client.get_collection(collection)
    smoke_collection = "_rag_client_smoke"
    if smoke_collection in [item.name for item in client.get_collections().collections]:
        client.delete_collection(smoke_collection)
    client.create_collection(
        collection_name=smoke_collection,
        vectors_config=models.VectorParams(size=1024, distance=models.Distance.COSINE),
    )
    client.upsert(
        collection_name=smoke_collection,
        points=[
            models.PointStruct(id=1, vector=[1.0] + [0.0] * 1023, payload={"kind": "smoke"}),
            models.PointStruct(id=2, vector=[0.0, 1.0] + [0.0] * 1022, payload={"kind": "other"}),
        ],
    )
    hits = client.query_points(
        collection_name=smoke_collection,
        query=[1.0] + [0.0] * 1023,
        limit=1,
    ).points
    client.delete_collection(smoke_collection)
    print(
        {
            "qdrant_client": version("qdrant-client"),
            "python": sys.version.split()[0],
            "storage": str(storage),
            "collection": collection,
            "created": created,
            "collections": [item.name for item in client.get_collections().collections],
            "vector_size": info.config.params.vectors.size,
            "distance": str(info.config.params.vectors.distance),
            "query_smoke_top_id": hits[0].id if hits else None,
        }
    )
    client.close()


if __name__ == "__main__":
    main()
