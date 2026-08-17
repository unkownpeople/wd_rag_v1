from __future__ import annotations

"""切分阶段命令入口：负责参数、批量文件和报告落盘。"""

import argparse
import json
from pathlib import Path
from typing import Any

from .config import ChunkConfig
from .io import read_jsonl, write_jsonl
from .pipeline import chunk_records
from .score import score_chunks


def _output_name(input_path: Path) -> str:
    suffix = ".cleaned.jsonl"
    stem = input_path.name[: -len(suffix)] if input_path.name.endswith(suffix) else input_path.stem
    return f"{stem}.chunks.jsonl"


def _parameters(config: ChunkConfig) -> dict[str, object]:
    return {
        "target_tokens": config.target_tokens,
        "max_tokens": config.max_tokens,
        "min_tokens": config.min_tokens,
        "sentence_overlap": config.sentence_overlap,
        "token_overlap": config.token_overlap,
        "small_table_body_rows": config.small_table_body_rows,
        "tokenizer": config.tokenizer_name,
    }


def run(input_dir: Path, output_dir: Path) -> dict[str, Any]:
    """批量执行切分，并为每个输入生成独立评分报告。"""

    output_dir.mkdir(parents=True, exist_ok=True)
    config = ChunkConfig()
    file_reports: list[dict[str, Any]] = []
    for input_path in sorted(input_dir.glob("*.cleaned.jsonl")):
        records = read_jsonl(input_path)
        chunks = chunk_records(records, config)
        chunk_path = output_dir / _output_name(input_path)
        score_path = output_dir / f"{chunk_path.stem}.score.json"
        write_jsonl(chunk_path, chunks)

        report = score_chunks(records, chunks, config)
        report.update(
            {
                "input_file": input_path.relative_to(input_dir.parent.parent).as_posix(),
                "chunk_file": chunk_path.relative_to(input_dir.parent.parent).as_posix(),
                "parameters": _parameters(config),
            }
        )
        score_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        file_reports.append(report)
        print(
            f"{input_path.name}: chunks={len(chunks)} "
            f"score={report['overall_score']} grade={report['grade']}"
        )

    weights = [
        max(1, report["comparison"]["text_source_tokens"] + report["comparison"]["table_source_body_rows"])
        for report in file_reports
    ]
    weighted_score = (
        sum(report["overall_score"] * weight for report, weight in zip(file_reports, weights))
        / sum(weights)
        if weights
        else 0.0
    )
    aggregate = {
        "stage": "chunk",
        "score_type": "offline_intrinsic_chunking_score",
        "input_dir": str(input_dir),
        "output_dir": str(output_dir),
        "parameters": _parameters(config),
        "file_count": len(file_reports),
        "chunk_count": sum(report["chunk_count"] for report in file_reports),
        "weighted_score": round(weighted_score, 2),
        "files": [
            {
                "input_file": report["input_file"],
                "chunk_file": report["chunk_file"],
                "chunk_count": report["chunk_count"],
                "overall_score": report["overall_score"],
                "grade": report["grade"],
                "text_lost_tokens": report["comparison"]["text_lost_tokens"],
                "table_lost_body_rows": report["comparison"]["table_lost_body_rows"],
                "duplicate_overlap_tokens": report["comparison"]["duplicate_overlap_tokens"],
                "internal_duplicate_tokens": report["comparison"]["internal_duplicate_tokens"],
                "cross_chunk_overlap_tokens": report["comparison"]["cross_chunk_overlap_tokens"],
            }
            for report in file_reports
        ],
        "interpretation": "这是与清洗内容的离线完整性评分；后续仍需固定评测集和相同 Embedding 模型比较召回。",
    }
    (output_dir / "chunking.report.json").write_text(
        json.dumps(aggregate, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(
        f"total: files={len(file_reports)} chunks={aggregate['chunk_count']} "
        f"weighted_score={aggregate['weighted_score']}"
    )
    return aggregate


def main() -> None:
    parser = argparse.ArgumentParser(description="Chunk cleaned JSONL files with UTF-8 output.")
    root = Path(__file__).resolve().parents[1]
    parser.add_argument("--input-dir", type=Path, default=root / "data" / "processed" / "cleaned")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=root / "data" / "processed" / "chunks" / "bge-m3-tokenizer-v1",
    )
    args = parser.parse_args()
    run(args.input_dir.resolve(), args.output_dir.resolve())
