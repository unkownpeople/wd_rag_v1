from __future__ import annotations

"""BGE-M3 ONNX/int8 编码器：只负责文本到归一化向量。"""

import hashlib
import os
from pathlib import Path
from typing import Iterable

import numpy as np


ROOT = Path(__file__).resolve().parents[1]
MODEL_ID = "BAAI/bge-m3"
MODEL_FORMAT = "onnx-int8"
DEFAULT_MODEL_PATH = ROOT / "models" / "bge-m3-embedding" / "model_quantized.onnx"
DEFAULT_TOKENIZER_PATH = ROOT / "models" / "bge-m3"
VECTOR_DIMENSION = 1024


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


class BGEEmbeddingEncoder:
    """使用 BGE-M3 ONNX/int8 模型进行 CLS pooling 和 L2 归一化。"""

    def __init__(
        self,
        model_path: Path = DEFAULT_MODEL_PATH,
        tokenizer_path: Path = DEFAULT_TOKENIZER_PATH,
        *,
        max_length: int = 8192,
    ) -> None:
        if not model_path.is_file():
            raise FileNotFoundError(f"BGE-M3 ONNX 权重不存在：{model_path}")
        if not tokenizer_path.is_dir():
            raise FileNotFoundError(f"BGE-M3 Tokenizer 不存在：{tokenizer_path}")

        try:
            import onnxruntime as ort
            from transformers import AutoTokenizer
        except ImportError as exc:  # pragma: no cover - environment guard
            raise RuntimeError(
                "Embedding 需要 onnxruntime、transformers，请使用 .venv_rag 安装。"
            ) from exc

        self.model_path = model_path
        self.tokenizer_path = tokenizer_path
        self.max_length = max_length
        self.model_sha256 = sha256_file(model_path)
        self.tokenizer = AutoTokenizer.from_pretrained(
            str(tokenizer_path), use_fast=True, local_files_only=True
        )
        self.session = ort.InferenceSession(
            str(model_path),
            sess_options=self._session_options(),
            providers=["CPUExecutionProvider"],
        )
        inputs = {item.name: item for item in self.session.get_inputs()}
        required_inputs = {"input_ids", "attention_mask"}
        if not required_inputs.issubset(inputs):
            raise ValueError(f"BGE-M3 ONNX 输入不完整：{sorted(inputs)}")
        output = self.session.get_outputs()[0]
        if output.name != "last_hidden_state" or output.shape[-1] != VECTOR_DIMENSION:
            raise ValueError(
                f"BGE-M3 ONNX 输出契约不匹配：name={output.name}, shape={output.shape}"
            )
        self.output_name = output.name

    @staticmethod
    def _session_options():
        """限制本地 CPU 推理的并行内存峰值，尤其适合长表格块。"""

        import onnxruntime as ort

        options = ort.SessionOptions()
        options.intra_op_num_threads = max(1, int(os.environ.get("RAG_ONNX_INTRA_OP_THREADS", "4")))
        options.inter_op_num_threads = 1
        options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
        options.enable_mem_pattern = False
        return options

    def encode(self, texts: Iterable[str], *, batch_size: int = 8) -> np.ndarray:
        values = [str(text or "") for text in texts]
        if not values:
            return np.empty((0, VECTOR_DIMENSION), dtype=np.float32)
        vectors: list[np.ndarray] = []
        for start in range(0, len(values), batch_size):
            batch = values[start : start + batch_size]
            encoded = self.tokenizer(
                batch,
                padding=True,
                truncation=True,
                max_length=self.max_length,
                return_tensors="np",
            )
            model_inputs = {
                name: encoded[name].astype(np.int64)
                for name in ("input_ids", "attention_mask")
            }
            hidden = self.session.run([self.output_name], model_inputs)[0].astype(
                np.float32
            )
            # Xenova/BGE-M3 的模型说明指定 pooling='cls'；不能使用
            # mean pooling，否则得到的向量空间与模型契约不一致。
            pooled = hidden[:, 0, :]
            norms = np.linalg.norm(pooled, axis=1, keepdims=True)
            normalized = pooled / np.clip(norms, 1e-12, None)
            vectors.append(normalized.astype(np.float32))
        result = np.concatenate(vectors, axis=0)
        if result.shape[1] != VECTOR_DIMENSION:
            raise ValueError(f"向量维度错误：{result.shape}")
        return result

    def metadata(self) -> dict[str, object]:
        return {
            "model_id": MODEL_ID,
            "model_format": MODEL_FORMAT,
            "model_path": str(self.model_path),
            "model_sha256": self.model_sha256,
            "tokenizer_path": str(self.tokenizer_path),
            "vector_dimension": VECTOR_DIMENSION,
            "distance": "Cosine",
            "pooling": "cls",
            "normalized": True,
            "max_length": self.max_length,
            "provider": "CPUExecutionProvider",
            "intra_op_num_threads": int(os.environ.get("RAG_ONNX_INTRA_OP_THREADS", "4")),
            "inter_op_num_threads": 1,
        }
