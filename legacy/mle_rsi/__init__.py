"""MLE task adapters and experiment orchestration for pinned Dream-RSI."""
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
_default_workspace = "/mnt/afs/yangsen1" if Path("/mnt/afs/yangsen1").is_dir() else str(REPO / "workspace")
WORKSPACE = Path(os.environ.get("RSI_EVAL_WORKSPACE", _default_workspace)).resolve()
os.environ["RSI_EVAL_WORKSPACE"] = str(WORKSPACE)
sys.path[:0] = [str(REPO / "third_party/dream-rsi/my_rsi/src"), str(REPO / "third_party/rsi-eval/src")]
