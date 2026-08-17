from __future__ import annotations

"""运行并验收当前 RAG 离线全链路。"""

import argparse
import json
from pathlib import Path
import subprocess
import time
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
PYTHON = ROOT / ".venv_rag" / "Scripts" / "python.exe"
PROCESSED = ROOT / "data" / "processed"
CLEANED = PROCESSED / "cleaned"
CHUNKS = PROCESSED / "chunks" / "bge-m3-tokenizer-v1"
EMBEDDING_MANIFEST = PROCESSED / "embeddings" / "bge-m3-onnx-int8.manifest.json"
EVALUATION = ROOT / "data" / "evaluation"
DENSE_REPORT = EVALUATION / "retrieval_eval.report.json"
HYBRID_REPORT = EVALUATION / "retrieval_eval.hybrid.report.json"
DEFAULT_REPORT = EVALUATION / "full_pipeline.acceptance.json"


def _tail(value: str, limit: int = 4000) -> str:
    value = value.strip()
    return value if len(value) <= limit else value[-limit:]


def _run_stage(stage: str, module: str, args: list[str], stages: list[dict[str, Any]]) -> None:
    command = [str(PYTHON), "-m", module, *args]
    started = time.perf_counter()
    completed = subprocess.run(
        command,
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    record = {
        "stage": stage,
        "module": module,
        "args": args,
        "returncode": completed.returncode,
        "duration_seconds": round(time.perf_counter() - started, 3),
        "stdout_tail": _tail(completed.stdout),
        "stderr_tail": _tail(completed.stderr),
    }
    stages.append(record)
    print(f"[{stage}] returncode={completed.returncode} duration={record['duration_seconds']}s")
    if completed.returncode != 0:
        raise RuntimeError(f"阶段失败：{stage}\n{record['stderr_tail'] or record['stdout_tail']}")


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _legacy_baseline_check() -> dict[str, Any]:
    baseline_files = sorted((PROCESSED / "chunks").glob("*.chunks.jsonl"))
    baseline_score_files = sorted((PROCESSED / "chunks").glob("*.chunks.score.json"))
    report_path = PROCESSED / "chunks" / "chunking.report.json"
    tokenizers = []
    for score_path in baseline_score_files:
        score = _read_json(score_path)
        tokenizers.append(score.get("tokenizer") or score.get("parameters", {}).get("tokenizer"))
    tokenizer = next((value for value in tokenizers if value), None)
    return {
        "preserved": bool(baseline_files)
        and bool(baseline_score_files)
        and report_path.is_file()
        and tokenizer == "heuristic-regex-v1",
        "file_count": len(baseline_files),
        "score_file_count": len(baseline_score_files),
        "report": str(report_path),
        "tokenizer": tokenizer,
    }


def _cleaning_check() -> dict[str, Any]:
    reports: dict[str, Any] = {}
    for path in sorted(CLEANED.glob("*.cleaned.report.json")):
        report = _read_json(path)
        reports[path.name] = {
            "passed": bool(report.get("validation", {}).get("passed")),
            "source_format": report.get("source_format"),
        }
    return {
        "passed": bool(reports) and all(item["passed"] for item in reports.values()),
        "reports": reports,
    }


def _chunking_check() -> dict[str, Any]:
    report = _read_json(CHUNKS / "chunking.report.json")
    files = report.get("files", [])
    integrity_passed = all(
        item.get("text_lost_tokens") == 0
        and item.get("table_lost_body_rows") == 0
        and item.get("internal_duplicate_tokens") == 0
        for item in files
    )
    return {
        "passed": bool(files) and integrity_passed,
        "tokenizer": report.get("parameters", {}).get("tokenizer"),
        "chunk_count": report.get("chunk_count"),
        "weighted_score": report.get("weighted_score"),
        "files": files,
    }


def _qdrant_check(expected_count: int) -> dict[str, Any]:
    from qdrant_client import QdrantClient

    storage = ROOT / "xianlian"
    client = QdrantClient(path=str(storage))
    try:
        collection = client.get_collection("rag_chunks")
        vector_config = collection.config.params.vectors
        distance = getattr(vector_config.distance, "value", str(vector_config.distance))
        count = client.count("rag_chunks", exact=True).count
        return {
            "count": count,
            "expected_count": expected_count,
            "vector_size": vector_config.size,
            "distance": str(distance),
            "passed": count == expected_count and vector_config.size == 1024 and str(distance).lower() == "cosine",
        }
    finally:
        client.close()


def _artifact_checks() -> dict[str, Any]:
    manifest = _read_json(EMBEDDING_MANIFEST)
    dense = _read_json(DENSE_REPORT)
    hybrid = _read_json(HYBRID_REPORT)
    chunk_count = int(manifest["chunk_count"])
    qdrant = _qdrant_check(chunk_count)
    checks = {
        "manifest_point_count_matches_chunks": manifest.get("point_count") == chunk_count,
        "qdrant_contract_and_count": qdrant["passed"],
        "dense_cases_complete": dense.get("case_count") == 21,
        "hybrid_recall_at_5": hybrid.get("metrics", {}).get("recall_at_5") == 1.0,
        "hybrid_citation_alignment_at_5": hybrid.get("metrics", {}).get("citation_alignment_at_5") == 1.0,
        "hybrid_no_answer_refusal": hybrid.get("metrics", {}).get("no_answer_refusal_rate") == 1.0,
    }
    return {
        "passed": all(checks.values()),
        "checks": checks,
        "manifest": {
            "chunk_count": manifest.get("chunk_count"),
            "point_count": manifest.get("point_count"),
            "model_sha256": manifest.get("model", {}).get("model_sha256"),
        },
        "qdrant": qdrant,
        "dense_metrics": dense.get("metrics", {}),
        "hybrid_metrics": hybrid.get("metrics", {}),
    }


def run(report_path: Path = DEFAULT_REPORT) -> dict[str, Any]:
    stages: list[dict[str, Any]] = []
    baseline = _legacy_baseline_check()
    result: dict[str, Any] = {
        "stage": "full_pipeline_acceptance",
        "project_root": str(ROOT),
        "python": str(PYTHON),
        "baseline": baseline,
        "stages": stages,
        "passed": False,
    }
    try:
        if not PYTHON.is_file():
            raise FileNotFoundError(f"F 盘虚拟环境不存在：{PYTHON}")
        if not baseline["preserved"]:
            raise RuntimeError("旧正则基线不存在，停止运行以避免覆盖不可追溯的旧结果。")

        _run_stage("parse", "parsers.run_multiformat_parse", [], stages)
        _run_stage("clean_pdf", "cleaners.run_pdf_clean", [], stages)
        _run_stage("clean_word", "cleaners.run_word_clean", [], stages)
        _run_stage("clean_excel", "cleaners.run_excel_clean", [], stages)
        _run_stage(
            "chunk_bge_m3",
            "chunking.run_chunking",
            ["--input-dir", str(CLEANED), "--output-dir", str(CHUNKS)],
            stages,
        )
        _run_stage("vectorize_qdrant", "embeddings.vectorize", [], stages)
        _run_stage("evaluate_dense", "embeddings.evaluate", ["--mode", "dense", "--output", str(DENSE_REPORT)], stages)
        _run_stage(
            "evaluate_hybrid",
            "embeddings.evaluate",
            ["--mode", "hybrid", "--output", str(HYBRID_REPORT)],
            stages,
        )
        _run_stage(
            "tests",
            "unittest",
            [
                "embeddings.test_retrieve",
                "chunking.test_chunker",
                "cleaners.test_pdf_cleaner",
                "cleaners.test_word_excel_cleaners",
            ],
            stages,
        )

        cleaning = _cleaning_check()
        chunking = _chunking_check()
        artifacts = _artifact_checks()
        result.update(
            {
                "cleaning": cleaning,
                "chunking": chunking,
                "artifacts": artifacts,
                "checks": {
                    "legacy_baseline_preserved": baseline["preserved"],
                    "cleaning_passed": cleaning["passed"],
                    "chunking_integrity_passed": chunking["passed"],
                    "artifacts_passed": artifacts["passed"],
                },
            }
        )
        result["passed"] = all(result["checks"].values())
        result["boundaries"] = [
            "本次验收覆盖解析、清洗、BGE-M3 Tokenizer 切块、Embedding、Qdrant、Dense/Hybrid 检索和固定评测集。",
            "尚未覆盖 LLM 生成、rerank、延迟、人工答案评分和多公司年报专用字段过滤。",
        ]
    except Exception as exc:
        result["error"] = str(exc)

    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Run and accept the local RAG pipeline on F drive.")
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    args = parser.parse_args()
    result = run(args.report.resolve())
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
