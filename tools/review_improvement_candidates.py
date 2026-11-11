"""Report queue admission reasons without modifying any candidate or failure."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

from tools.supervised_development_runtime import GovernorCandidateSource


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True, help="Explicit review snapshot")
    parser.add_argument("--source-root", type=Path, required=True, help="Clean source checkout")
    args = parser.parse_args()
    reader = GovernorCandidateSource(args.database, args.source_root)
    try:
        reports = reader.review_queue()
        # No conversation text, tool payloads or secrets in the review inventory.
        fields = {
            "candidate_id",
            "failure_id",
            "classification",
            "eligible",
            "bound_source_sha",
            "current_source_sha",
        }
        print(
            json.dumps(
                {
                    "counts": dict(Counter(item["classification"] for item in reports)),
                    "candidates": [
                        {key: value for key, value in item.items() if key in fields}
                        for item in reports
                    ],
                },
                indent=2,
                sort_keys=True,
            )
        )
    finally:
        reader.close()


if __name__ == "__main__":
    main()
