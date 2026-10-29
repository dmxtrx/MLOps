"""Совместимый CLI стенда и последовательный адаптер процесса выпуска."""
import argparse
import hashlib
import json
from pathlib import Path
import uuid

import release


def canonical(value):
    return release.canonical(value)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def read(path):
    return json.loads(Path(path).read_text())


def write(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False))


def initialize(root):
    if root.exists():
        raise FileExistsError("Рабочий каталог уже существует. Выберите новый --workspace.")
    root.mkdir(parents=True)
    runtime = {
        "base_revision": "fixture-only", "tokenizer_sha256": "a" * 64,
        "template_sha256": "b" * 64,
        "generation": {"max_new_tokens": 32, "do_sample": False}, "dtype": "float32",
    }
    for version in ["A", "B"]:
        artifact = root / f"model-{version}.txt"
        artifact.write_text(f"STUB ONLY — version {version}\n")
        files = {artifact.name: digest(artifact.read_bytes())}
        manifest = {
            "schema_version": 1, "model_id": f"fixture-{version}",
            "artifact_uri": artifact.name, "model_sha256": digest(canonical(files)),
            "protocol_sha256": "d" * 64, "golden_sha256": "e" * 64,
            "runtime_sha256": digest(canonical(runtime)), "runtime": runtime,
            "file_hashes": files,
        }
        report = {key: manifest[key] for key in ["model_id", *release.HASH_FIELDS]}
        report.update(
            status="complete", passed=True,
            evaluation_run_id=f"fixture-eval-{version}",
            metrics={"accuracy": 0.8, "format_rate": 1.0}, synthetic=True,
        )
        write(root / f"manifest-{version}.json", manifest)
        write(root / f"report-{version}.json", report)
    write(root / "accepted.json", read(root / "manifest-A.json"))
    report = read(root / "report-B.json")
    report["status"] = "running"
    report["passed"] = "false"
    write(root / "report-incomplete.json", report)
    report = read(root / "report-B.json")
    report["passed"] = False
    write(root / "report-reject.json", report)
    write(root / "config.json", {
        "tokenizer_revision": "r1", "template_sha256": "b" * 64,
        "max_length": 128, "code_revision": "prepare-v1",
    })
    (root / "input.txt").write_text("Первый пример\nВторой пример\n")
    print("Создан синтетический стенд:", root)


def prepare(root):
    config = read(root / "config.json")
    key = release.cache_key(root / "input.txt", config)
    cache = root / "cache"
    cache.mkdir(exist_ok=True)
    entry = cache / f"{key}.json"
    if entry.exists():
        result = read(entry)
        print("CACHE_HIT", result["input_sha256"], key, flush=True)
        return result
    payload = (root / "input.txt").read_bytes()
    result = {
        "input_sha256": digest(payload), "rows": len(payload.splitlines()),
        "config": config, "cache_key": key,
    }
    write(entry, result)
    print("PREPARED", result["input_sha256"], key, flush=True)
    return result


def append_jsonl(path, value):
    with Path(path).open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(value, ensure_ascii=False, allow_nan=False) + "\n")
        stream.flush()


def read_jsonl(path):
    path = Path(path)
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line]


def run(root, report_name, fail_after_prepare=False, fail_after_accept=False):
    prepared = prepare(root)
    if fail_after_prepare:
        raise RuntimeError("Инъекция сбоя после подготовки")
    manifest = read(root / "manifest-B.json")
    report = read(root / report_name)
    return publish(root, prepared, manifest, report, report_name, fail_after_accept)


def publish(root, prepared, manifest, report, report_name, fail_after_accept=False):
    attempt = {
        "attempt_id": str(uuid.uuid4()), "report": report_name,
        "prepared": prepared["input_sha256"],
    }
    try:
        publication_id = release.release_id(manifest, report)
        release.verify_artifact_files(root, manifest)
        previous = read(root / "accepted.json") if (root / "accepted.json").exists() else None
        was_accepted = previous == manifest
        release.publish(manifest, report, root / "accepted.json")
        if fail_after_accept:
            raise RuntimeError("Инъекция сбоя между accepted.json и журналом")
        publications = read_jsonl(root / "publications.jsonl")
        if any(item.get("release_id") == publication_id for item in publications):
            outcome = "ALREADY_PUBLISHED"
        else:
            outcome = "RECOVERED" if was_accepted else "PUBLISHED"
            append_jsonl(root / "publications.jsonl", {
                "release_id": publication_id,
                "model_id": manifest["model_id"],
                "runtime_sha256": manifest["runtime_sha256"],
                "evaluation_run_id": report["evaluation_run_id"],
                "prepared": prepared["input_sha256"],
                "event": "publication",
            })
        append_jsonl(root / "attempts.jsonl", {
            **attempt, "release_id": publication_id, "outcome": outcome,
        })
        print(outcome, manifest["model_id"], publication_id, flush=True)
        return {"outcome": outcome, "release_id": publication_id, "prepared": prepared}
    except Exception as error:
        append_jsonl(root / "attempts.jsonl", {
            **attempt, "outcome": "ERROR", "error": str(error),
        })
        raise


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", type=Path, default=Path(".lab"))
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("init")
    run_parser = sub.add_parser("run")
    run_parser.add_argument("--report", default="report-B.json")
    run_parser.add_argument("--fail-after-prepare", action="store_true")
    run_parser.add_argument("--fail-after-accept", action="store_true")
    args = parser.parse_args()
    if args.command == "init":
        initialize(args.workspace)
    else:
        run(args.workspace, args.report, args.fail_after_prepare, args.fail_after_accept)


if __name__ == "__main__":
    main()
