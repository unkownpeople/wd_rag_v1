from __future__ import annotations

"""通过真实 HTTP 接口评测无显式过滤器的中文公司别名问题。"""

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]
DATASET_PATH = ROOT / "data" / "evaluation" / "test_set_v1_chinese_aliases.jsonl"
FULL_REPORT_PATH = ROOT / "data" / "evaluation" / "test_set_v1_chinese_aliases.acceptance.json"
RETRIEVAL_REPORT_PATH = ROOT / "data" / "evaluation" / "test_set_v1_chinese_aliases.retrieval.json"
FORBIDDEN_REQUEST_FIELDS = {"filters", "company_id", "fiscal_year", "document_id", "source_format", "report_type"}


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def load_contract_and_cases() -> tuple[dict[str, Any], list[dict[str, Any]]]:
    rows = _load_jsonl(DATASET_PATH)
    contracts = [row for row in rows if row.get("record_type") == "contract"]
    cases = [row for row in rows if row.get("record_type") == "case"]
    if len(contracts) != 1:
        raise ValueError("中文别名题集必须且只能包含一条契约")
    if not cases or len({str(case.get("case_id") or "") for case in cases}) != len(cases):
        raise ValueError("中文别名题集必须包含非空且唯一的 case_id")
    for case in cases:
        forbidden = FORBIDDEN_REQUEST_FIELDS.intersection(case)
        if forbidden:
            raise ValueError(f"中文别名用例不得携带显式过滤字段：{sorted(forbidden)}")
        if not str(case.get("query") or "").strip() or not str(case.get("expected_company_id") or "").strip():
            raise ValueError("中文别名用例必须声明 query 和 expected_company_id")
    return contracts[0], cases


def _normalized_number(value: Any) -> str:
    return "".join(character for character in str(value or "") if character.isdigit() or character in ".-%")


def _number_present(value: Any, text: str) -> bool:
    expected = _normalized_number(value)
    if not expected:
        return False
    normalized_text = str(text or "").replace(",", "").replace("，", "")
    return expected.replace(",", "") in normalized_text


def _post_query(*, api_url: str, query: str, generate: bool, timeout: float) -> dict[str, Any]:
    payload = json.dumps(
        {
            "query": query,
            "top_k": 5,
            "mode": "hybrid",
            "generate": generate,
        },
        ensure_ascii=False,
    ).encode("utf-8")
    request = Request(
        api_url,
        data=payload,
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    with urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def run(*, api_url: str, retrieval_only: bool, timeout: float) -> dict[str, Any]:
    contract, cases = load_contract_and_cases()
    rows: list[dict[str, Any]] = []
    for case in cases:
        row: dict[str, Any] = {
            "case_id": case["case_id"],
            "alias": case["alias"],
            "query": case["query"],
            "expected_company_id": case["expected_company_id"],
            "error": None,
        }
        try:
            response = _post_query(
                api_url=api_url,
                query=str(case["query"]),
                generate=not retrieval_only,
                timeout=timeout,
            )
            evidence = list(response.get("evidence") or [])
            evidence_text = "\n".join(str(item.get("text") or "") for item in evidence)
            answer = str(response.get("answer") or "")
            retrieval = dict(response.get("retrieval") or {})
            expected_company = str(case["expected_company_id"])
            evidence_companies = sorted(
                {
                    str((item.get("citation") or {}).get("company_id") or "")
                    for item in evidence
                    if str((item.get("citation") or {}).get("company_id") or "")
                }
            )
            evidence_families = {
                str((item.get("citation") or {}).get("statement_family") or "")
                for item in evidence
            }
            expected_values = list(case.get("expected_values") or [])
            required_families = set(case.get("required_evidence_families") or [])
            numeric_in_evidence = {
                value: _number_present(value, evidence_text)
                for value in expected_values
            }
            numeric_in_answer = {
                value: _number_present(value, answer)
                for value in expected_values
            }
            resolved_company = str((retrieval.get("resolved_filters") or {}).get("company_id") or "")
            company_filter_resolved = resolved_company == expected_company
            company_isolated = evidence_companies == [expected_company]
            family_coverage = required_families.issubset(evidence_families)
            layers: dict[str, bool] = {
                "company_filter_resolution": company_filter_resolved,
                "company_isolation": company_isolated,
                "retrieval_coverage": bool(
                    company_filter_resolved
                    and company_isolated
                    and evidence
                    and all(numeric_in_evidence.values())
                    and family_coverage
                ),
            }
            if not retrieval_only:
                layers.update(
                    {
                        "grounded_answer": bool(
                            response.get("answerable")
                            and response.get("citation_valid")
                            and response.get("citation_ids")
                            and all(numeric_in_answer.values())
                        ),
                        "answer_completeness": bool(
                            response.get("answerable")
                            and all(numeric_in_answer.values())
                        ),
                    }
                )
            row.update(
                {
                    "answer": None if retrieval_only else answer,
                    "evidence_count": len(evidence),
                    "evidence_companies": evidence_companies,
                    "resolved_filters": retrieval.get("resolved_filters") or {},
                    "numeric_in_evidence": numeric_in_evidence,
                    "numeric_in_answer": numeric_in_answer if not retrieval_only else {},
                    "family_coverage": family_coverage,
                    "layers": layers,
                    "planner": retrieval.get("planner") or {},
                    "evidence": evidence,
                }
            )
        except Exception as exc:
            row["error"] = f"{type(exc).__name__}: {exc}"
        rows.append(row)

    layer_names = ["company_filter_resolution", "company_isolation", "retrieval_coverage"]
    if not retrieval_only:
        layer_names.extend(["grounded_answer", "answer_completeness"])
    report = {
        "test_set": contract["test_set"],
        "mode": "retrieval_only" if retrieval_only else "full",
        "scope": contract["scope"],
        "request_policy": contract["request_policy"],
        "api_url": api_url,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "case_count": len(rows),
        "scores": {
            layer: {
                "passed_count": sum(bool((row.get("layers") or {}).get(layer)) for row in rows),
                "pass_rate": round(
                    sum(bool((row.get("layers") or {}).get(layer)) for row in rows) / max(len(rows), 1),
                    4,
                ),
            }
            for layer in layer_names
        },
        "rows": rows,
    }
    report_path = RETRIEVAL_REPORT_PATH if retrieval_only else FULL_REPORT_PATH
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({key: report[key] for key in ("test_set", "mode", "case_count", "scores")}, ensure_ascii=False, indent=2))
    print(f"report={report_path}")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="运行 V1 中文公司别名真实 HTTP 评测")
    parser.add_argument("--api-url", default="http://127.0.0.1:8001/api/rag/query")
    parser.add_argument("--retrieval-only", action="store_true")
    parser.add_argument("--timeout", type=float, default=180.0)
    args = parser.parse_args()
    run(api_url=args.api_url, retrieval_only=args.retrieval_only, timeout=args.timeout)
