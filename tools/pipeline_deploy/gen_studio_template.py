"""Regenerate `<repo>/studio_config.template.json` from
`PipelineConfig.default_template()` -- the code default, not a hand-maintained
JSON file. Run this after any change to `DEFAULT_PROJECT_CONFIG` or the
studio-level defaults in `square_core/config/pipeline.py`.

    python -m tools.pipeline_deploy.gen_studio_template

`tests/test_studio_template.py` asserts the checked-in file stays in sync.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

repo_root = Path(__file__).resolve().parents[2]
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))


def main() -> None:
    from square_core.config.pipeline import default_template

    p = repo_root / "studio_config.template.json"
    p.write_text(json.dumps(default_template(), indent=4) + "\n", encoding="utf-8")
    print(f"wrote {p}")


if __name__ == "__main__":
    main()
