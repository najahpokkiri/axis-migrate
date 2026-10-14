"""`python -m axis_pipeline` / console script `axis-pipeline` (vendored, byte-identical; CONTRACTS §5).

Runner mode:  python -m axis_pipeline --step <id> --ctx runs/<id>/ctx/<step>.json
Wheel mode:   python -m axis_pipeline --step <id> --input-root R --work-root W --output-root O --scratch-dir S
                                      [--no-seek-root P]...
Exit codes: 0 ok, 1 step exception, 2 input/parameter error, 3 output contract violation.
"""
import sys

from ._axis_runtime import main

if __name__ == "__main__":
    sys.exit(main())
