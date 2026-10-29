"""Замер реального API: одинаковые запросы при concurrency 1 и 2."""
import argparse
import concurrent.futures
import json
import socket
import statistics
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
HW6 = ROOT.parent / "hw6-7"


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


def one_request(port, mode, index, prompt):
    started = time.perf_counter()
    record = {"id": index, "mode": mode, "request": {"max_new_tokens": 32}}
    try:
        status, response = request(
            port, "POST", "/generate", {"prompt": prompt, "max_new_tokens": 32},
        )
        record.update(
            status=status,
            client_seconds=time.perf_counter() - started,
            generation_seconds=response.get("generation_seconds"),
            queue_seconds=response.get("queue_seconds"),
            model_id=response.get("model_id"),
            runtime_sha256=response.get("runtime_sha256"),
            request_id=response.get("request_id"),
            output_tokens=response.get("output_tokens"),
            answer=response.get("answer"),
            error=None if status == 200 else response,
        )
    except Exception as error:
        record.update(
            status=0, client_seconds=time.perf_counter() - started,
            error=f"{type(error).__name__}: {error}",
        )
    return record


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--count", type=int, default=8, choices=(4, 8))
    parser.add_argument("--output-dir", type=Path, default=ROOT / "artifacts" / "benchmark")
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    rows = [json.loads(line) for line in (HW6 / "data" / "eval" / "golden.jsonl").read_text().splitlines()]
    prompts = [row["input"] for row in rows[:args.count]]
    port = free_port()
    log_path = args.output_dir / "service.log"
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
                    raise RuntimeError(f"Сервис завершился: {log_path.read_text()}")
                try:
                    status, _ = request(port, "GET", "/ready", timeout=2)
                    if status == 200:
                        break
                except (OSError, ValueError):
                    pass
                time.sleep(0.2)
            else:
                raise TimeoutError("Модель не стала готовой за 600 секунд")

            attempts = []
            summaries = []
            for mode, concurrency in (("concurrency-1", 1), ("concurrency-2", 2)):
                batch_started = time.perf_counter()
                with concurrent.futures.ThreadPoolExecutor(max_workers=concurrency) as executor:
                    futures = [executor.submit(one_request, port, mode, i, prompt) for i, prompt in enumerate(prompts, 1)]
                    results = [future.result() for future in futures]
                batch_seconds = time.perf_counter() - batch_started
                attempts.extend(results)
                successful = [item for item in results if item["status"] == 200]
                summaries.append({
                    "mode": mode,
                    "n": len(results),
                    "successes": len(successful),
                    "errors": len(results) - len(successful),
                    "batch_seconds": batch_seconds,
                    "successful_requests_per_second": len(successful) / batch_seconds,
                    "mean_success_client_seconds": statistics.mean(item["client_seconds"] for item in successful) if successful else None,
                    "max_success_client_seconds": max((item["client_seconds"] for item in successful), default=None),
                    "max_new_tokens": 32,
                })
            with (args.output_dir / "attempts.jsonl").open("w") as stream:
                for item in attempts:
                    stream.write(json.dumps(item, ensure_ascii=False, allow_nan=False) + "\n")
            report = {
                "protocol": {"count_per_mode": args.count, "modes": [1, 2], "same_prompts": True},
                "summaries": summaries,
                "limitations": [
                    "Маленькая локальная выборка; p95 не оценивается",
                    "Backend сериализует генерацию через один semaphore",
                    "TTFT и ITL не измерялись для единого JSON-ответа",
                ],
            }
            (args.output_dir / "summary.json").write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
            print(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False))
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=10)


if __name__ == "__main__":
    main()
