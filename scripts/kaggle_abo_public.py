"""Start ABO Qwen 8B on Kaggle using only published inference assets."""

import atexit
import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from uuid import uuid4
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[1]
MODEL_REF = "qwen-lm/qwen-3-vl/Transformers/8b-instruct/1"
MODEL = Path("/kaggle/input/models") / MODEL_REF.lower()
ADAPTER_RELEASE = "qwen3-vl-8b-abo-lora-v1"
ADAPTER_ARCHIVE = "abo-qwen3-vl-8b-abo-lora-v1.zip"
ADAPTER_URL = f"https://github.com/AaronYue77/ABO-Adaptive-RAG/releases/download/{ADAPTER_RELEASE}/{ADAPTER_ARCHIVE}"
ARCHIVE_SHA = "452a3d67ec0be24798f960c0bde0b70c282eb754507a1a76f0d7d54048535350"
ADAPTER_SHA = "8a67f16042347215b7020d1d4216cd37127cb820ab4460f634a64d7628b34587"


def prepare_adapter(workspace):
    from common import log_lock, write_json

    from app.abo.web_setup import download

    directory = workspace / "models" / ADAPTER_RELEASE
    weights = directory / "adapter_model.safetensors"
    with log_lock(directory):
        release = workspace / "downloads" / ADAPTER_ARCHIVE
        download(ADAPTER_URL, release, ARCHIVE_SHA)
        with ZipFile(release) as package:
            model = package.read(weights.name)
            if sha256(model).hexdigest() != ADAPTER_SHA:
                raise ValueError("Published LoRA weights do not match the accepted adapter.")
            directory.mkdir(parents=True, exist_ok=True)
            weights.write_bytes(model)
            (directory / "adapter_config.json").write_bytes(package.read("adapter_config.json"))
        active = workspace / "data/ecommerce_rag/stage-c-active.json"
        active.parent.mkdir(parents=True, exist_ok=True)
        write_json(
            active,
            {
                "adapter": weights.relative_to(workspace).as_posix(),
                "adapter_sha256": ADAPTER_SHA,
                "train": 120,
                "dev": 50,
                "release": ADAPTER_RELEASE,
            },
        )


def start():
    workspace = ROOT / "abo_runtime"
    log = workspace / "reports" / "public-kaggle-startup.txt"
    log.parent.mkdir(parents=True, exist_ok=True)

    def event(message):
        with log.open("a", encoding="utf-8") as stream:
            stream.write(f'[{datetime.now(UTC).isoformat(timespec="seconds")}] {message}\n')
        print(message, flush=True)

    event("准备公开 ABO 数据与 Qwen3-VL-8B")
    try:
        session = initialize(workspace, event)
    except Exception as error:
        event(f"初始化失败 · {type(error).__name__} · 请查看当前代码单元的错误")
        raise
    atexit.register(session.close)
    event("ABO 商品助手已就绪")
    print("当前会话日志", session.output, flush=True)
    return session


