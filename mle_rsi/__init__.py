"""MLE task adapters and experiment orchestration for pinned Dream-RSI."""
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
WORKSPACE = Path(os.environ.get("RSI_EVAL_WORKSPACE", "/mnt/afs/yangsen1")).resolve()
os.environ["RSI_EVAL_WORKSPACE"] = str(WORKSPACE)
sys.path[:0] = [str(REPO / "third_party/dream-rsi/my_rsi/src"), str(REPO / "third_party/rsi-eval/src")]
