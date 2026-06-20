# Signal Schema

Loop signals are structured records consumed by humans or loop tooling. RA1 checks that a schema is documented; it does not validate emitted runtime events.

## Minimal schema

```json
{
  "schema_version": "1",
  "signal": "loop.run.completed",
  "source": "loop-runner",
  "timestamp": "2026-01-01T00:00:00Z",
  "severity": "info",
  "evidence": ["loop-runs/20260101-000000-example/README.md"],
  "decision": "continue",
  "owner": "repo-maintainers"
}
```

Fields may be extended, but producers and consumers should preserve these names.
