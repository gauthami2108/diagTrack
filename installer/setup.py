"""
DiagTrack setup. Started by Setup.cmd on Windows, or by `docker compose run --rm setup` in Docker.
The work is done by the diagtrack_setup package next to this file.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from diagtrack_setup.main import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
