"""本机诊断记录；仅保存请求和响应，不保存配置或请求头。"""
import json
import re
from datetime import datetime, timezone
from pathlib import Path


def write_query_record(directory: Path, request_id: str, request: dict, response: dict | None,
                       elapsed_seconds: float, error_type: str | None = None) -> None:
    record = {'request_id': request_id, 'recorded_at': datetime.now(timezone.utc).isoformat(),
              'elapsed_seconds': round(elapsed_seconds, 3), 'request': request,
              'response': response, 'error_type': error_type}
    text = json.dumps(record, ensure_ascii=False, indent=2)
    text = re.sub(r'(?i)\bsk-[a-z0-9_-]{12,}', '[REDACTED_KEY]', text)
    directory.mkdir(parents=True, exist_ok=True)
    # 随机请求ID，每次独立保存，不覆盖前一次网页测试。
    with (directory / f'{request_id}.json').open('x', encoding='utf-8') as stream:
        stream.write(text)
