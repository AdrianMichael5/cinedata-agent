"""Evaluate the agent on eval/gabarito.json: python eval/run_eval.py --help."""

import sys
from pathlib import Path

from cinedata_agent.evaluation.main import main

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:], eval_dir=Path(__file__).resolve().parent))
