"""Контракты кеша, проверки выпуска и атомарной публикации."""
import hashlib
import json
import math
import os
import re
import tempfile
from pathlib import Path

SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
HASH_FIELDS = ("model_sha256", "protocol_sha256", "golden_sha256", "runtime_sha256")


def canonical(value):
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"),
        ensure_ascii=False, allow_nan=False,
    ).encode("utf-8")


def digest(value):
    return hashlib.sha256(value).hexdigest()


def cache_key(data_path: Path, config: dict) -> str:
    data_sha256 = digest(Path(data_path).read_bytes())
    return digest(canonical({"data_sha256": data_sha256, "config": config}))


def validate_release(manifest: dict, report: dict) -> None:
    if not isinstance(manifest, dict) or not isinstance(report, dict):
        raise ValueError("Manifest и report должны быть JSON-объектами")
    if type(manifest.get("schema_version")) is not int or manifest["schema_version"] != 1:
        raise ValueError("Неподдерживаемая schema_version")
    for key in ("model_id", "artifact_uri"):
        if not isinstance(manifest.get(key), str) or not manifest[key]:
            raise ValueError(f"Некорректное поле manifest.{key}")
    for key in HASH_FIELDS:
        if not isinstance(manifest.get(key), str) or not SHA256_RE.fullmatch(manifest[key]):
            raise ValueError(f"Некорректный manifest.{key}")
    runtime = manifest.get("runtime")
    if not isinstance(runtime, dict) or not runtime:
        raise ValueError("Пустой runtime")
    try:
        actual_runtime_sha256 = digest(canonical(runtime))
    except (TypeError, ValueError) as error:
        raise ValueError("Runtime нельзя канонизировать") from error
    if actual_runtime_sha256 != manifest["runtime_sha256"]:
        raise ValueError("runtime_sha256 не соответствует runtime")

    if report.get("status") != "complete":
        raise ValueError("Оценка не завершена")
    if report.get("passed") is not True:
        raise ValueError("Кандидат отклонён")
    if not isinstance(report.get("evaluation_run_id"), str) or not report["evaluation_run_id"]:
        raise ValueError("Нет evaluation_run_id")
    if report.get("model_id") != manifest["model_id"]:
        raise ValueError("Отчёт относится к другой модели")
    for key in HASH_FIELDS:
        if report.get(key) != manifest[key]:
            raise ValueError(f"Отчёт не совпадает по {key}")

    metrics = report.get("metrics")
    if not isinstance(metrics, dict):
        raise ValueError("Нет metrics")
    for key in ("accuracy", "format_rate"):
        value = metrics.get(key)
        if (
            not isinstance(value, (int, float)) or isinstance(value, bool)
            or not math.isfinite(value) or not 0 <= value <= 1
        ):
            raise ValueError(f"Некорректная метрика {key}")


def publish(manifest: dict, report: dict, target: Path) -> None:
    validate_release(manifest, report)
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary_name = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=target.parent,
            prefix=f".{target.name}.", suffix=".tmp", delete=False,
        ) as temporary:
            temporary_name = temporary.name
            json.dump(manifest, temporary, ensure_ascii=False, indent=2, allow_nan=False)
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary_name, target)
    except Exception:
        if temporary_name is not None:
            Path(temporary_name).unlink(missing_ok=True)
        raise


def release_id(manifest: dict, report: dict) -> str:
    validate_release(manifest, report)
    return digest(canonical({
        "model_id": manifest["model_id"],
        "model_sha256": manifest["model_sha256"],
        "runtime_sha256": manifest["runtime_sha256"],
        "protocol_sha256": manifest["protocol_sha256"],
        "golden_sha256": manifest["golden_sha256"],
        "evaluation_run_id": report["evaluation_run_id"],
    }))


def verify_artifact_files(root: Path, manifest: dict) -> None:
    expected = manifest.get("file_hashes")
    if expected is None:
        return
    if not isinstance(expected, dict) or not expected:
        raise ValueError("file_hashes должен быть непустым объектом")
    root = Path(root)
    actual = {}
    for relative, expected_hash in expected.items():
        path = root / relative
        if not path.is_file():
            raise ValueError(f"Файл артефакта не найден: {relative}")
        actual[relative] = digest(path.read_bytes())
    if actual != expected or digest(canonical(actual)) != manifest["model_sha256"]:
        raise ValueError("Хеши файлов артефакта не совпадают с manifest")
