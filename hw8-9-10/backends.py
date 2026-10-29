"""Загрузчик реальной baseline-модели с проверкой файлов и runtime."""
import os
import threading
from pathlib import Path

import release


class RealBackend:
    kind = "transformers+peft"

    def __init__(self, manifest):
        import torch
        from transformers import AutoModelForCausalLM, AutoTokenizer

        root = Path(manifest["artifact_uri"])
        release.verify_artifact_files(root, manifest)
        runtime = manifest["runtime"]
        generation = runtime["generation"]
        self.manifest = manifest
        self.loaded_id = manifest["model_id"]
        self.runtime_sha256 = manifest["runtime_sha256"]
        self.calls = 0
        self._lock = threading.Lock()
        self._torch = torch
        self._generation = generation
        torch.set_num_threads(int(os.environ.get("SERVICE_TORCH_THREADS", "4")))
        try:
            torch.set_num_interop_threads(int(os.environ.get("SERVICE_TORCH_INTEROP_THREADS", "1")))
        except RuntimeError:
            pass
        device_name = os.environ.get("SERVICE_DEVICE", "auto")
        if device_name == "auto":
            device_name = "cuda" if torch.cuda.is_available() else "mps" if torch.backends.mps.is_available() else "cpu"
        self.device = torch.device(device_name)
        dtype = getattr(torch, os.environ.get("SERVICE_DTYPE", runtime["dtype"]))
        torch.manual_seed(generation["seed"])
        self.tokenizer = AutoTokenizer.from_pretrained(root, local_files_only=True)
        self.model = AutoModelForCausalLM.from_pretrained(
            root, revision=runtime["base_revision"], dtype=dtype, local_files_only=True,
        ).to(self.device).eval()

    def _generate(self, prompt, limit):
        torch = self._torch
        generation = self._generation
        text = self.tokenizer.apply_chat_template(
            [{"role": "system", "content": generation["system"]},
             {"role": "user", "content": prompt}],
            tokenize=False, add_generation_prompt=True,
            enable_thinking=generation["enable_thinking"],
        )
        batch = self.tokenizer(text, return_tensors="pt").to(self.device)
        with torch.inference_mode():
            output = self.model.generate(
                **batch, max_new_tokens=limit, do_sample=False,
                pad_token_id=self.tokenizer.pad_token_id or self.tokenizer.eos_token_id,
            )
        output_tokens = output[0, batch["input_ids"].shape[1]:]
        answer = self.tokenizer.decode(output_tokens, skip_special_tokens=True)
        return {"answer": answer, "output_tokens": int(output_tokens.shape[0])}

    def warmup(self):
        self._generate("Warmup", 1)

    def generate(self, prompt, limit):
        with self._lock:
            self.calls += 1
        return self._generate(prompt, limit)
