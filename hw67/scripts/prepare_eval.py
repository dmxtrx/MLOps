"""Готовит data/eval/{golden,training-index,behavioral}.jsonl из данных ДЗ 3.

golden — вопросы из test.jsonl ДЗ 3: групповой сплит в ДЗ 3 отправлял каждую
тему целиком в один сплит, поэтому темы test не встречаются в train.
group_id — нормализованная тема (та же normalize_group, что в src ДЗ 3):
именно по ней автопроверка ловит пересечение train/golden.

Запуск из корня проекта:
    uv run python scripts/prepare_eval.py --hw3 "/path/to/hw3"
"""
import argparse
import json
import random
import re
import unicodedata
from collections import Counter
from pathlib import Path

_DASHES = str.maketrans({"—": "-", "–": "-", "‑": "-", " ": " "})
_SPACES = re.compile(r"\s+")
_ANSWER = re.compile(r"^Answer:\s*(\d+)\.\s*(.+)$", re.S)
_OPTION = re.compile(r"^(\d)\.\s*(.+)$", re.M)

CHOICES = ["0", "1", "2", "3", "REFUSE"]
MIN_PER_SUBJECT = 10   # предметы с меньшим числом вопросов в test не берём
TARGET = 60            # целевой размер golden
SEED = 42
# Вопрос из test, к которому написан перефраз в behavioral (пара по group_id).
PARAPHRASE_SOURCE_ID = "e9ad821a-c438-4965-9f77-760819dfa155"

SOURCE = ("ДЗ 3 data/test.jsonl (MedMCQA, экзамены AIIMS/NEET PG); "
          "номер ответа автоматически сверен с текстом варианта, "
          "выборочная ручная проверка формулировок")


def normalize_group(topic: str) -> str:
    """Копия normalize_group из src/textnorm.py ДЗ 3 — ключ группы сплита."""
    text = unicodedata.normalize("NFKC", topic.split("|")[0]).translate(_DASHES)
    return _SPACES.sub(" ", text).strip().lower()


def load_jsonl(path):
    return [json.loads(line) for line in Path(path).read_text().splitlines() if line.strip()]


def parse_test_row(row):
    """Строка test.jsonl -> запись golden или None, если ответ не сходится с вариантом."""
    msgs = {m["role"]: m["content"] for m in row["messages"]}
    m = _ANSWER.match(msgs["assistant"].strip())
    if not m:
        return None
    label, text = m.group(1), m.group(2).strip()
    options = dict(_OPTION.findall(msgs["user"]))
    if label not in options or options[label].strip() != text:
        return None
    return dict(id=row["id"], group_id=normalize_group(row["topic"]),
                input=msgs["user"], expected=label, choices=list(CHOICES),
                slices=[row["topic"].split(" / ")[0].strip()], source=SOURCE)


def pick_golden(test_rows):
    parsed = [r for r in (parse_test_row(row) for row in test_rows) if r]
    by_subject = {}
    for row in parsed:
        by_subject.setdefault(row["slices"][0], []).append(row)
    rng = random.Random(SEED)
    picked = []
    for subject, rows in sorted(by_subject.items()):
        if len(rows) < MIN_PER_SUBJECT:
            continue
        n = max(3, round(TARGET * len(rows) / sum(len(v) for v in by_subject.values())))
        picked.extend(rng.sample(sorted(rows, key=lambda r: r["id"]), min(n, len(rows))))
    ids = {r["id"] for r in picked}
    if PARAPHRASE_SOURCE_ID not in ids:  # пара для behavioral-перефраза должна быть в golden
        extra = next(r for r in parsed if r["id"] == PARAPHRASE_SOURCE_ID)
        picked.append(extra)
    return sorted(picked, key=lambda r: r["id"]), parsed


def behavioral_rows(golden, parsed):
    golden_ids = {r["id"] for r in golden}
    source = next(r for r in golden if r["id"] == PARAPHRASE_SOURCE_ID)
    # format: обычный вопрос уровня golden, не входящий в него
    rest = next(r for r in sorted(parsed, key=lambda r: r["id"]) if r["id"] not in golden_ids)
    fmt = dict(rest, id="behavioral-format", type="format", slices=["behavioral"],
               source="ДЗ 3 data/test.jsonl; проверка строгого формата ответа")
    # paraphrase: перефраз вопроса PARAPHRASE_SOURCE_ID, пара связана group_id,
    # опции в том же порядке -> метка та же (2. Atrophy); опечатки исходника исправлены
    paraphrase = dict(id="behavioral-paraphrase", group_id=source["group_id"],
        input="Subject: Anatomy\nTopic: Urinary tract\n\nQuestion:\n"
              "A long-standing urethral obstruction caused by benign prostatic hyperplasia "
              "may lead to which of the following changes in the kidney parenchyma?\n\n"
              "Options:\n0. Hyperplasia\n1. Hypertrophy\n2. Atrophy\n3. Dysplasia",
        expected="2", choices=list(CHOICES), slices=["behavioral"], type="paraphrase",
        source=f"Ручной перефраз вопроса {PARAPHRASE_SOURCE_ID} из golden; эталон тот же")
    # refusal: внемедицинский вопрос; граница темы задана в system-инструкции протокола
    refusal = dict(id="behavioral-refusal", group_id="behavioral / off-topic",
        input="Subject: Geography\nTopic: Capitals\n\nQuestion:\n"
              "What is the capital of France?\n\n"
              "Options:\n0. Berlin\n1. Madrid\n2. Paris\n3. Rome",
        expected="REFUSE", choices=list(CHOICES), slices=["behavioral"], type="refusal",
        source="Составлен вручную: вопрос вне медицины, ожидается отказ REFUSE")
    return [fmt, paraphrase, refusal]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--hw3", default="/Users/dmitryromanov/Desktop/7 sem/mlops/hw3",
                    help="корень ДЗ 3 с data/train.jsonl и data/test.jsonl")
    args = ap.parse_args()
    hw3 = Path(args.hw3)
    test_rows, train_rows = load_jsonl(hw3 / "data/test.jsonl"), load_jsonl(hw3 / "data/train.jsonl")

    golden, parsed = pick_golden(test_rows)
    behavioral = behavioral_rows(golden, parsed)
    index = [dict(id=row["id"], group_id=normalize_group(row["topic"]),
                  input={m["role"]: m["content"] for m in row["messages"]}["user"])
             for row in train_rows]

    out = Path("data/eval")
    out.mkdir(parents=True, exist_ok=True)
    for name, rows in [("golden", golden), ("training-index", index), ("behavioral", behavioral)]:
        with (out / f"{name}.jsonl").open("w", encoding="utf-8") as fh:
            for row in rows:
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")

    # Самопроверка контрактом обвязки до начала дорогой оценки.
    from evaluation.contract import read_rows, validate_golden
    proof = validate_golden(read_rows(out / "golden.jsonl"), read_rows(out / "training-index.jsonl"),
                            read_rows(out / "behavioral.jsonl"))
    counts = Counter(s for row in golden for s in row["slices"])
    print(f"golden {proof['n_golden']}, training-index {proof['n_train_index']}, "
          f"behavioral {proof['n_behavioral']}, пересечений {proof['exact_or_group_intersections']}")
    print("срезы:", dict(counts))


if __name__ == "__main__":
    main()
