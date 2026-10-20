import contextlib
import importlib.machinery
import importlib.util
import io
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import Mock, patch


ROOT = Path(__file__).resolve().parents[1]
LOADER = importlib.machinery.SourceFileLoader("cleaner", str(ROOT / "rc-clean-test-users"))
SPEC = importlib.util.spec_from_loader(LOADER.name, LOADER)
cleaner = importlib.util.module_from_spec(SPEC)
LOADER.exec_module(cleaner)


class ClassificationTests(unittest.TestCase):
    def setUp(self):
        self.config = {"app": {"rc": "test", "name": "Test", "test_store_only": True}}
        self.rc = Mock()
        self.rc.enumerate_customers.return_value = ["test", "buyer", "unknown"]
        customers = {
            "test": {"last_seen_mobile_app": {"type": "test_store"}},
            "buyer": {"last_seen_mobile_app": {"type": "test_store"},
                      "history": [{"product": "paid", "is_sandbox": False}]},
            "unknown": {"last_seen_app_version": "future"},
        }
        self.rc.customer.side_effect = lambda project, user: customers[user]
        self.rc.delete_customer.return_value = True
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.object(cleaner, "APPS", self.config))
        self.stack.enter_context(patch.object(cleaner.time, "sleep"))
        self.stack.enter_context(contextlib.redirect_stdout(io.StringIO()))

    def test_test_store_only_preserves_buyer_and_unknown_without_asc(self):
        with patch.object(cleaner, "asc_inventory", side_effect=AssertionError("ASC read")):
            self.assertEqual(cleaner.clean_app(self.rc, "app", True), 1)
        self.rc.delete_customer.assert_called_once_with("test", "test")

    def test_rejected_ios_deletion_fails(self):
        self.config["app"] = {"rc": "test", "asc": "apple"}
        self.rc.delete_customer.return_value = False
        inventory = {"released": {"1"}, "pending": set(), "tf_only": set(),
                     "store": [], "tf": []}
        self.rc.enumerate_customers.return_value = ["test"]
        with patch.object(cleaner, "asc_inventory", return_value=inventory):
            with self.assertRaisesRegex(RuntimeError, "rejected 1"):
                cleaner.clean_app(self.rc, "app", True)

    def test_dry_run_never_deletes(self):
        self.assertEqual(cleaner.clean_app(self.rc, "app", False), 1)
        self.rc.delete_customer.assert_not_called()

    def test_unconfigured_project_fails_coverage(self):
        self.rc.get.return_value.json.return_value = {
            "apps": [{"id": "test", "name": "Configured"},
                     {"id": "missing", "name": "New project"}]}
        self.assertFalse(cleaner.check_coverage(self.rc, self.config))

    def test_complete_coverage_succeeds(self):
        self.rc.get.return_value.json.return_value = {"apps": [{"id": "test", "name": "Test"}]}
        self.assertTrue(cleaner.check_coverage(self.rc, self.config))

    def test_empty_inventory_fails(self):
        self.rc.get.return_value.json.return_value = {"apps": []}
        with self.assertRaisesRegex(RuntimeError, "empty"):
            cleaner.check_coverage(self.rc, self.config)

    def test_all_apps_cleans_known_projects_but_fails_for_missing_coverage(self):
        self.rc.get.return_value.json.return_value = {
            "apps": [{"id": "missing", "name": "New project"}]}
        with patch.object(cleaner, "load_apps", return_value=self.config), \
                patch.object(cleaner, "RC_API", return_value=self.rc), \
                patch.object(cleaner, "clean_bundle", return_value=0) as clean, \
                patch.object(sys, "argv", ["rc-clean", "--all-apps"]):
            self.assertEqual(cleaner.main(), 1)
        clean.assert_called_once()


