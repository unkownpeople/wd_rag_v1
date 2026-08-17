from __future__ import annotations

import csv
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "data" / "tables_preview"
CONFIGS = {
    "finqa": ROOT / "data" / "tables" / "finqa" / "train.json",
    "tatqa": ROOT / "data" / "tables" / "tatqa" / "train.json",
}


def table_rows(item: dict) -> list[list[object]]:
    table = item.get("table") or item.get("table_text") or item.get("table_data") or []
    if isinstance(table, dict):
        table = table.get("table", [])
    if not isinstance(table, list) or not table:
        return []
    if isinstance(table[0], dict):
        keys = list(table[0])
        return [keys, *[[row.get(key, "") for key in keys] for row in table]]
    return [list(row) for row in table]


def main() -> None:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    records: list[dict[str, object]] = []
    for name, source in CONFIGS.items():
        dataset = json.loads(source.read_text(encoding="utf-8"))
        found = 0
        for source_index, item in enumerate(dataset):
            rows = table_rows(item)
            if not rows:
                continue
            target = OUTPUT / f"{name}_table_{found + 1:02d}.csv"
            with target.open("w", encoding="utf-8-sig", newline="") as file:
                csv.writer(file).writerows(rows)
            records.append(
                {
                    "dataset": name,
                    "source_index": source_index,
                    "csv": target.relative_to(ROOT).as_posix(),
                    "rows": len(rows),
                    "columns": max(len(row) for row in rows),
                }
            )
            found += 1
            if found == 3:
                break
        print(f"{name}: exported {found} table previews")
    (OUTPUT / "index.json").write_text(
        json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"total: {len(records)}")


if __name__ == "__main__":
    main()
