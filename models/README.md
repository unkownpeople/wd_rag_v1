# BGE-M3 本地模型

Tokenizer 来自 `BAAI/bge-m3`，上游模型卡与许可入口：`https://huggingface.co/BAAI/bge-m3`。根目录 `LICENSE` 不会把第三方模型或 Tokenizer 重新授权为本项目代码；详见 `THIRD_PARTY_NOTICES.md`。

仓库保留 `models/bge-m3/` Tokenizer，但不提交 ONNX 权重。原因是已验证权重为 `569,694,530` 字节，超过 GitHub 普通 Git 的 100 MiB 单文件限制。

运行前将与 `BAAI/bge-m3` 对应的 ONNX int8 权重放到：

```text
models/bge-m3-embedding/model_quantized.onnx
```

当前 V1 验证契约：

- Model ID：`BAAI/bge-m3`
- Format：`onnx-int8`
- Vector dimension：`1024`
- Pooling：`cls`
- Normalized：`true`
- Distance：`Cosine`
- SHA-256：`0826f8c1ab9edf1801db86c61919d4d108e8bfc0b809ec823ad366882ff0b77d`

PowerShell 校验：

```powershell
Get-FileHash .\models\bge-m3-embedding\model_quantized.onnx -Algorithm SHA256
```

哈希不一致意味着查询向量不再与已验收数据库使用完全相同的模型文件，不能直接复用当前准确率结论。
