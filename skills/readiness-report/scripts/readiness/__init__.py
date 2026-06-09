"""agent-readiness engine — pure-stdlib, deterministic readiness scoring.

The engine owns the canonical, reproducible gating score. The agent layer (in the skills)
only adds advisory commentary; it never changes the score this engine produces.
"""
from .version import ENGINE_VERSION, REGISTRY_VERSION, DETECTOR_VERSION, SCHEMA_VERSION

__all__ = ["ENGINE_VERSION", "REGISTRY_VERSION", "DETECTOR_VERSION", "SCHEMA_VERSION"]
