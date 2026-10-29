"""Prefect-flow выпуска с сохраняемым результатом подготовки."""
import argparse
import json
import os
from pathlib import Path

from prefect import flow, task

import lab
import release


def input_key(context, parameters):
    root = Path(parameters["root"])
    config = lab.read(root / "config.json")
    preparation_key = release.cache_key(root / "input.txt", config)
    return release.digest(release.canonical({
        "workspace": str(root.resolve()), "preparation": preparation_key,
    }))


@task(persist_result=True, cache_key_fn=input_key)
def prepare_input(root):
    return lab.prepare(root)


@task
def get_artifact(root):
    manifest = lab.read(root / "manifest-B.json")
    release.verify_artifact_files(root, manifest)
    return {
        "artifact_uri": manifest["artifact_uri"],
        "model_id": manifest["model_id"],
        "model_sha256": manifest["model_sha256"],
        "manifest": manifest,
    }


@task
def evaluate_release(root, report_name, artifact):
    report = lab.read(root / report_name)
    release.validate_release(artifact["manifest"], report)
    return report


@task
def publish_release(root, prepared, artifact, report, report_name, fail_after_accept=False):
    return lab.publish(
        root, prepared, artifact["manifest"], report,
        report_name, fail_after_accept,
    )


@flow(name="model-release")
def release_flow(root, report_name, fail_after_prepare=False, fail_after_accept=False):
    prepared = prepare_input(root)
    if fail_after_prepare:
        raise RuntimeError("Инъекция сбоя после подготовки")
    artifact = get_artifact(root)
    report = evaluate_release(root, report_name, artifact)
    result = publish_release(
        root, prepared, artifact, report, report_name, fail_after_accept,
    )
    print("FLOW_RESULT", json.dumps(result, ensure_ascii=False), flush=True)
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", type=Path, default=Path(".lab"))
    parser.add_argument("--report", default="report-B.json")
    parser.add_argument("--fail-after-prepare", action="store_true")
    parser.add_argument("--fail-after-accept", action="store_true")
    args = parser.parse_args()
    os.environ.setdefault("PREFECT_HOME", str(args.workspace / ".prefect"))
    os.environ.setdefault("PREFECT_RESULTS_PERSIST_BY_DEFAULT", "true")
    release_flow(
        args.workspace, args.report,
        args.fail_after_prepare, args.fail_after_accept,
    )


if __name__ == "__main__":
    main()
