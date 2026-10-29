"""Собрать manifest принятой baseline и связанные отчёты из ДЗ 6+7."""
import hashlib
import json
import os
from pathlib import Path
import sys

import release

ROOT = Path(__file__).resolve().parent
HW6 = ROOT.parent / "hw6-7"
OUT = ROOT / "artifacts" / "real"
BASE = Path(os.environ.get(
    "QWEN_BASE_PATH",
    "/Users/dmitryromanov/.cache/huggingface/hub/models--Qwen--Qwen3-0.6B/"
    "snapshots/c1899de289a04d12100db370d81485cdf75e47ca",
))


def read(path):
    return json.loads(Path(path).read_text())


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write(path, value):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n")


def main():
    sys.path.insert(0, str(HW6))
    from evaluation.solution import quality_gate

    baseline_eval_path = HW6 / "metrics" / "baseline-eval.json"
    candidate_eval_path = HW6 / "metrics" / "candidate-eval.json"
    baseline_eval = read(baseline_eval_path)
    candidate_eval = read(candidate_eval_path)
    params = read(HW6 / "evaluation" / "params.json")
    candidate_decision = {"passed": False}
    try:
        candidate_decision = quality_gate(
            baseline_eval, candidate_eval, params["critical_slices"],
        )
    except ValueError as error:
        candidate_decision["reason"] = str(error)

    candidate_report = {
        "status": "complete",
        "passed": candidate_decision["passed"],
        "reason": candidate_decision.get("reason"),
        "model_id": candidate_eval["model"]["uri"],
        "evaluation_run_id": candidate_eval["evaluation_run_id"],
        "metrics": {
            "accuracy": candidate_eval["overall"]["accuracy"],
            "format_rate": candidate_eval["overall"]["format_rate"],
        },
        "sources": {
            "baseline_eval": {"uri": str(baseline_eval_path), "sha256": sha(baseline_eval_path)},
            "candidate_eval": {"uri": str(candidate_eval_path), "sha256": sha(candidate_eval_path)},
            "baseline_outputs": {"uri": str(HW6 / "metrics" / "baseline-outputs.json"), "sha256": sha(HW6 / "metrics" / "baseline-outputs.json")},
            "candidate_outputs": {"uri": str(HW6 / "metrics" / "candidate-outputs.json"), "sha256": sha(HW6 / "metrics" / "candidate-outputs.json")},
        },
    }
    write(OUT / "release-report.json", candidate_report)

    file_hashes = {
        path.name: sha(path) for path in sorted(BASE.iterdir())
        if path.is_file() and path.name in {
            "config.json", "generation_config.json", "merges.txt", "model.safetensors",
            "tokenizer.json", "tokenizer_config.json", "vocab.json",
        }
    }
    tokenizer_hashes = {key: value for key, value in file_hashes.items() if key != "model.safetensors"}
    tokenizer_config = read(BASE / "tokenizer_config.json")
    runtime = {
        "base_name": baseline_eval["model"]["name"],
        "base_revision": baseline_eval["model"]["revision"],
        "tokenizer_sha256": release.digest(release.canonical(tokenizer_hashes)),
        "template_sha256": release.digest(tokenizer_config["chat_template"].encode()),
        "generation": {
            "system": params["system"],
            "max_new_tokens": params["max_new_tokens"],
            "do_sample": False,
            "seed": params["seed"],
            "enable_thinking": params["enable_thinking"],
        },
        "dtype": "bfloat16",
    }
    manifest = {
        "schema_version": 1,
        "model_id": "baseline-qwen3-0.6b-c1899de2",
        "artifact_uri": str(BASE),
        "model_sha256": release.digest(release.canonical(file_hashes)),
        "protocol_sha256": baseline_eval["protocol_sha256"],
        "golden_sha256": baseline_eval["golden_sha256"],
        "runtime_sha256": release.digest(release.canonical(runtime)),
        "runtime": runtime,
        "file_hashes": file_hashes,
        "origin": {
            "kind": "accepted-baseline",
            "evaluation_run_id": baseline_eval["evaluation_run_id"],
            "candidate_training_run_id": candidate_eval["model"]["run_id"],
        },
    }
    baseline_passed = (
        baseline_eval["status"] == "complete"
        and baseline_eval["overall"]["errors"] == 0
        and baseline_eval["overall"]["n"] >= 50
        and baseline_eval["behavioral"]["overall"]["errors"] == 0
        and baseline_eval["behavioral"]["overall"]["n"] >= 3
    )
    if not baseline_passed:
        raise ValueError("Прежняя baseline не имеет полной успешной оценки")
    baseline_report = {
        "status": baseline_eval["status"],
        "passed": baseline_passed,
        "decision": "serve-previous-baseline",
        "evaluation_run_id": baseline_eval["evaluation_run_id"],
        "model_id": manifest["model_id"],
        **{key: manifest[key] for key in release.HASH_FIELDS},
        "metrics": {
            "accuracy": baseline_eval["overall"]["accuracy"],
            "format_rate": baseline_eval["overall"]["format_rate"],
        },
        "sources": candidate_report["sources"],
        "candidate_release_report": {
            "uri": str(OUT / "release-report.json"),
            "sha256": sha(OUT / "release-report.json"),
        },
    }
    release.validate_release(manifest, baseline_report)
    release.verify_artifact_files(BASE, manifest)
    write(OUT / "manifest.json", manifest)
    write(OUT / "baseline-report.json", baseline_report)
    write(OUT / "accepted.json", manifest)
    print(json.dumps({
        "accepted": manifest["model_id"],
        "candidate_passed": candidate_decision["passed"],
        "candidate_reason": candidate_decision.get("reason"),
        "manifest": str(OUT / "manifest.json"),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
