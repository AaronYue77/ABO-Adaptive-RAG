"""Load the accepted C adapter and keep base routing on the same 8B instance."""
from contextlib import nullcontext
from dataclasses import dataclass
import json
from pathlib import Path

from full_catalog import WORKSPACE
from common import file_hash


@dataclass(frozen=True)
class ReaderAdapter:
    directory: Path
    sha256: str

    @classmethod
    def accepted(cls, configuration=None):
        path = configuration or WORKSPACE / "data/ecommerce_rag/stage-c-active.json"
        data = json.loads(path.read_text("utf-8"))
        weights = (WORKSPACE / data["adapter"]).resolve()
        if (not weights.is_relative_to(WORKSPACE) or weights.name != "adapter_model.safetensors"
            or file_hash(weights) != data["adapter_sha256"] or data["train"] != 120 or data["dev"] != 50):
            raise ValueError("Accepted C 120/50 adapter identity mismatch")
        config = json.loads((weights.parent / "adapter_config.json").read_text("utf-8"))
        if config.get("peft_type") != "LORA":
            raise ValueError("Accepted C adapter must be LoRA")
        return cls(weights.parent, data["adapter_sha256"])

    def mount(self, runtime):
        from peft import PeftModel

        active = getattr(runtime, "reader_adapter_sha256", None)
        if active == self.sha256:
            return
        if active is not None:
            raise ValueError("A different reader adapter is already attached")
        runtime.model = PeftModel.from_pretrained(runtime.model, self.directory).eval()
        runtime.reader_adapter_sha256 = self.sha256


def base_generation(runtime, *, use_adapter=False):
    if getattr(runtime, "reader_adapter_sha256", None) and not use_adapter:
        return runtime.model.disable_adapter()
    return nullcontext()


if __name__ == "__main__":
    accepted = ReaderAdapter.accepted()
    print(f"Accepted 120/50 C reader adapter, SHA-256 {accepted.sha256}")
