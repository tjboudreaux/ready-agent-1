"""Version stamps embedded in every report.

These are how stale state is detected: a report (and the cached `.agents/readiness`
state used for advisory grounding) carries these versions, and anything produced by a
different engine/registry/detector version is re-evaluated rather than trusted.
"""

ENGINE_VERSION = "0.1.0"
REGISTRY_VERSION = "0.1.0"
DETECTOR_VERSION = "0.2.0"
SCHEMA_VERSION = "1"


def version_stamp() -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "engine_version": ENGINE_VERSION,
        "registry_version": REGISTRY_VERSION,
        "detector_version": DETECTOR_VERSION,
    }