def initialize(workspace, event):
    if not (MODEL / "config.json").is_file():
        raise FileNotFoundError(
            f"请在 Kaggle 的 Add Input > Models 添加 {MODEL_REF}。预期挂载目录 {MODEL}"
        )
    if not os.environ.get("CUDA_VISIBLE_DEVICES", "0"):
        raise RuntimeError("请在 Kaggle Session options 中启用 NVIDIA GPU。")
    os.environ.update(
        DOC_VQA_WORKSPACE=str(workspace),
        ABO_FONT_PATH=str(workspace / "fonts/NotoSansSC.ttf"),
        HF_HUB_OFFLINE="1",
        ABO_QWEN8_MODEL_DIR=str(MODEL),
        ABO_QWEN8_REVISION="kaggle:" + MODEL_REF.lower(),
    )
    event("安装公开的推理依赖")
    subprocess.run(
        [
            sys.executable,
            "-m",
            "pip",
            "install",
            "--quiet",
            "--disable-pip-version-check",
            "-r",
            str(ROOT / "requirements-abo.txt"),
            "PyMuPDF==2.0",
            "reportlab==5.1.0",
            "bitsandbytes==0.50.2",
            "transformers>=4.57,<6",
            "accelerate>=1.1,<2",
            "peft>=0.17.1,<1",
        ],
        check=True,
    )

    import torch

    if not torch.cuda.is_available():
        raise RuntimeError("请在 Kaggle Session options 中启用 NVIDIA GPU。")
    from langchain_core.globals import set_debug, set_verbose

    from app.abo.service import AboDialogue, bind_runtime
    from app.abo.web_setup import prepare

    set_debug(False)
    set_verbose(False)
    bind_runtime()
    event(f"使用 GPU {torch.cuda.get_device_name(0)}")
    prepare_adapter(workspace)
    prepare()
    from app.abo.dialogue_adapter import prepare as prepare_dialogue_adapter

    prepare_dialogue_adapter()
    out = workspace / "reports/ecommerce_rag" / ("abo-public-kaggle-" + uuid4().hex[:10])
    out.mkdir(parents=True)
    return AboDialogue(out, gpu=True, entry="公开 Kaggle Notebook")


def verify(chat):
    """Preserve both successful and failed cloud acceptance artifacts."""
    try:
        return verify_checks(chat)
    finally:
        output = Path("/kaggle/working")
        if output.is_dir():
            from zipfile import ZIP_DEFLATED

            archive = output / ("abo-session-" + chat.output.name + ".zip")
            with ZipFile(archive, "w", compression=ZIP_DEFLATED) as package:
                for path in chat.output.rglob("*"):
                    if path.is_file():
                        package.write(path, path.relative_to(chat.output))


def verify_checks(chat):
    """Check real GPU calls and the browser-readable response using public demo questions."""
    import torch
    from common import write_json

    config = json.loads((chat.output / "config.json").read_text("utf-8"))
    manifest = chat.app.retriever.manifest
    assert config["gpu"] and config["adapter_sha256"] == ADAPTER_SHA, config
    assert (manifest["records"], len(manifest["product_types"]), len(manifest["domains"])) == (
        147702,
        576,
        32,
    )
    checks = []
    for question, expected in (
        ("手表类商品有多少个？", "catalog_count"),
        ("有哪些电风扇，给我看看图片", "search_results"),
        ("你好", "chat"),
        ("帮我看图，B07T9TLVJ3能配 iPhone 8 Plus 吗？", "compatible"),
    ):
        row = chat.respond(question)
        result = {
            "question": question,
            "action": row["decision"],
            "answer": row["display_answer"],
            "model_executed": any(
                call.get("model_executed") for call in row.get("model_calls", [])
            ),
            "visual_observation": bool(row.get("visual_observation")),
            "citations": row["citations"],
            "issue_id": row.get("issue_id"),
        }
        checks.append(result)
        write_json(
            chat.output / "public-demo-verify-progress.json", {"checks": checks, "completed": False}
        )
        print(json.dumps(result, ensure_ascii=False), flush=True)
        assert result["action"] == expected and not result["issue_id"], result
        if expected in {"chat", "compatible"}:
            assert result["model_executed"], result
        if expected == "compatible":
            assert result["visual_observation"] and result["citations"], result
    reader = chat.app.reader
    assert reader.load_count == 1 and reader.reader.vlm.reader_adapter_sha256 == ADAPTER_SHA
    report = {
        "model": "Qwen3-VL-8B-Instruct",
        "adapter_sha256": ADAPTER_SHA,
        "gpu": torch.cuda.get_device_name(0),
        "catalog_records": manifest["records"],
        "checks": checks,
    }
    write_json(chat.output / "public-demo-verify.json", report)
    write_json(Path("/kaggle/working/abo-public-validation.json"), report)
    from app.abo.state import clear_state

    clear_state(chat, "公开演示验收完成，恢复空上下文；这是程序事件，并非用户输入。")
    print("公开资源验收通过 · 检索、真实 8B 调用、LoRA 读图和来源", flush=True)
    return report
