import hashlib
import json
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read_json(path):
    return json.loads(Path(path).read_text())


def run_python(args, check=True):
    result = subprocess.run(
        [sys.executable, *args], cwd=ROOT, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=20,
    )
    if check and result.returncode:
        raise AssertionError(f"Команда завершилась с {result.returncode}:\n{result.stdout}")
    return result


def init_workspace(root):
    run_python(["lab.py", "--workspace", str(root), "init"])


def run_lab(root, *args, check=True):
    return run_python(["lab.py", "--workspace", str(root), "run", *args], check=check)


def request(port, method, path, payload=None):
    body = None if payload is None else json.dumps(payload).encode()
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}", data=body, method=method,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=3) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read())


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class Server:
    def __init__(self, workspace, warmup=0.05):
        self.workspace = workspace
        self.warmup = warmup
        self.port = free_port()
        self.log = tempfile.TemporaryFile()
        self.process = subprocess.Popen(
            [sys.executable, "service.py", "--workspace", str(workspace),
             "--port", str(self.port), "--warmup-seconds", str(warmup)],
            cwd=ROOT, stdout=self.log, stderr=subprocess.STDOUT,
        )
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                self.log.seek(0)
                raise AssertionError(self.log.read().decode())
            try:
                status, _ = request(self.port, "GET", "/health")
                if status == 200:
                    return
            except (OSError, ValueError):
                time.sleep(0.02)
        raise AssertionError("HTTP-сервер не запустился")

    def stop(self):
        self.process.terminate()
        try:
            self.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait(timeout=5)
        self.log.close()


class IntegrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "workspace"
        init_workspace(self.root)
        self.servers = []
        self.addCleanup(self.stop_servers)

    def stop_servers(self):
        for server in self.servers:
            server.stop()

    def start_server(self, warmup=0.05, wait_ready=True):
        server = Server(self.root, warmup)
        self.servers.append(server)
        if wait_ready:
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                status, _ = request(server.port, "GET", "/ready")
                if status == 200:
                    break
                time.sleep(0.02)
            self.assertEqual(status, 200)
        return server

    def test_recovery_and_input_invalidation_across_processes(self):
        failed = run_lab(self.root, "--fail-after-prepare", check=False)
        self.assertNotEqual(failed.returncode, 0)
        self.assertIn("PREPARED", failed.stdout)
        recovered = run_lab(self.root)
        self.assertIn("CACHE_HIT", recovered.stdout)

        for label, mutate in [
            ("input bytes", lambda: (self.root / "input.txt").write_text("Другие байты\n")),
            ("tokenizer", lambda: self.change_config("tokenizer_revision", "r2")),
            ("template", lambda: self.change_config("template_sha256", "f" * 64)),
            ("max length", lambda: self.change_config("max_length", 256)),
            ("code revision", lambda: self.change_config("code_revision", "prepare-v2")),
        ]:
            with self.subTest(change=label):
                mutate()
                result = run_lab(self.root)
                self.assertIn("PREPARED", result.stdout)
                self.assertNotIn("CACHE_HIT", result.stdout)

    def change_config(self, key, value):
        path = self.root / "config.json"
        config = read_json(path)
        config[key] = value
        path.write_text(json.dumps(config))

    def test_invalid_reports_rejected_without_pointer_change(self):
        original = (self.root / "accepted.json").read_bytes()
        original_hash = hashlib.sha256(original).hexdigest()
        cases = {}
        incomplete = read_json(self.root / "report-incomplete.json")
        cases["incomplete"] = incomplete
        rejected = read_json(self.root / "report-reject.json")
        cases["rejected"] = rejected
        foreign = read_json(self.root / "report-B.json")
        foreign["model_id"] = "fixture-A"
        cases["foreign model"] = foreign
        invalid_metric = read_json(self.root / "report-B.json")
        invalid_metric["metrics"]["accuracy"] = float("nan")
        cases["NaN"] = invalid_metric

        for name, report in cases.items():
            with self.subTest(report=name):
                path = self.root / f"case-{name.replace(' ', '-')}.json"
                path.write_text(json.dumps(report))
                result = run_lab(self.root, "--report", path.name, check=False)
                self.assertNotEqual(result.returncode, 0, result.stdout)
                current = (self.root / "accepted.json").read_bytes()
                self.assertEqual(hashlib.sha256(current).hexdigest(), original_hash)
                self.assertEqual(current, original)

    def test_repeat_and_interrupted_publication_are_idempotent(self):
        run_lab(self.root)
        run_lab(self.root)
        publications = [json.loads(line) for line in (self.root / "publications.jsonl").read_text().splitlines()]
        self.assertEqual(len(publications), 1)
        self.assertEqual(publications[0]["model_id"], "fixture-B")
        self.assertIn("release_id", publications[0])
        self.assertIn("evaluation_run_id", publications[0])
        self.assertIn("runtime_sha256", publications[0])

        (self.root / "publications.jsonl").unlink()
        recovered = run_lab(self.root)
        self.assertIn("RECOVERED", recovered.stdout)
        run_lab(self.root)
        publications = (self.root / "publications.jsonl").read_text().splitlines()
        self.assertEqual(len(publications), 1)

    def test_readiness_blocks_generation_until_warmup(self):
        server = self.start_server(warmup=0.8, wait_ready=False)
        ready_status, _ = request(server.port, "GET", "/ready")
        generate_status, _ = request(server.port, "POST", "/generate", {"prompt": "ping", "max_new_tokens": 1})
        self.assertEqual(ready_status, 503)
        self.assertEqual(generate_status, 503)
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            ready_status, _ = request(server.port, "GET", "/ready")
            if ready_status == 200:
                break
            time.sleep(0.05)
        self.assertEqual(ready_status, 200)

    def test_loaded_version_survives_pointer_change_restart_and_rollback(self):
        server = self.start_server()
        port = server.port
        status, first = request(port, "POST", "/generate", {"prompt": "ping", "max_new_tokens": 1})
        self.assertEqual(status, 200)
        self.assertEqual(first["model_id"], "fixture-A")
        run_lab(self.root)
        status, still_loaded = request(port, "POST", "/generate", {"prompt": "ping", "max_new_tokens": 1})
        self.assertEqual(status, 200)
        self.assertEqual(still_loaded["model_id"], "fixture-A")
        self.assertEqual(still_loaded["runtime_sha256"], first["runtime_sha256"])
        self.stop_servers()

        server = self.start_server()
        port = server.port
        _, second = request(port, "POST", "/generate", {"prompt": "ping", "max_new_tokens": 1})
        self.assertEqual(second["model_id"], "fixture-B")
        code = (
            "import json,release;from pathlib import Path;"
            f"r=Path({str(self.root)!r});"
            "release.publish(json.loads((r/'manifest-A.json').read_text()),"
            "json.loads((r/'report-A.json').read_text()),r/'accepted.json')"
        )
        run_python(["-c", code])
        _, before_rollback_restart = request(port, "POST", "/generate", {"prompt": "ping", "max_new_tokens": 1})
        self.assertEqual(before_rollback_restart["model_id"], "fixture-B")
        self.stop_servers()

        server = self.start_server()
        _, rolled_back = request(server.port, "POST", "/generate", {"prompt": "ping", "max_new_tokens": 1})
        self.assertEqual(rolled_back["model_id"], "fixture-A")

    def test_invalid_requests_do_not_call_backend(self):
        server = self.start_server()
        _, before = request(server.port, "GET", "/debug")
        for payload in [
            {"prompt": "", "max_new_tokens": 1},
            {"prompt": "ping", "max_new_tokens": 0},
            {"prompt": "ping", "max_new_tokens": 100000},
            {"prompt": "ping", "max_new_tokens": "1"},
        ]:
            with self.subTest(payload=payload):
                status, _ = request(server.port, "POST", "/generate", payload)
                self.assertGreaterEqual(status, 400)
                self.assertLess(status, 500)
                _, current = request(server.port, "GET", "/debug")
                self.assertEqual(current["calls"], before["calls"])
        status, answer = request(server.port, "POST", "/generate", {"prompt": "ping", "max_new_tokens": 1})
        self.assertEqual(status, 200)
        self.assertEqual(answer["model_id"], "fixture-A")
        _, current = request(server.port, "GET", "/debug")
        self.assertEqual(current["calls"], before["calls"] + 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
