"""Скорер строгих JSON-ответов и гейт допуска кандидата в champion.

score: сводит ответы по id, плохой ответ — строка с нулевым результатом,
а не повод выкинуть её из знаменателя. quality_gate: любая несовместимость,
NaN, missing, ошибка генерации или регрессия — ValueError; учебный допуск
регрессии равен нулю, пороги зафиксированы до измерения.
"""
import json
import math

METRICS = ("n", "correct", "valid", "errors", "accuracy", "format_rate")


def _parse(raw, choices):
    """Строгий разбор ответа: JSON-объект с единственным строковым полем answer.

    Возвращает (parsed, valid). parsed — извлечённая строка (None, если
    структура не соблюдена); valid — True, только если метка входит в choices.
    Маркдаун-ограждения, лишние ключи, null и нестроковый answer невалидны.
    """
    if not isinstance(raw, str) or not raw.strip():
        return None, False
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        return None, False
    if not isinstance(value, dict) or set(value) != {"answer"}:
        return None, False
    parsed = value["answer"]
    if not isinstance(parsed, str):
        return None, False
    return parsed, parsed in choices


def _aggregate(items):
    """Метрики группы строк: ошибки и невалидные ответы остаются в знаменателе."""
    n = len(items)
    correct = sum(1 for i in items if i["correct"])
    valid = sum(1 for i in items if i["valid"])
    errors = sum(1 for i in items if i["error"] is not None)
    return dict(n=n, correct=correct, valid=valid, errors=errors,
                accuracy=correct / n, format_rate=valid / n)


def score(rows, outputs):
    if not rows:
        raise ValueError("Пустой набор заданий")
    if not outputs:
        raise ValueError("Пустой набор ответов")
    ids = [row["id"] for row in rows]
    if len(set(ids)) != len(ids):
        raise ValueError("Дубли id в заданиях")
    by_id = {}
    for out in outputs:
        oid = out.get("id")
        if oid in by_id:
            raise ValueError(f"Дубль id в ответах: {oid}")
        by_id[oid] = out
    if set(by_id) != set(ids):
        raise ValueError("Множества id заданий и ответов не совпадают: "
                         f"только в заданиях {sorted(set(ids) - set(by_id))[:3]}, "
                         f"только в ответах {sorted(set(by_id) - set(ids))[:3]}")
    per_item = []
    for row in rows:
        out = by_id[row["id"]]
        raw, error = out.get("raw"), out.get("error")
        if error is None:
            parsed, valid = _parse(raw, row["choices"])
        else:
            parsed, valid = None, False
        per_item.append(dict(id=row["id"], raw=raw, parsed=parsed, valid=valid,
                             correct=bool(valid and parsed == row["expected"]),
                             error=error, slices=list(row["slices"])))
    grouped = {}
    for item in per_item:
        for name in item["slices"]:
            grouped.setdefault(name, []).append(item)
    return dict(overall=_aggregate(per_item),
                slices={name: _aggregate(items) for name, items in grouped.items()},
                per_item=per_item)


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def _is_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _check_block(block, items, where):
    """Метрики блока конечны и пересчитываются из per_item, а не принимаются на веру."""
    _require(isinstance(block, dict), f"{where}: нет блока метрик")
    for key in METRICS:
        _require(_is_number(block.get(key)), f"{where}: метрика {key} отсутствует или не конечна")
    for key in ("n", "correct", "valid", "errors"):
        _require(float(block[key]).is_integer() and block[key] >= 0, f"{where}: {key} не целое неотрицательное")
    n = len(items)
    _require(n > 0, f"{where}: пустая группа")
    _require(block["n"] == n, f"{where}: n={block['n']} не соответствует per_item ({n})")
    _require(block["correct"] == sum(1 for i in items if i["correct"]), f"{where}: correct не сходится с per_item")
    _require(block["valid"] == sum(1 for i in items if i["valid"]), f"{where}: valid не сходится с per_item")
    _require(block["errors"] == sum(1 for i in items if i["error"] is not None), f"{where}: errors не сходится с per_item")
    _require(math.isclose(block["accuracy"], block["correct"] / n), f"{where}: accuracy не соответствует counts")
    _require(math.isclose(block["format_rate"], block["valid"] / n), f"{where}: format_rate не соответствует counts")


