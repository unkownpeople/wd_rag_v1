from __future__ import annotations

"""Download and pin only the BGE-M3 fast Tokenizer under the F-drive project."""

import json
import os
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODEL_ID = "BAAI/bge-m3"
MODEL_DIR = ROOT / "models" / "bge-m3"
CACHE_DIR = ROOT / ".hf_cache"


def main() -> None:
    # Keep Hugging Face caches inside this project; do not fall back to C:\Users.
    os.environ.setdefault("HF_HOME", str(CACHE_DIR))
    os.environ.setdefault("TRANSFORMERS_CACHE", str(CACHE_DIR / "transformers"))

    from transformers import AutoTokenizer

    tokenizer = AutoTokenizer.from_pretrained(
        MODEL_ID,
        cache_dir=str(CACHE_DIR),
        use_fast=True,
    )
    if not getattr(tokenizer, "is_fast", False):
        raise RuntimeError("BAAI/bge-m3 未提供可返回 offset mapping 的 fast Tokenizer")

    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    tokenizer.save_pretrained(MODEL_DIR)
    probe = "RAG 识别、清洗和切块只使用 Tokenizer，不执行向量化。"
    encoded = tokenizer(
        probe,
        add_special_tokens=False,
        return_offsets_mapping=True,
        truncation=False,
    )
    offsets = [pair for pair in encoded["offset_mapping"] if pair[1] > pair[0]]
    print(
        json.dumps(
            {
                "model_id": MODEL_ID,
                "tokenizer_dir": str(MODEL_DIR),
                "cache_dir": str(CACHE_DIR),
                "is_fast": tokenizer.is_fast,
                "probe_token_count": len(offsets),
                "weights_downloaded": False,
                "embedding_started": False,
                "vector_store_written": False,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
