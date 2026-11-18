"""An optional accepted dialogue adapter, independent of the frozen C reader."""

import json
from contextlib import contextmanager
from hashlib import file_digest

STAGES = {"adaptive_turn_plan", "adaptive_grounded_answer", "adaptive_grounded_repair"}


def configuration():
    from common import WORKSPACE

    path = WORKSPACE / "data/ecommerce_rag/stage-dialogue-active.json"
    if not path.is_file():
        return None
    config = json.loads(path.read_text("utf-8"))
    if (
        config["candidate_dev"]["cases"] != config["base_dev"]["cases"]
        or config["base_dev"]["cases"] <= 0
        or config["candidate_dev"]["passed"] <= config["base_dev"]["passed"]
    ):
        raise ValueError("Dialogue adapter was not selected by strict development improvement")
    return config


def prepare():
    from zipfile import ZipFile

    from common import WORKSPACE

    from app.abo.web_setup import download

    config = configuration()
    if not config:
        return
    directory = (WORKSPACE / config["directory"]).resolve()
    if not directory.is_relative_to(WORKSPACE):
        raise ValueError("Dialogue adapter path outside project runtime")
    weights = directory / "adapter_model.safetensors"
    adapter_config = directory / "adapter_config.json"
    if weights.is_file() and adapter_config.is_file():
        with weights.open("rb") as stream:
            weight_ok = file_digest(stream, "sha256").hexdigest() == config["adapter_sha256"]
        with adapter_config.open("rb") as stream:
            config_ok = (
                not config.get("adapter_config_sha256")
                or file_digest(stream, "sha256").hexdigest() == config["adapter_config_sha256"]
            )
        if weight_ok and config_ok:
            return
    archive = WORKSPACE / "downloads" / config["archive"]
    download(config["url"], archive, config["archive_sha256"])
    with ZipFile(archive) as package:
        directory.mkdir(parents=True, exist_ok=True)
        for name in ("adapter_config.json", "adapter_model.safetensors"):
            (directory / name).write_bytes(package.read(name))
    with weights.open("rb") as stream:
        if file_digest(stream, "sha256").hexdigest() != config["adapter_sha256"]:
            raise ValueError("Dialogue adapter digest mismatch")
    with adapter_config.open("rb") as stream:
        if (
            config.get("adapter_config_sha256")
            and file_digest(stream, "sha256").hexdigest() != config["adapter_config_sha256"]
        ):
            raise ValueError("Dialogue adapter configuration digest mismatch")


@contextmanager
def generation_context(runtime, stage, *, use_adapter=False):
    from c8_reader import base_generation

    if stage not in STAGES:
        with base_generation(runtime, use_adapter=use_adapter):
            yield
        return
    config = configuration()
    if not config:
        with base_generation(runtime, use_adapter=False):
            yield
        return
    from common import WORKSPACE
    from peft import PeftModel

    directory = (WORKSPACE / config["directory"]).resolve()
    if not directory.is_relative_to(WORKSPACE):
        raise ValueError("Dialogue adapter path outside project runtime")
    previous = getattr(runtime.model, "active_adapter", None)
    if getattr(runtime, "dialogue_adapter_sha256", None) != config["adapter_sha256"]:
        with (directory / "adapter_model.safetensors").open("rb") as stream:
            if file_digest(stream, "sha256").hexdigest() != config["adapter_sha256"]:
                raise ValueError("Dialogue adapter digest mismatch")
        if isinstance(runtime.model, PeftModel):
            runtime.model.load_adapter(directory, adapter_name="abo_dialogue")
        else:
            runtime.model = PeftModel.from_pretrained(
                runtime.model, directory, adapter_name="abo_dialogue"
            ).eval()
        runtime.dialogue_adapter_sha256 = config["adapter_sha256"]
    runtime.model.set_adapter("abo_dialogue")
    try:
        yield
    finally:
        if previous and previous != "abo_dialogue":
            runtime.model.set_adapter(previous)
