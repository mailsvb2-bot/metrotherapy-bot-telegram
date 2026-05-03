import os
import sys
from pathlib import Path

# Pytest collection on a production server must not inherit APP_ENV=prod.
# Runtime boot should fail fast in prod when secrets are missing, but unit tests
# import modules at collection time and need a hermetic dev environment.
# Use assignment, not setdefault: on the prod server APP_ENV is already set.
os.environ["APP_ENV"] = "dev"

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
