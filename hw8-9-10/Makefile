PYTHON ?= .venv/bin/python
WORKSPACE ?= .lab
PORT ?= 8091

.PHONY: check check-public init run lab-run service real-manifest real-service smoke benchmark benchmark-count4
check:
	$(PYTHON) -m compileall -q release.py lab.py flow.py service.py backends.py prepare_real.py benchmark.py smoke_real.py demo_stub.py tests
	$(PYTHON) -m unittest discover -s tests -v

check-public:
	$(PYTHON) -m unittest discover -s tests -p 'test_release.py' -v

init:
	$(PYTHON) lab.py --workspace $(WORKSPACE) init

run:
	$(PYTHON) flow.py --workspace $(WORKSPACE)

lab-run:
	$(PYTHON) lab.py --workspace $(WORKSPACE) run

service:
	$(PYTHON) service.py --workspace $(WORKSPACE) --port $(PORT) --warmup-seconds 1

real-manifest:
	$(PYTHON) prepare_real.py

real-service:
	$(PYTHON) service.py --workspace artifacts/real --backend real --port $(PORT) --warmup-seconds 0

smoke:
	$(PYTHON) smoke_real.py

benchmark:
	$(PYTHON) benchmark.py

benchmark-count4:
	$(PYTHON) benchmark.py --count 4
