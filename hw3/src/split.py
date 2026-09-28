"""Стадия split: разбиение на train/val/test."""

import json
import random
import time
from pathlib import Path

from src.config import load_params
from src.contamination import report
from src.schema import Example, dump, iter_examples
from src.textnorm import normalize_group

from src.contamination import is_clean, report


def group_split(sizes: dict[str, int], ratios: dict[str, float], seed: int) -> dict[str, str]:
    """Раздать метки сплита ГРУППАМ, а не строкам.

    Тема целиком уходит в один сплит: вопросы внутри темы — парафразы друг
    друга, и тема, разрезанная между train и test, — это test, показанный
    модели на обучении. Каждая следующая группа идёт в тот сплит, которому
    больше всех недостаёт до своей доли: так доли держатся близко к ratios,
    а не «train 90 %, test пуст».
    """
    groups = sorted(sizes)                  # порядок не зависит от порядка строк
    random.Random(seed).shuffle(groups)
    total = sum(sizes.values())
    names = list(ratios)
    filled = {name: 0 for name in names}
    assignment: dict[str, str] = {}
    for group in groups:
        name = max(names, key=lambda n: ratios[n] * total - filled[n])
        assignment[group] = name
        filled[name] += sizes[group]
    return assignment


def main() -> None:
    params = load_params()
    paths = params["paths"]
    cfg = params["split"]
    started = time.perf_counter()

    examples: list[Example] = list(iter_examples(paths["clean"]))
    if cfg["group_key"] != "topic":
        raise SystemExit(f"неизвестный split.group_key: {cfg['group_key']!r}")

    sizes: dict[str, int] = {}
    for ex in examples:
        key = normalize_group(ex.topic)
        sizes[key] = sizes.get(key, 0) + 1

    assignment = group_split(sizes, cfg["ratios"], cfg["seed"])
    buckets: dict[str, list[Example]] = {name: [] for name in cfg["ratios"]}
    for ex in examples:
        buckets[assignment[normalize_group(ex.topic)]].append(ex)

    for name, rows in buckets.items():
        out = Path(paths[name])
        out.parent.mkdir(parents=True, exist_ok=True)
        with out.open("w", encoding="utf-8") as fh:
            for ex in rows:
                fh.write(dump(ex) + "\n")

    nd = params["clean"]["near_dup"]
    rep = report(
        buckets["train"],
        buckets["test"],
        shingle_words=nd["shingle_words"],
        num_perm=nd["num_perm"],
        threshold=params["contamination"]["threshold"],
    )

    metrics = {
        "version": params["collect"]["version"],
        "seed": cfg["seed"],
        "group_key": cfg["group_key"],
        "groups_total": len(sizes),
        "sizes": {name: len(rows) for name, rows in buckets.items()},
        "groups": {
            name: len({normalize_group(ex.topic) for ex in rows}) for name, rows in buckets.items()
        },
        "ratios_actual": {
            name: round(len(rows) / len(examples), 4) for name, rows in buckets.items()
        },
        "contamination": rep,
        "seconds": round(time.perf_counter() - started, 2),
    }
    mpath = Path(paths["metrics_split"])
    mpath.parent.mkdir(parents=True, exist_ok=True)
    mpath.write_text(json.dumps(metrics, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(
        "split: "
        + ", ".join(f"{name} {len(rows)}" for name, rows in buckets.items())
        + f" (групп {len(sizes)}, {metrics['seconds']} с)"
    )

    if not is_clean(rep):
        raise SystemExit(
            "split: train и test пересекаются — "
            f"id {rep['id_overlap']}, текст {rep['text_overlap']}, "
            f"группы {rep['group_overlap']}, near-dup {rep['near_dup_pairs']}"
        )


if __name__ == "__main__":
    main()
