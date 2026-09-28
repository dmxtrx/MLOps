"""Стадия collect: MedMCQA (parquet) → data/raw.jsonl.

Источник — MedMCQA: вопросы индийских медицинских вступительных экзаменов
AIIMS и NEET PG, четыре варианта ответа, лицензия Apache-2.0.
https://huggingface.co/datasets/openlifescienceai/medmcqa

Контракт стадии держит остальной пайплайн: на выходе JSONL со строками
{"id", "topic", "messages": [system, user, assistant]}.

Готовый набор — сырьё, а не сдача. Стадия делает пять вещей, и каждая видна
числом в metrics/collect.json:

  1. сужает набор до перечисленных предметов (collect.subjects), если нужно;
  2. оставляет только вопросы с одним правильным ответом (choice_type = single);
  3. выбрасывает вопросы без темы: тема — ключ группы для сплита, и строке
     без неё честно попасть некуда;
  4. сверяет разметку: индекс ответа в 0..3, все четыре варианта непустые.
     Битая строка выбрасывается, а не переносится в обучение;
  5. разводит инструкцию на варианты (collect.system_prompts), чтобы модель
     не заучила одну формулировку как константу.
"""

import hashlib
import json
import time
from pathlib import Path

import pyarrow.parquet as pq

from src.config import load_params, source_files

OPTION_COLUMNS = ["opa", "opb", "opc", "opd"]
COLUMNS = ["id", "question", *OPTION_COLUMNS, "cop", "choice_type", "subject_name", "topic_name"]
BATCH = 2000


def pick_prompt(example_id: str, variants: list[str]) -> str:
    """Детерминированно выбрать вариант инструкции по id примера.

    Именно sha1, а не встроенный hash(): тот солится на каждый запуск процесса,
    и raw.jsonl переставал бы быть воспроизводимым.
    """
    digest = hashlib.sha1(example_id.encode("utf-8")).hexdigest()
    return variants[int(digest, 16) % len(variants)]


def clean_field(value) -> str:
    """Строковое поле источника без краевых пробелов; None — пустая строка."""
    return str(value).strip() if value is not None else ""


def answer_is_valid(row: dict) -> bool:
    """Разметка пригодна: индекс ответа в 0..3, все варианты непустые."""
    cop = row["cop"]
    if not isinstance(cop, int) or not 0 <= cop < len(OPTION_COLUMNS):
        return False
    return all(clean_field(row[col]) for col in OPTION_COLUMNS)


def build_user(row: dict) -> str:
    options = "\n".join(
        f"{i}. {clean_field(row[col])}" for i, col in enumerate(OPTION_COLUMNS)
    )
    return (
        f"Subject: {clean_field(row['subject_name'])}\n"
        f"Topic: {clean_field(row['topic_name'])}\n\n"
        f"Question:\n{clean_field(row['question'])}\n\n"
        f"Options:\n{options}"
    )


def build_answer(row: dict) -> str:
    cop = row["cop"]
    return f"Answer: {cop}. {clean_field(row[OPTION_COLUMNS[cop]])}"


def main() -> None:
    params = load_params()
    cfg = params["collect"]
    paths = params["paths"]
    n_rows = cfg["n_rows"]
    variants = cfg["system_prompts"]
    if not variants:
        raise SystemExit("collect.system_prompts пуст: инструкцию брать неоткуда")
    subjects = cfg["subjects"]
    wanted = set(subjects) if subjects else None

    out = Path(paths["raw"])
    out.parent.mkdir(parents=True, exist_ok=True)

    started = time.perf_counter()
    files = source_files(params)
    scanned = written = 0
    dropped = {"subject_filter": 0, "multi_choice": 0, "no_topic": 0, "answer_invalid": 0}
    prompts_used: set[str] = set()

    with out.open("w", encoding="utf-8") as fh:
        for src in files:
            taken = 0
            # Фильтры применяются ДО отсечки n_rows: иначе «первые 3000 строк»
            # и «3000 строк после фильтров» — разные вещи.
            for batch in pq.ParquetFile(src).iter_batches(batch_size=BATCH, columns=COLUMNS):
                for row in batch.to_pylist():
                    if taken >= n_rows:
                        break
                    scanned += 1
                    if wanted is not None and clean_field(row["subject_name"]) not in wanted:
                        dropped["subject_filter"] += 1
                        continue
                    if clean_field(row["choice_type"]) != "single":
                        dropped["multi_choice"] += 1
                        continue
                    if not clean_field(row["topic_name"]):
                        dropped["no_topic"] += 1
                        continue
                    if not answer_is_valid(row):
                        dropped["answer_invalid"] += 1
                        continue
                    prompt = pick_prompt(row["id"], variants)
                    prompts_used.add(prompt)
                    record = {
                        "id": row["id"],
                        # Предмет + тема: одноимённые темы разных предметов —
                        # разные группы. Без «|»: normalize_group режет по нему.
                        "topic": f"{clean_field(row['subject_name'])} / {clean_field(row['topic_name'])}",
                        "messages": [
                            {"role": "system", "content": prompt},
                            {"role": "user", "content": build_user(row)},
                            {"role": "assistant", "content": build_answer(row)},
                        ],
                    }
                    fh.write(json.dumps(record, ensure_ascii=False) + "\n")
                    taken += 1
                    written += 1
                if taken >= n_rows:
                    break

    metrics = {
        "version": cfg["version"],
        "files": len(files),
        "rows_scanned": scanned,
        "rows_written": written,
        **{f"dropped_{name}": count for name, count in dropped.items()},
        "subjects_filter": len(wanted) if wanted else 0,
        "system_prompt_variants": len(prompts_used),
        "seconds": round(time.perf_counter() - started, 2),
    }
    mpath = Path(paths["metrics_collect"])
    mpath.parent.mkdir(parents=True, exist_ok=True)
    mpath.write_text(json.dumps(metrics, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(
        f"collect: версия {cfg['version']}, файлов {len(files)}, "
        f"просмотрено {scanned}, записано {written} "
        f"(предмет -{dropped['subject_filter']}, multi -{dropped['multi_choice']}, "
        f"без темы -{dropped['no_topic']}, битая разметка -{dropped['answer_invalid']}), "
        f"вариантов инструкции {len(prompts_used)}, {metrics['seconds']} с → {out}"
    )


if __name__ == "__main__":
    main()