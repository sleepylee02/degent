"""Official replay entrypoint.

The historical batch-file demo runner was replaced by trace-clock replay.
Keep this module path stable so existing commands can continue to use:

    python -m model.stream.replay_pipeline
"""

from __future__ import annotations

from model.stream.trace_replay import main


if __name__ == "__main__":
    main()
