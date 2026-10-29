"""FastAPI-сервис с общим интерфейсом Stub/Real backend."""
import argparse
import json
import threading
import time
import uuid
from pathlib import Path

import uvicorn
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field, field_validator


class StubBackend:
    kind = "STUB ONLY"

    def __init__(self, manifest):
        self.loaded_id = manifest["model_id"]
        self.runtime_sha256 = manifest["runtime_sha256"]
        self.calls = 0
        self._lock = threading.Lock()

    def generate(self, prompt, limit):
        with self._lock:
            self.calls += 1
        time.sleep(0.01)
        return {"answer": f"STUB ONLY: {self.loaded_id}", "output_tokens": 1}


class ServiceState:
    def __init__(self, root, warmup_seconds, backend_name="stub"):
        self.root = Path(root)
        self.warmup_seconds = warmup_seconds
        self.backend_name = backend_name
        self.backend = None
        self.manifest = None
        self.ready = False
        self.error = None
        self.warmup_finished = threading.Event()
        self.semaphore = threading.BoundedSemaphore(1)
        threading.Thread(target=self._load_and_warmup, daemon=True).start()

    def _load_and_warmup(self):
        started = time.perf_counter()
        try:
            manifest = json.loads((self.root / "accepted.json").read_text())
            if self.backend_name == "stub":
                import release
                release.verify_artifact_files(self.root, manifest)
                backend = StubBackend(manifest)
            else:
                from backends import RealBackend
                backend = RealBackend(manifest)
            self.manifest = manifest
            self.backend = backend
            remaining = self.warmup_seconds - (time.perf_counter() - started)
            if remaining > 0:
                time.sleep(remaining)
            self.ready = True
            self.warmup_finished.set()
        except Exception as error:
            self.error = str(error)
            self.warmup_finished.set()

    def generate(self, request):
        if not self.ready or self.backend is None:
            raise HTTPException(status_code=503, detail="model is not ready")
        started = time.perf_counter()
        with self.semaphore:
            generation_started = time.perf_counter()
            result = self.backend.generate(request.prompt, request.max_new_tokens)
        finished = time.perf_counter()
        result.update(
            model_id=self.manifest["model_id"],
            runtime_sha256=self.manifest["runtime_sha256"],
            request_id=str(uuid.uuid4()),
            queue_seconds=generation_started - started,
            generation_seconds=finished - generation_started,
            total_seconds=finished - started,
        )
        return result


class GenerateRequest(BaseModel):
    prompt: str = Field(min_length=1, max_length=4096, strict=True)
    max_new_tokens: int = Field(default=32, ge=1, le=32, strict=True)

    @field_validator("prompt")
    @classmethod
    def prompt_is_not_blank(cls, value):
        if not value.strip():
            raise ValueError("prompt must not be blank")
        return value


def create_app(state):
    app = FastAPI(title="Model release service")

    @app.get("/health")
    def health():
        return {"alive": True, "backend": "STUB ONLY" if state.backend_name == "stub" else "transformers+peft"}

    @app.get("/ready")
    def ready():
        payload = {"ready": state.ready, "error": state.error}
        if not state.ready:
            raise HTTPException(status_code=503, detail=payload)
        return payload

    @app.get("/debug")
    def debug():
        if state.backend_name != "stub":
            raise HTTPException(status_code=404, detail="not found")
        return {
            "loaded_id": state.manifest["model_id"] if state.manifest else None,
            "calls": state.backend.calls if state.backend else 0,
            "warmup_finished": state.warmup_finished.is_set(),
        }

    @app.post("/generate")
    def generate(request: GenerateRequest):
        return state.generate(request)

    return app


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", type=Path, default=Path(".lab"))
    parser.add_argument("--port", type=int, default=8091)
    parser.add_argument("--warmup-seconds", type=float, default=5)
    parser.add_argument("--backend", choices=("stub", "real"), default="stub")
    args = parser.parse_args()
    state = ServiceState(args.workspace, args.warmup_seconds, args.backend)
    uvicorn.run(create_app(state), host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
