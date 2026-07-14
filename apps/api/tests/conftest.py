from __future__ import annotations

import os
from pathlib import Path


# Test runs never load a developer's ignored root .env or make live model calls.
os.environ["IM_ONE_ENV_FILE"] = str(Path(__file__).with_name(".env.test"))
for key in ("OPENAI_API_KEY", "IM_ONE_API_TOKEN", "IM_ONE_TRUSTED_PROXY_TOKEN"):
    os.environ.pop(key, None)
