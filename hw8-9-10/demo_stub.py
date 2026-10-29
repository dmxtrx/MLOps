"""Сохранить наблюдаемые STUB-ответы A → B → A и отказ неверному запросу."""
import json
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "tests"))
from test_integration import Server, init_workspace, request, run_lab, run_python


def wait_ready(port):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        status, body = request(port, "GET", "/ready")
        if status == 200:
            return body
        time.sleep(0.02)
    raise TimeoutError("STUB не стал готовым")


def generate(port):
    status, body = request(port, "POST", "/generate", {"prompt": "ping", "max_new_tokens": 1})
    assert status == 200, body
    return body


def main():
    records = []
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "workspace"
        init_workspace(root)
        server = Server(root, 0.05)
        wait_ready(server.port)
        records.append({"stage": "initial-A", **generate(server.port)})
        run_lab(root)
        records.append({"stage": "pointer-B-without-restart", **generate(server.port)})
        server.stop()

        server = Server(root, 0.05)
        wait_ready(server.port)
        records.append({"stage": "after-restart-B", **generate(server.port)})
        code = (
            "import json,release;from pathlib import Path;"
            f"r=Path({str(root)!r});"
            "release.publish(json.loads((r/'manifest-A.json').read_text()),"
            "json.loads((r/'report-A.json').read_text()),r/'accepted.json')"
        )
        run_python(["-c", code])
        records.append({"stage": "rollback-pointer-A-without-restart", **generate(server.port)})
        server.stop()

        server = Server(root, 0.05)
        wait_ready(server.port)
        records.append({"stage": "after-rollback-restart-A", **generate(server.port)})
        _, before = request(server.port, "GET", "/debug")
        invalid_status, invalid = request(server.port, "POST", "/generate", {"prompt": "", "max_new_tokens": 1})
        _, after = request(server.port, "GET", "/debug")
        records.append({
            "stage": "invalid-request", "status": invalid_status,
            "calls_before": before["calls"], "calls_after": after["calls"],
            "response": invalid,
        })
        server.stop()

    assert [item["model_id"] for item in records[:5]] == [
        "fixture-A", "fixture-A", "fixture-B", "fixture-B", "fixture-A",
    ]
    assert records[-1]["status"] == 422
    assert records[-1]["calls_before"] == records[-1]["calls_after"]
    output = ROOT / "evidence" / "after" / "stub-version-switch.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(records, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
    print(json.dumps(records, ensure_ascii=False, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