def _check_report(report, min_n, min_slices, label):
    """Структура отчёта и его внутренняя согласованность с per_item."""
    _require(isinstance(report, dict), f"{label}: не отчёт")
    for key in ("overall", "slices", "per_item"):
        _require(key in report, f"{label}: нет {key}")
    per_item = report["per_item"]
    _require(isinstance(per_item, list) and per_item, f"{label}: пустой per_item")
    ids = [item.get("id") for item in per_item]
    _require(all(isinstance(i, str) and i for i in ids), f"{label}: нестроковые id в per_item")
    _require(len(set(ids)) == len(ids), f"{label}: дубли id в per_item")
    _require(len(per_item) >= min_n, f"{label}: n={len(per_item)} меньше минимума {min_n}")
    _check_block(report["overall"], per_item, f"{label}.overall")
    slices = report["slices"]
    _require(isinstance(slices, dict) and len(slices) >= min_slices,
             f"{label}: срезов {len(slices) if isinstance(slices, dict) else 0}, нужно минимум {min_slices}")
    mentioned = set()
    for item in per_item:
        _require(isinstance(item.get("slices"), list), f"{label}: нет slices в per_item")
        mentioned.update(item["slices"])
    _require(mentioned <= set(slices), f"{label}: в per_item есть срезы вне сводки")
    for name, block in slices.items():
        _check_block(block, [i for i in per_item if name in i["slices"]], f"{label}.slices[{name}]")


def quality_gate(baseline, candidate, critical_slices):
    # 1. Совместимость протокола, набора и зафиксированной спецификации baseline.
    for key in ("protocol_sha256", "golden_sha256", "baseline_spec_sha256"):
        _require(baseline.get(key) and baseline.get(key) == candidate.get(key),
                 f"Несовместимые отчёты: {key}")
    for label, report in (("baseline", baseline), ("candidate", candidate)):
        _require(report.get("status") == "complete", f"{label}: статус {report.get('status')!r}, а не complete")
        _require(report.get("model"), f"{label}: не указана модель")
        _check_report(report, min_n=50, min_slices=3, label=label)
    # 2. Одинаковые id, имена срезов и размеры групп у обоих отчётов.
    _require({i["id"] for i in baseline["per_item"]} == {i["id"] for i in candidate["per_item"]},
             "Наборы id baseline и candidate различаются")
    _require(set(baseline["slices"]) == set(candidate["slices"]), "Наборы срезов различаются")
    for name in baseline["slices"]:
        _require(baseline["slices"][name]["n"] == candidate["slices"][name]["n"],
                 f"Размер среза {name} различается")
    # 3. Нет ошибок генерации; общие метрики и критические срезы не ниже baseline.
    for label, report in (("baseline", baseline), ("candidate", candidate)):
        _require(report["overall"]["errors"] == 0, f"{label}: есть ошибки генерации")
    a, b = baseline["overall"], candidate["overall"]
    _require(b["accuracy"] >= a["accuracy"],
             f"Регрессия accuracy: candidate {b['accuracy']:.4f} < baseline {a['accuracy']:.4f}")
    _require(b["format_rate"] >= a["format_rate"],
             f"Регрессия format_rate: candidate {b['format_rate']:.4f} < baseline {a['format_rate']:.4f}")
    critical = {}
    for name in critical_slices:
        _require(name in baseline["slices"] and name in candidate["slices"],
                 f"Критический срез {name!r} отсутствует в отчётах")
        sa, sb = baseline["slices"][name], candidate["slices"][name]
        _require(sb["accuracy"] >= sa["accuracy"],
                 f"Регрессия в критическом срезе {name}: {sb['accuracy']:.4f} < {sa['accuracy']:.4f}")
        critical[name] = dict(n=sb["n"], baseline=sa["accuracy"], candidate=sb["accuracy"])
    # 4. Behavioral-кейсы кандидата: минимум три, все верные, без ошибок генерации.
    for label, report in (("baseline", baseline), ("candidate", candidate)):
        _require("behavioral" in report, f"{label}: нет behavioral-отчёта")
        _check_report(report["behavioral"], min_n=3, min_slices=1, label=f"{label}.behavioral")
    beh = candidate["behavioral"]["overall"]
    _require(beh["errors"] == 0, "candidate: ошибки генерации в behavioral")
    _require(beh["correct"] == beh["n"], f"candidate: behavioral пройден {beh['correct']}/{beh['n']}")
    return dict(passed=True, n=b["n"],
                accuracy=dict(baseline=a["accuracy"], candidate=b["accuracy"]),
                format_rate=dict(baseline=a["format_rate"], candidate=b["format_rate"]),
                critical=critical,
                behavioral=dict(n=beh["n"], correct=beh["correct"]))
