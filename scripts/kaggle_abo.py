"""Start the new Adaptive-rag ABO profile and run the release gates on Kaggle."""

import atexit
from datetime import datetime, timedelta, timezone
import json
from hashlib import file_digest
from pathlib import Path
import os
import runpy
from shutil import copyfileobj, copytree
import subprocess
import sys
from uuid import uuid4
from urllib.request import urlopen
from zipfile import ZipFile


ROOT = Path("/kaggle/working/abo-rag")
MODEL = Path("/kaggle/input/models/qwen-lm/qwen-3-vl/transformers/8b-instruct/1")
OFFICIAL_SOURCE = "https://amazon-berkeley-objects.s3.amazonaws.com/"


def stage_release():
    inputs = Path("/kaggle/input")
    markers = list(inputs.rglob("ABO_8B_FORMAL.txt",recurse_symlinks=True))
    if markers:
        marker, = markers
        copytree(marker.parent, ROOT, dirs_exist_ok=True)
    else:
        bundle, = inputs.rglob("abo-8b-formal.zip",recurse_symlinks=True)
        with ZipFile(bundle) as archive:
            archive.extractall(ROOT)
    manifest = json.loads((ROOT/"adaptive-release-manifest.json").read_text("utf-8"))
    for relative, digest in manifest.items():
        with (ROOT/relative).open("rb") as stream:
            if file_digest(stream,"sha256").hexdigest() != digest:
                raise ValueError(f"Release checksum mismatch: {relative}")
    print(f"Verified {len(manifest)} release files.",flush=True)


def download(url, path):
    if path.is_file():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    pending = path.with_suffix(path.suffix+".part")
    with urlopen(url, timeout=120) as response, pending.open("wb") as output:
        copyfileobj(response, output)
    pending.replace(path)


def setup_catalog(workspace=ROOT):
    source = workspace/"data/ecommerce_rag/abo-source"
    snapshot = workspace/"offline/abo-source-for-kaggle.zip"
    if snapshot.is_file():
        with ZipFile(snapshot) as archive:
            archive.extractall(workspace, members=[name for name in archive.namelist()
                                                   if name.startswith(("data/", "fonts/"))])
    expected = {
        "archives/abo-listings.tar": "b7f7ceacb328fa5ab6e143b88e1f948443a877cfc95b67ff09c8ebabd50644e3",
        "images/metadata/images.csv.gz": "915263ef354d7a5d3503e899e610082f510c0011cccd5b4dc4f4c5efa86ef539",
    }
    for relative, digest in expected.items():
        path = source/relative
        download(OFFICIAL_SOURCE+relative, path)
        with path.open("rb") as stream:
            if file_digest(stream,"sha256").hexdigest() != digest:
                raise ValueError(f"Official ABO input checksum mismatch: {relative}")
    if not snapshot.is_file():
        for relative in ("README.md", "LICENSE-CC-BY-4.0.txt"):
            download(OFFICIAL_SOURCE+relative, source/relative)
    font = workspace/"fonts/NotoSansSC.ttf"
    if not font.is_file() and os.name != "nt":
        base = "https://raw.githubusercontent.com/google/fonts/2894aab31764f10f29c421bdfd2340d3b382d384/ofl/notosanssc/"
        download(base+"NotoSansSC%5Bwght%5D.ttf", font)
        download(base+"OFL.txt", font.with_name("OFL.txt"))
    environment = {**os.environ, "DOC_VQA_WORKSPACE": str(workspace), "ABO_SOURCE_DIR": str(source)}
    if font.is_file():
        os.environ["ABO_FONT_PATH"] = str(font)
    build = workspace/"upstream/VisRAG/personal/ecommerce_rag/full_catalog.py"
    subprocess.run([sys.executable, str(build)], cwd=workspace, env=environment, check=True)


def configure_graph_runtime():
    from langchain_core.globals import set_debug, set_verbose

    # Core 0.3 still checks the root LangChain flags when Kaggle preinstalls it.
    set_debug(False)
    set_verbose(False)


def start():
    log = Path("/kaggle/working/ABO启动记录.txt")

    def boot_event(text):
        with log.open("a",encoding="utf-8") as file:
            stamp=datetime.now(timezone(timedelta(hours=8))).isoformat(timespec="seconds")
            file.write(f"[{stamp}] {text}\n")

    boot_event("开始初始化离线输入、GPU 和 ABO 目录")
    try:
        session = _start()
    except Exception as error:
        boot_event(f"启动失败 · {type(error).__name__} · 查看 Notebook 执行错误")
        raise
    boot_event(f"启动完成 · {session.output}")
    atexit.register(session.close)
    return session


def _start():
    stage_release()
    if not (MODEL/"config.json").is_file():
        raise FileNotFoundError(f"Attach the official Kaggle model: {MODEL}")
    install = [sys.executable,"-m","pip","install","--no-deps"]
    wheel_dir = ROOT/"offline/wheels"
    if not wheel_dir.is_dir():
        subprocess.run([*install,"bitsandbytes==0.50.2","PyMuPDF==2.0","reportlab==5.1.0"],check=True)
        wheel_dir = ROOT/"graph-wheels"
    wheels = sorted(wheel_dir.glob("*.whl"))
    subprocess.run([*install,"--no-index",*map(str,wheels)],check=True)
    os.environ.update(HF_HUB_OFFLINE="1", HF_HOME="/tmp/abo-adaptive-hf", ABO_QWEN8_MODEL_DIR=str(MODEL),
                      ABO_QWEN8_REVISION="kaggle:qwen-lm/qwen-3-vl/transformers/8b-instruct/1",
                      DOC_VQA_WORKSPACE=str(ROOT))
    setup_catalog()
    os.chdir(ROOT)
    sys.path.insert(0,str(ROOT))
    configure_graph_runtime()
    from app.abo.service import AboDialogue

    output = ROOT/"reports/ecommerce_rag"/("abo-adaptive-kaggle-"+uuid4().hex[:10])
    output.mkdir(parents=True)
    return AboDialogue(output,gpu=True,entry="Kaggle Notebook")


def verify(session):
    from app.abo.validation import run_category_validation, run_presentation_validation, run_validation
    from common import WORKSPACE

    checks_script = WORKSPACE/"scripts/kaggle_abo_checks.py"
    if not checks_script.is_file():
        checks_script = WORKSPACE/"scripts/kaggle_stage_d_chat.py"
    smoke = runpy.run_path(str(checks_script))
    smoke["verify"](session)
    smoke["verify_categories"](session)
    result = run_validation(session,session.output/"cross-category.json",require=True)
    print(f"Cross-category regression {result['passed']}/{result['cases']}.",flush=True)
    category = run_category_validation(session,session.output/"all-category-followups.json")
    print(f"Product follow-up across source categories: {category['passed']}/{category['categories']}.",flush=True)
    presentation = run_presentation_validation(session,session.output/"presentation.json")
    print(f"Customer reply presentation: {presentation['passed']}/{presentation['cases']}.",flush=True)
    from common import read_jsonl

    records=read_jsonl(session.output/"results.jsonl")
    transcript=(session.output/"对话记录.txt").read_text("utf-8")
    events=read_jsonl(session.output/"运行事件.jsonl")
    assert {"product","compatibility"} <= {row["entry_lane"] for row in records}
    assert transcript.count("\n用户：")==len(records)
    assert events[0]["event"]=="start" and events[1]["event"]=="ready"
    print(f"Unified session transcript verified: {len(records)} turns.",flush=True)
    return result
