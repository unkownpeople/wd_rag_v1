from __future__ import annotations

"""从当前 V1 chunks 独立构建或校验单公司活动集合。"""

import argparse
from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from embeddings.vectorize import _load_chunks, run as vectorize


TARGET_CHUNK_DIR = (
    ROOT / "data" / "processed" / "annual_reports" / "v1_single_company_chunks"
)
QDRANT_PATH = ROOT / "xianlian"
TARGET_COLLECTION = "annual_report_v1_single_company"
MANIFEST_PATH = (
    ROOT
    / "data"
    / "processed"
    / "annual_reports"
    / "v1_single_company_reports"
    / "embedding.manifest.json"
)
REPORT_PATH = (
    ROOT / "data" / "evaluation" / "v1_single_company.collection_build.json"
)


def _report_path(path: Path) -> str:
    resolved = path.resolve()
    try:
        return resolved.relative_to(ROOT).as_posix()
    except ValueError:
        return str(resolved)


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _validate_chunks(chunks: list[dict[str, Any]]) -> dict[str, int]:
    chunk_ids = [str(chunk.get("chunk_id") or "") for chunk in chunks]
    if not all(chunk_ids) or len(chunk_ids) != len(set(chunk_ids)):
        raise RuntimeError("V1 切块存在缺失或重复 chunk_id")

    companies = {str(chunk.get("company_id") or "") for chunk in chunks}
    if companies != {"apple", "microsoft", "tcs"}:
        raise RuntimeError(f"V1 公司集合不符合边界：{sorted(companies)}")

    counts = Counter(
        (str(chunk.get("company_id") or ""), int(chunk.get("fiscal_year")))
        for chunk in chunks
        if chunk.get("company_id") and chunk.get("fiscal_year") is not None
    )
    return {
        f"{company}:{year}": count
        for (company, year), count in sorted(counts.items())
    }


def run(*, recreate: bool = False, batch_size: int = 32) -> dict[str, Any]:
    chunks, _ = _load_chunks(TARGET_CHUNK_DIR)
    company_year_counts = _validate_chunks(chunks)

    # 默认模式校验并复用当前集合；--recreate 会清空目标集合并从现有
    # 3959 个 chunks 全量编码，不依赖任何旧集合或已裁剪的中间目录。
    manifest = vectorize(
        chunk_dir=TARGET_CHUNK_DIR,
        qdrant_path=QDRANT_PATH,
        manifest_path=MANIFEST_PATH,
        collection=TARGET_COLLECTION,
        recreate_collection=recreate,
        resume_existing=not recreate,
        batch_size=batch_size,
        max_length=8192,
    )

    point_count = int(manifest["point_count"])
    result = {
        "stage": "v1_single_company_collection_build",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "collection": TARGET_COLLECTION,
        "qdrant_path": _report_path(QDRANT_PATH),
        "chunk_dir": _report_path(TARGET_CHUNK_DIR),
        "chunk_count": len(chunks),
        "point_count": point_count,
        "company_year_counts": company_year_counts,
        "build_source": "v1_single_company_chunks",
        "resume_existing": not recreate,
        "encoded_point_count": int(
            manifest.get("missing_point_count_before_write") or 0
        ),
        "manifest": _report_path(MANIFEST_PATH),
        "passed": point_count == len(chunks),
    }
    _write_json(REPORT_PATH, result)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build or validate the self-contained V1 single-company collection"
    )
    parser.add_argument(
        "--recreate",
        action="store_true",
        help="清空目标集合并从当前 V1 chunks 全量重新编码",
    )
    parser.add_argument("--batch-size", type=int, default=32)
    args = parser.parse_args()
    try:
        result = run(recreate=args.recreate, batch_size=args.batch_size)
    except Exception as exc:
        failure = {
            "stage": "v1_single_company_collection_build",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "passed": False,
            "error_type": type(exc).__name__,
            "error": str(exc),
        }
        _write_json(REPORT_PATH, failure)
        print(json.dumps(failure, ensure_ascii=False, indent=2))
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
