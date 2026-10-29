"""Реальный smoke: readiness, ответ baseline и отказ неверному запросу."""
import json
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def request(port, method, path, payload=None, timeout=300):
    body = None if payload is None else json.dumps(payload).encode()
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}", data=body, method=method,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read())


def main():
    root = ROOT / "artifacts" / "real"
    accepted = json.loads((root / "accepted.json").read_text())
    golden = json.loads((ROOT.parent / "hw6-7" / "data" / "eval" / "golden.jsonl").read_text().splitlines()[0])
    port = free_port()
    log_path = root / "smoke-service.log"
    with log_path.open("w") as log:
        process = subprocess.Popen(
            [sys.executable, "service.py", "--workspace", "artifacts/real",
             "--backend", "real", "--port", str(port), "--warmup-seconds", "0"],
            cwd=ROOT, stdout=log, stderr=subprocess.STDOUT,
        )
        try:
            deadline = time.monotonic() + 600
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    raise RuntimeError(log_path.read_text())
                try:
                    ready_status, ready = request(port, "GET", "/ready", timeout=2)
                    if ready_status == 200:
                        break
                except (OSError, ValueError):
                    pass
                time.sleep(0.2)
            else:
                raise TimeoutError("Модель не стала готовой")
            health_status, health = request(port, "GET", "/health")
            generation_status, generation = request(
                port, "POST", "/generate",
                {"prompt": golden["input"], "max_new_tokens": 32},
            )
            invalid_status, invalid = request(
                port, "POST", "/generate",
                {"prompt": golden["input"], "max_new_tokens": 100000},
            )
            result = {
                "health": {"status": health_status, "body": health},
                "ready": {"status": ready_status, "body": ready},
                "generation": {"status": generation_status, "body": generation},
                "invalid": {"status": invalid_status, "body": invalid},
            }
            assert generation_status == 200, result
            assert generation["model_id"] == accepted["model_id"], result
            assert generation["runtime_sha256"] == accepted["runtime_sha256"], result
            assert generation["answer"].strip(), result
            assert generation["output_tokens"] > 0, result
            assert 400 <= invalid_status < 500, result
            (root / "smoke.json").write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
            print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=10)


if __name__ == "__main__":
    main()
