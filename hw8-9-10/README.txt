ДЗ 8+9+10: проверяемый выпуск модели

УСТАНОВКА И БЫСТРЫЕ ПРОВЕРКИ

  uv sync
  make check

Python 3.12+ и точные версии зависимостей зафиксированы в pyproject.toml/uv.lock.
Веса для unit/integration-тестов не нужны. tests/test_release.py — исходные 13
проверок; tests/test_integration.py — шесть новых HTTP/subprocess-сценариев.
Локальный make check является эквивалентом CI; ci.example.yml только шаблон.

STUB-ПРОЦЕСС И PREFECT

  make init                         # новый .lab; существующий не перезаписывается
  make run                          # Prefect release_flow
  make lab-run                      # тот же контракт через stdlib-адаптер
  make service                      # STUB ONLY на 127.0.0.1:8091

Отдельные опыты:

  .venv/bin/python flow.py --workspace .lab --fail-after-prepare
  .venv/bin/python flow.py --workspace .lab
  .venv/bin/python lab.py --workspace .lab run --fail-after-accept
  .venv/bin/python lab.py --workspace .lab run

Подготовка кешируется по SHA-256 байтов input.txt и всем полям config.json.
Prefect сохраняет результат задачи; attempts.jsonl отделён от publications.jsonl.
release_id связывает модель, runtime и evaluation_run_id. accepted.json пишется
через временный файл, flush/fsync и os.replace.

HTTP: GET /health, GET /ready, POST /generate, GET /debug только для STUB.
До готовности ready/generate возвращают 503. prompt 1..4096 символов,
max_new_tokens целое 1..32. Параллельные вычисления сериализуются semaphore.
Ответ содержит фактически загруженные model_id/runtime_sha256, request_id,
output_tokens, queue/generation/total durations.

РЕАЛЬНАЯ BASELINE-МОДЕЛЬ

  make real-manifest
  make real-service
  make smoke
  make benchmark                  # 8+8; на медленном CPU: make benchmark-count4

prepare_real.py вызывает quality_gate из ../hw6-7 и сохраняет исходные отчёты/хеши.
Кандидат отклонён: behavioral 2/3. Порог не ослаблялся; accepted.json указывает на
прежнюю baseline Qwen3-0.6B с revision c1899de289a04d12100db370d81485cdf75e47ca.
Веса читаются из локального HF-кэша, не копируются в Git. Путь можно сменить через
QWEN_BASE_PATH. SERVICE_DEVICE=cpu|mps|cuda и SERVICE_DTYPE задают окружение;
по умолчанию используются auto и bfloat16 из протокола оценки.

artifacts/real/release-report.json — отказ кандидата; baseline-report.json —
решение обслуживать прежнюю baseline; smoke.json — реальный ответ и HTTP 4xx.
artifacts/benchmark/attempts.jsonl — все запросы, summary.json — сравнение 1 и 2.
На маленькой выборке p95 не оценивается; TTFT/ITL для единого JSON не заявляются.

ДОКАЗАТЕЛЬСТВА И СДАЧА

evidence/before/ — исходные красные журналы; evidence/after/ — зелёные и опыты.
defects.csv связывает семь причин, исправления и тесты. REPORT.txt содержит команды,
связи версий, журналы, измерения, ограничения и план видео 5–8 минут.
Перед сдачей заменить pending-final-commit в defects.csv на фактический hash.
Веса, .venv, кеши, Docker-volumes и секреты не включать в Git/архив.
