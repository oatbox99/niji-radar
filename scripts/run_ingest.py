"""Cron entrypoint: one ingestion cycle. Wire to launchd/cron hourly (daily for JMA).

  python -m scripts.run_ingest              # full cycle (needs GEMINI_API_KEY)
  python -m scripts.run_ingest --no-semantic
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import data_ingestion  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser(description="niji_gunma data ingestion")
    ap.add_argument("--no-semantic", action="store_true",
                    help="skip the LLM blog-labeling path (no GEMINI_API_KEY needed)")
    args = ap.parse_args()
    data_ingestion.run(with_semantic=not args.no_semantic)


if __name__ == "__main__":
    main()
