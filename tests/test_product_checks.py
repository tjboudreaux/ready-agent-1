"""Product & Experimentation checks: two-part evidence, no SDK-only false positives."""
import unittest

from readiness.checks import product
from readiness.model import Status
from tests.test_checks import CheckCase


class TestAnalyticsInstrumentation(CheckCase):
    def test_pass_sdk_and_events(self):
        ctx = self.ctx({"package.json": '{"dependencies":{"@segment/analytics-node":"^1"}}',
                        "src/track.js": "analytics.track('Signup Completed', {});\n"})
        self.assertEqual(self.s(product.analytics_instrumentation(ctx)), Status.PASS)

    def test_pass_sdk_and_tracking_plan(self):
        ctx = self.ctx({"package.json": '{"dependencies":{"posthog-node":"^4"}}',
                        "tracking-plan.json": '{"events":["Signup"]}'})
        self.assertEqual(self.s(product.analytics_instrumentation(ctx)), Status.PASS)

    def test_fail_sdk_only(self):
        ctx = self.ctx({"package.json": '{"dependencies":{"@segment/analytics-node":"^1"}}',
                        "src/x.js": "const a = 1;\n"})
        self.assertEqual(self.s(product.analytics_instrumentation(ctx)), Status.FAIL)

    def test_fail_events_without_sdk(self):
        ctx = self.ctx({"src/track.js": "thing.track('x');\n"})
        self.assertEqual(self.s(product.analytics_instrumentation(ctx)), Status.FAIL)

    def test_fail_none(self):
        ctx = self.ctx({"src/x.js": "const a = 1;\n"})
        self.assertEqual(self.s(product.analytics_instrumentation(ctx)), Status.FAIL)


class TestFeatureFlags(CheckCase):
    def test_pass_provider_and_evaluation(self):
        ctx = self.ctx({"package.json": '{"dependencies":{"launchdarkly-node-server-sdk":"^7"}}',
                        "src/flags.js": "const on = client.variation('new-ui', user, false);\n"})
        self.assertEqual(self.s(product.feature_flags(ctx)), Status.PASS)

    def test_fail_provider_only(self):
        ctx = self.ctx({"package.json": '{"dependencies":{"launchdarkly-node-server-sdk":"^7"}}',
                        "src/x.js": "const a = 1;\n"})
        self.assertEqual(self.s(product.feature_flags(ctx)), Status.FAIL)

    def test_fail_evaluation_without_provider(self):
        ctx = self.ctx({"src/flags.js": "if (isEnabled('x')) {}\n"})
        self.assertEqual(self.s(product.feature_flags(ctx)), Status.FAIL)

    def test_fail_none(self):
        ctx = self.ctx({"src/x.js": "const a = 1;\n"})
        self.assertEqual(self.s(product.feature_flags(ctx)), Status.FAIL)


class TestExperimentConfig(CheckCase):
    def test_pass_owned_registry(self):
        ctx = self.ctx({"experiments.yml":
                        "checkout_color:\n  owner: growth\n  variants: [control, blue]\n  metric: conversion\n"})
        self.assertEqual(self.s(product.experiment_config(ctx)), Status.PASS)

    def test_fail_missing_fields(self):
        ctx = self.ctx({"experiments.yml": "checkout_color:\n  variants: [control, blue]\n"})
        self.assertEqual(self.s(product.experiment_config(ctx)), Status.FAIL)

    def test_fail_none(self):
        ctx = self.ctx({"README.md": "# x\n"})
        self.assertEqual(self.s(product.experiment_config(ctx)), Status.FAIL)


if __name__ == "__main__":
    unittest.main()