class AppleRetryTests(unittest.TestCase):
    def test_transient_tls_error_retries(self):
        response = Mock()
        response.json.return_value = {"data": []}
        with patch.object(cleaner.requests, "get", side_effect=[
                cleaner.requests.exceptions.SSLError("temporary EOF"), response]) as get, \
                patch.object(cleaner.time, "sleep"):
            self.assertEqual(cleaner._asc_get("/v1/apps", {}), {"data": []})
        self.assertEqual(get.call_count, 2)

    def test_persistent_connection_failure_is_bounded(self):
        with patch.object(cleaner.requests, "get", side_effect=
                          cleaner.requests.exceptions.ConnectionError("offline")) as get, \
                patch.object(cleaner.time, "sleep"):
            with self.assertRaises(cleaner.requests.exceptions.ConnectionError):
                cleaner._asc_get("/v1/apps", {})
        self.assertEqual(get.call_count, 4)

    def test_auth_error_is_not_retried(self):
        response = Mock(status_code=403)
        response.raise_for_status.side_effect = cleaner.requests.exceptions.HTTPError("forbidden")
        with patch.object(cleaner.requests, "get", return_value=response) as get:
            with self.assertRaises(cleaner.requests.exceptions.HTTPError):
                cleaner._asc_get("/v1/apps", {})
        self.assertEqual(get.call_count, 1)

    def test_transient_server_error_retries(self):
        unavailable = Mock(status_code=503)
        unavailable.raise_for_status.side_effect = cleaner.requests.exceptions.HTTPError("unavailable")
        response = Mock()
        response.json.return_value = {"data": []}
        with patch.object(cleaner.requests, "get", side_effect=[unavailable, response]) as get, \
                patch.object(cleaner.time, "sleep"):
            self.assertEqual(cleaner._asc_get("/v1/apps", {}), {"data": []})
        self.assertEqual(get.call_count, 2)


class RunnerTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name)
        self.stub = self.path / "cleaner.py"
        self.log = self.path / "run.log"
        self.state = self.path / "state"
        self.args = [sys.executable, str(ROOT / "scripts/rc-clean-runner.py"),
                     "--script", str(self.stub), "--state-dir", str(self.state),
                     "--log", str(self.log), "--timeout", "5"]

    def run_runner(self, code):
        self.stub.write_text(code)
        return subprocess.run(self.args, capture_output=True, text=True, timeout=10)

    def test_success_advances_stamp_and_followup_uses_recent_window(self):
        (self.state / "run.lock").mkdir(parents=True)
        result = self.run_runner("import sys\nprint(sys.argv[1:])\n")
        self.assertEqual(result.returncode, 0)
        self.assertTrue((self.state / "last-full-sweep").exists())
        self.assertTrue((self.state / "last-success").exists())
        self.assertEqual(subprocess.run(self.args, capture_output=True, timeout=10).returncode, 0)
        self.assertIn("'--since-days', '3'", self.log.read_text())

    def test_failure_does_not_advance_success(self):
        result = self.run_runner("raise SystemExit(1)\n")
        self.assertEqual(result.returncode, 1)
        self.assertFalse((self.state / "last-full-sweep").exists())
        self.assertFalse((self.state / "last-success").exists())
        self.assertIn("exit 1", self.log.read_text())

    def test_timeout_kills_child_and_releases_lock(self):
        self.stub.write_text("import time\ntime.sleep(10)\n")
        result = subprocess.run(self.args[:-1] + ["0.1"], capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 124)
        self.assertFalse((self.state / "last-full-sweep").exists())
        self.assertEqual(self.run_runner("pass\n").returncode, 0)

    def test_concurrent_run_skips_and_crashed_owner_recovers(self):
        marker = self.path / "started"
        self.stub.write_text(f"from pathlib import Path\nimport time\nPath({str(marker)!r}).touch()\ntime.sleep(0.8)\n")
        first = subprocess.Popen(self.args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.addCleanup(lambda: first.poll() is None and first.kill())
        deadline = time.monotonic() + 5
        while not marker.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertTrue(marker.exists())
        first.send_signal(signal.SIGKILL)
        first.wait(timeout=5)
        result = subprocess.run(self.args, capture_output=True, text=True, timeout=5)
        self.assertIn("another run is active", result.stdout)
        time.sleep(1)
        self.assertEqual(self.run_runner("pass\n").returncode, 0)


if __name__ == "__main__":
    unittest.main()
