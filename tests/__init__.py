"""Test package. Puts the engine on sys.path so `import readiness` works under unittest."""
import sys
from pathlib import Path

_ENGINE = Path(__file__).resolve().parent.parent / "engine"
if str(_ENGINE) not in sys.path:
    sys.path.insert(0, str(_ENGINE))
