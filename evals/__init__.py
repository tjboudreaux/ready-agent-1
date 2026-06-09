"""Evals for the ra1-report / ra1-fix agent contracts.

- ``contracts``: deterministic invariants the agent output MUST satisfy (score immutability, etc.).
- ``judge``:     LLM-as-judge for prose fabrication/grounding (pluggable model_fn).
- ``runner``:    runs scenarios through a model_fn, applies contracts + judge, reports pass/fail.
- ``scenarios``: positive + adversarial scenario fixtures.

Unit tests exercise the contract/judge logic with mock model functions (deterministic, no network).
A live run (`python3 evals/runner.py`) drives a real model (gemini/codex).
"""
