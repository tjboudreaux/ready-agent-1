"""Debugging & Observability checks — advisory only.

Each criterion requires TWO-part evidence: a configuration/dependency signal AND a wiring/usage
signal in the repository. An import or a dependency on its own never passes — a package being
present does not make a system observable. RA1 verifies configuration evidence, not the runtime
quality of the telemetry.
"""
from __future__ import annotations

from ._helpers import adep, aglob, agrep, ev, failed, passed


def _two_part(kind, cfg, wiring):
    """Pass only when both a configuration signal and a wiring/usage signal are present."""
    if cfg and wiring:
        return passed(f"{kind}: configured and wired into the application.",
                      [ev(f"{kind} config", source=str(cfg)), ev(f"{kind} wiring", source=str(wiring))])
    if not cfg and not wiring:
        return failed(f"No {kind}: neither configuration nor usage found.")
    if not cfg:
        return failed(f"{kind} referenced in code but not configured (no library/config).")
    return failed(f"{kind} dependency/config present but not wired into the application (import-only).")


_LOG_DEPS = ["structlog", "loguru", "pino", "winston", "bunyan", "zap", "go.uber.org/zap",
             "zerolog", "serilog"]
_LOG_CONFIG = ["logback.xml", "log4j2.xml", "logging.conf", "logging.ini", "**/logback.xml"]
_LOG_WIRING = [r"get_logger|getLogger|create_logger|createLogger",
               r"structlog\.(get_logger|configure)|winston\.createLogger|pino\(|zap\.New|"
               r"zerolog\.New|LoggerFactory\.getLogger|loguru"]


def structured_logging(ctx):
    cfg = adep(ctx, _LOG_DEPS) or aglob(ctx, _LOG_CONFIG)
    return _two_part("Structured logging", cfg, agrep(ctx, _LOG_WIRING))


_TRACE_DEPS = ["opentelemetry", "opentelemetry-sdk", "@opentelemetry/sdk-node",
               "@opentelemetry/api", "go.opentelemetry.io/otel", "jaeger-client", "zipkin",
               "ddtrace", "elastic-apm-node"]
_TRACE_CONFIG = ["otel-collector-config.y*ml", "**/otel-collector*.y*ml", "otel.config.*"]
_TRACE_WIRING = [r"get_tracer|TracerProvider|start_as_current_span|start_span|startSpan|"
                 r"otel\.Tracer|@WithSpan|register_tracer|trace\.set_tracer_provider"]


def tracing(ctx):
    cfg = adep(ctx, _TRACE_DEPS) or aglob(ctx, _TRACE_CONFIG)
    return _two_part("Distributed tracing", cfg, agrep(ctx, _TRACE_WIRING))


_METRIC_DEPS = ["prometheus-client", "prom-client", "micrometer", "go.opentelemetry.io/otel/metric",
                "github.com/prometheus/client_golang", "statsd", "datadog", "opentelemetry-exporter-prometheus"]
_METRIC_CONFIG = ["prometheus.y*ml", "**/prometheus.y*ml", "**/servicemonitor*.y*ml"]
_METRIC_WIRING = [r"Counter\(|Histogram\(|Gauge\(|Summary\(|/metrics|make_asgi_app|"
                  r"MeterProvider|meter\.|prometheus\.|promhttp|registry\.(register|MustRegister)"]


def metrics(ctx):
    cfg = adep(ctx, _METRIC_DEPS) or aglob(ctx, _METRIC_CONFIG)
    return _two_part("Metrics", cfg, agrep(ctx, _METRIC_WIRING))


_HEALTH_SOURCE = [r"/healthz|/livez|/readyz|/health\b|/ready\b|/_health|HealthCheck|health_check"]
_HEALTH_PLATFORM = ["**/*.y*ml", "**/*.yaml"]


def health_endpoints(ctx):
    """A health/readiness endpoint must exist in code or as a platform probe — a README mention
    of "health checks" is not evidence."""
    route = agrep(ctx, _HEALTH_SOURCE)
    if route:
        return passed("Health/readiness endpoint exposed in application code.",
                      [ev("health route", source=str(route))])
    for wf in ctx.static.glob(_HEALTH_PLATFORM):
        low = (ctx.static.read(wf) or "").lower()
        if "livenessprobe" in low or "readinessprobe" in low:
            return passed("Health/readiness probe declared in platform config.",
                          [ev("k8s probe", source=wf)])
    return failed("No health/readiness endpoint in code or platform probe (a doc mention is not enough).")


_ALERT_FILES = ["**/*.rules.y*ml", "**/alerts*.y*ml", "**/alertmanager.y*ml", "**/*alert*.y*ml",
                "**/monitors/*.json", "**/*alerts*.tf"]


def alerting_rules(ctx):
    """Alert rules must declare service ownership (owner/team/service/runbook); an unowned alert or
    a dashboard alone does not pass."""
    for f in ctx.static.glob(_ALERT_FILES):
        low = (ctx.static.read(f) or "").lower()
        if "alert" in low and any(k in low for k in ("owner", "team", "service", "runbook")):
            return passed("Alerting rules with service ownership.", [ev("alert rules", source=f)])
    return failed("No owned alerting rules (alert config with owner/team/service/runbook).")


_DASH_FILES = ["**/dashboards/*.json", "**/*dashboard*.json", "**/*dashboard*.tf", "grafana/**/*.json"]


def dashboards_as_code(ctx):
    """Dashboards-as-code must be a real definition (panels + metric targets), not a screenshot or
    a doc describing dashboards."""
    for f in ctx.static.glob(_DASH_FILES):
        low = (ctx.static.read(f) or "").lower()
        if "panels" in low and any(k in low for k in ("targets", "expr", "datasource", "query")):
            return passed("Dashboards defined as code with metric targets.", [ev("dashboard", source=f)])
        if "grafana_dashboard" in low:
            return passed("Dashboard provisioned as code (Terraform).", [ev("dashboard tf", source=f)])
    return failed("No dashboards-as-code with metric targets (screenshots/docs do not count).")
