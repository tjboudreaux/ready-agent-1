"""Observability checks: two-part evidence, no import-only / doc-only false positives."""
import unittest

from readiness.checks import observability
from readiness.checks._helpers import agrep
from readiness.model import Status
from tests.test_checks import CheckCase


class TestStructuredLogging(CheckCase):
    def test_pass_config_and_wiring(self):
        ctx = self.ctx({"pyproject.toml": '[project]\nname="s"\ndependencies=["structlog"]\n',
                        "app/main.py": "import structlog\nlog = structlog.get_logger()\nlog.info('up')\n"})
        self.assertEqual(self.s(observability.structured_logging(ctx)), Status.PASS)

    def test_fail_import_only(self):
        ctx = self.ctx({"pyproject.toml": '[project]\nname="s"\ndependencies=["structlog"]\n',
                        "app/main.py": "x = 1\n"})
        self.assertEqual(self.s(observability.structured_logging(ctx)), Status.FAIL)

    def test_fail_wiring_without_config(self):
        ctx = self.ctx({"app/main.py": "logger = getLogger()\n"})
        self.assertEqual(self.s(observability.structured_logging(ctx)), Status.FAIL)

    def test_fail_print_only(self):
        ctx = self.ctx({"app/main.py": "print('hi')\n"})
        self.assertEqual(self.s(observability.structured_logging(ctx)), Status.FAIL)


class TestTracing(CheckCase):
    def test_pass(self):
        ctx = self.ctx({"pyproject.toml": '[project]\nname="s"\ndependencies=["opentelemetry-sdk"]\n',
                        "app/trace.py": "from opentelemetry import trace\nt = trace.get_tracer(__name__)\n"})
        self.assertEqual(self.s(observability.tracing(ctx)), Status.PASS)

    def test_fail_import_only(self):
        ctx = self.ctx({"pyproject.toml": '[project]\nname="s"\ndependencies=["opentelemetry-sdk"]\n',
                        "app/x.py": "y = 2\n"})
        self.assertEqual(self.s(observability.tracing(ctx)), Status.FAIL)


class TestMetrics(CheckCase):
    def test_pass(self):
        ctx = self.ctx({"pyproject.toml": '[project]\nname="s"\ndependencies=["prometheus-client"]\n',
                        "app/m.py": "from prometheus_client import Counter\nc = Counter('x','d')\n"})
        self.assertEqual(self.s(observability.metrics(ctx)), Status.PASS)

    def test_fail_dependency_only(self):
        ctx = self.ctx({"pyproject.toml": '[project]\nname="s"\ndependencies=["prometheus-client"]\n',
                        "app/x.py": "z = 3\n"})
        self.assertEqual(self.s(observability.metrics(ctx)), Status.FAIL)


class TestHealthEndpoints(CheckCase):
    def test_pass_code_route(self):
        ctx = self.ctx({"app/api.py": "@app.get('/healthz')\ndef health(): return 'ok'\n"})
        self.assertEqual(self.s(observability.health_endpoints(ctx)), Status.PASS)

    def test_pass_k8s_probe(self):
        ctx = self.ctx({"k8s/deploy.yaml": "spec:\n  containers:\n  - livenessProbe:\n      httpGet:\n        path: /\n"})
        self.assertEqual(self.s(observability.health_endpoints(ctx)), Status.PASS)

    def test_fail_doc_mention_only(self):
        ctx = self.ctx({"README.md": "# x\n\nWe run health checks.\n"})
        self.assertEqual(self.s(observability.health_endpoints(ctx)), Status.FAIL)


class TestAlertingRules(CheckCase):
    def test_pass_owned(self):
        ctx = self.ctx({"alerts.yml": "groups:\n- name: a\n  rules:\n  - alert: HighErrors\n    team: payments\n"})
        self.assertEqual(self.s(observability.alerting_rules(ctx)), Status.PASS)

    def test_fail_unowned(self):
        ctx = self.ctx({"alerts.yml": "groups:\n- name: a\n  rules:\n  - alert: HighErrors\n"})
        self.assertEqual(self.s(observability.alerting_rules(ctx)), Status.FAIL)

    def test_fail_none(self):
        ctx = self.ctx({"README.md": "# x\n"})
        self.assertEqual(self.s(observability.alerting_rules(ctx)), Status.FAIL)


class TestDashboardsAsCode(CheckCase):
    def test_pass_grafana_json(self):
        ctx = self.ctx({"dashboards/svc.json": '{"panels":[{"targets":[{"expr":"rate(x[5m])"}]}]}'})
        self.assertEqual(self.s(observability.dashboards_as_code(ctx)), Status.PASS)

    def test_pass_terraform(self):
        ctx = self.ctx({"infra/dashboard.tf": 'resource "grafana_dashboard" "x" {}\n'})
        self.assertEqual(self.s(observability.dashboards_as_code(ctx)), Status.PASS)

    def test_fail_panels_without_targets(self):
        ctx = self.ctx({"dashboards/svc.json": '{"panels":[{"title":"cpu"}]}'})
        self.assertEqual(self.s(observability.dashboards_as_code(ctx)), Status.FAIL)

    def test_fail_none(self):
        ctx = self.ctx({"README.md": "# x\n"})
        self.assertEqual(self.s(observability.dashboards_as_code(ctx)), Status.FAIL)


class TestAgrepFallback(CheckCase):
    def test_root_fallback_for_monorepo_app(self):
        ctx = self.ctx({"package.json": '{"name":"root","workspaces":["packages/*"]}',
                        "packages/a/package.json": '{"name":"a"}',
                        "packages/b/package.json": '{"name":"b"}',
                        "shared.py": "import structlog\nstructlog.get_logger()\n"},
                       app_path="packages/a")
        self.assertIsNotNone(agrep(ctx, [r"get_logger"]))


if __name__ == "__main__":
    unittest.main()
