from __future__ import annotations

"""V2 最小流程真实 HTTP 深度/广度测试。

只使用标准库访问已经启动的 FastAPI 服务；普通问题统一 ``generate=false``，
因此不会触发外部模型调用。报告写入项目盘，便于复核请求、过滤、证据和拒答链路。
"""

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]
CASE_PATH = ROOT / "data" / "evaluation" / "test_set_v2_deep_breadth.jsonl"
REPORT_PATH = ROOT / "data" / "evaluation" / "test_v2_http.report.json"


def _cases(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _post(url: str, body: dict[str, Any], timeout: float) -> tuple[int, dict[str, Any], float]:
    request = Request(
        url,
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    started = time.perf_counter()
    try:
        with urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
            return int(response.status), payload, time.perf_counter() - started
    except HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            payload = {"detail": raw}
        return int(exc.code), payload, time.perf_counter() - started


def _body(case: dict[str, Any]) -> dict[str, Any]:
    body: dict[str, Any] = {
        "query": str(case["query"]),
        "top_k": 8,
        "mode": "hybrid",
        "generate": case.get("expected_outcome") == "risk_refusal",
    }
    for key in ("company_id", "fiscal_year", "source_format", "document_id", "report_type"):
        if case.get(key) is not None:
            body[key] = case[key]
    if "document_id" not in body and case.get("expected_documents"):
        body["document_id"] = case["expected_documents"][0]
    return body


def run(
    *,
    base_url: str,
    case_path: Path = CASE_PATH,
    report_path: Path = REPORT_PATH,
    timeout: float = 120.0,
) -> dict[str, Any]:
    base_url = base_url.rstrip("/")
    health_status, health, health_seconds = _get(f"{base_url}/health", timeout)
    rows: list[dict[str, Any]] = []
    for case in _cases(case_path):
        expected = str(case.get("expected_outcome") or "normal")
        status, payload, elapsed = _post(f"{base_url}/api/rag/query", _body(case), timeout)
        generation = payload.get("generation") or {}
        retrieval = payload.get("retrieval") or {}
        if expected == "risk_refusal":
            passed = status == 200 and generation.get("status") == "policy_refusal" and not payload.get("answerable")
        elif expected == "manual_review":
            passed = status == 200 and generation.get("status") == "clarification_needed" and not payload.get("answerable")
        elif expected == "no_evidence":
            passed = status == 200 and generation.get("status") == "no_evidence" and not payload.get("answerable")
        else:
            # 检索-only 请求允许回答闸门因 dense 阈值返回 no_evidence；本项
            # 验证的是 HTTP、过滤和 Evidence 链路是否完整。
            passed = status == 200 and bool(payload.get("evidence")) and generation.get("status") in {"not_requested", "retrieval_only", "no_evidence"}
        rows.append(
            {
                "case_id": case["case_id"],
                "dimension": case.get("dimension", "breadth"),
                "expected_outcome": expected,
                "status_code": status,
                "passed": passed,
                "elapsed_ms": round(elapsed * 1000, 1),
                "generation_status": generation.get("status"),
                "answerable": payload.get("answerable"),
                "evidence_count": len(payload.get("evidence") or []),
                "citation_valid": payload.get("citation_valid"),
                "resolved_filters": retrieval.get("resolved_filters", {}),
                "retrieval": {
                    "hit_count": retrieval.get("hit_count"),
                    "effective_top_k": retrieval.get("effective_top_k"),
                    "candidate_count": retrieval.get("candidate_count"),
                    "coverage_truncated": retrieval.get("coverage_truncated"),
                },
                "error": payload.get("detail") if status >= 400 else None,
            }
        )
    report = {
        "stage": "v2_minimal_real_http_deep_breadth",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "base_url": base_url,
        "health": {"status_code": health_status, "payload": health, "elapsed_ms": round(health_seconds * 1000, 1)},
        "case_count": len(rows),
        "passed": sum(bool(row["passed"]) for row in rows),
        "failed": sum(not bool(row["passed"]) for row in rows),
        "rows": rows,
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: report[key] for key in ("case_count", "passed", "failed", "health")}, ensure_ascii=False, indent=2))
    print(f"report={report_path}")
    return report


def _get(url: str, timeout: float) -> tuple[int, dict[str, Any], float]:
    started = time.perf_counter()
    try:
        with urlopen(url, timeout=timeout) as response:
            return int(response.status), json.loads(response.read().decode("utf-8")), time.perf_counter() - started
    except (HTTPError, URLError, TimeoutError) as exc:
        return getattr(exc, "code", 0) or 0, {"detail": str(exc)}, time.perf_counter() - started


def main() -> int:
    parser = argparse.ArgumentParser(description="Run V2 deep/breadth cases against a live FastAPI endpoint")
    parser.add_argument("--base-url", default="http://127.0.0.1:8001")
    parser.add_argument("--case-path", type=Path, default=CASE_PATH)
    parser.add_argument("--report-path", type=Path, default=REPORT_PATH)
    parser.add_argument("--timeout", type=float, default=120.0)
    args = parser.parse_args()
    report = run(base_url=args.base_url, case_path=args.case_path, report_path=args.report_path, timeout=args.timeout)
    return 0 if report["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
