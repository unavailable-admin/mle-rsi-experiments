"""Official MLE CLI with legacy pandas string semantics for its unchanged graders.

Pandas 3 defaults to StringDtype, whose astype(str) retains missing values as
NaN. The official text-normalization graders expect the pandas 2 behavior
where astype(str) yields string values before sklearn accuracy_score.
This process-local option leaves the environment and official code unchanged.
"""
import sys
import pandas as pd
from mlebench.cli import main

pd.options.future.infer_string=False
if __name__=='__main__':sys.exit(main())
