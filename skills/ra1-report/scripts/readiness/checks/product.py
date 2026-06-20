"""Product & Experimentation checks — advisory only.

Two-part evidence, like observability: an SDK/provider/config signal AND a usage/wiring signal.
An analytics or feature-flag SDK in the dependency list does not prove the product is instrumented.
"""
from __future__ import annotations

from ._helpers import adep, aglob, agrep, ev, failed, passed


_ANALYTICS_DEPS = ["segment", "@segment/analytics-node", "analytics-node", "amplitude",
                   "@amplitude/analytics-node", "mixpanel", "posthog", "posthog-node",
                   "rudder-sdk-js", "@rudderstack/rudder-sdk-node"]
_ANALYTICS_PLAN = ["tracking-plan.json", "tracking_plan.y*ml", "events.y*ml", "analytics/events.*"]
_ANALYTICS_WIRING = [r"analytics\.track\(|\.track\(|\.capture\(|logEvent\(|trackEvent\(|"
                     r"posthog\.capture|mixpanel\.track|amplitude\.(track|logEvent)"]


def analytics_instrumentation(ctx):
    cfg = adep(ctx, _ANALYTICS_DEPS)
    wiring = agrep(ctx, _ANALYTICS_WIRING) or aglob(ctx, _ANALYTICS_PLAN)
    if cfg and wiring:
        return passed("Analytics instrumented: SDK plus named events / tracking plan.",
                      [ev("analytics SDK", source=str(cfg)), ev("events/plan", source=str(wiring))])
    if not cfg and not wiring:
        return failed("No analytics instrumentation (SDK + named events / tracking plan).")
    if not cfg:
        return failed("Event tracking referenced but no analytics SDK configured.")
    return failed("Analytics SDK present but no named events or tracking plan (dependency-only).")


_FLAG_DEPS = ["launchdarkly-node-server-sdk", "launchdarkly", "unleash-client", "flagsmith",
              "@splitsoftware/splitio", "configcat-node", "@openfeature/server-sdk", "ldclient"]
_FLAG_CONFIG = ["flags.y*ml", "feature-flags.y*ml", ".flagsmith", "unleash*.json"]
_FLAG_WIRING = [r"\.variation\(|isEnabled\(|is_enabled\(|getFlag|get_flag|booleanValue\(|"
                r"getBooleanValue|client\.variation|evaluate(Flag|Boolean)?\("]


def feature_flags(ctx):
    cfg = adep(ctx, _FLAG_DEPS) or aglob(ctx, _FLAG_CONFIG)
    wiring = agrep(ctx, _FLAG_WIRING)
    if cfg and wiring:
        return passed("Feature flags: provider/config plus in-code flag evaluation.",
                      [ev("flag provider/config", source=str(cfg)), ev("flag evaluation", source=str(wiring))])
    if not cfg and not wiring:
        return failed("No feature-flag tooling (provider/config + in-code evaluation).")
    if not cfg:
        return failed("Flag evaluation referenced but no provider/config configured.")
    return failed("Feature-flag provider/config present but no in-code evaluation (docs/config only).")


_EXPERIMENT_FILES = ["experiments.y*ml", "experiments.json", "**/experiments/*.y*ml",
                     "**/experiments/*.json", ".optimizely/**", "experiment*.y*ml"]


def experiment_config(ctx):
    """An experiment registry must name an owner, variants, and a success metric; analytics events
    without experiment ownership do not pass."""
    for f in ctx.static.glob(_EXPERIMENT_FILES):
        low = (ctx.static.read(f) or "").lower()
        if "variant" in low and any(k in low for k in ("metric", "success", "goal")) \
                and any(k in low for k in ("owner", "team")):
            return passed("Experiment registry with owner, variants, and a success metric.",
                          [ev("experiment config", source=f)])
    return failed("No owned experiment registry (variants + success metric + owner).")
