"""Start the new Adaptive-rag ABO profile and run the release gates on Kaggle."""

import atexit
from datetime import datetime, timedelta, timezone
import json
from hashlib import file_digest
from pathlib import Path
import os
import runpy
import sys
from uuid import uuid4
from zipfile import ZipFile


def mounted_script(name):
    inputs = Path("/kaggle/input")
    markers = list(inputs.rglob("ABO_8B_FORMAL.txt",recurse_symlinks=True))
    if markers:
        marker, = markers
        return marker.parent / "scripts" / name
    bundle, = inputs.rglob("abo-8b-formal.zip",recurse_symlinks=True)
    destination = Path("/kaggle/working") / name
    with ZipFile(bundle) as archive:
        destination.write_bytes(archive.read("scripts/"+name))
    return destination


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
    project = runpy.run_path(str(mounted_script("kaggle_8b_project.py")))
    project["setup"]()
    workspace = project["ROOT"]
    manifest = json.loads((workspace/"adaptive-release-manifest.json").read_text("utf-8"))
    for relative, digest in manifest.items():
        with (workspace/relative).open("rb") as stream:
            assert file_digest(stream,"sha256").hexdigest()==digest, relative
    print(f"Verified {len(manifest)} release files.",flush=True)
    project["setup_catalog"]()
    os.chdir(workspace)
    sys.path.insert(0,str(workspace))
    configure_graph_runtime()
    from app.abo.service import AboDialogue

    output = workspace/"reports/ecommerce_rag"/("abo-adaptive-kaggle-"+uuid4().hex[:10])
    output.mkdir(parents=True)
    return AboDialogue(output,gpu=True,entry="Kaggle Notebook")


def verify(session):
    from app.abo.validation import run_category_validation, run_presentation_validation, run_validation
    from common import WORKSPACE

    smoke = runpy.run_path(str(WORKSPACE/"scripts/kaggle_stage_d_chat.py"))
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
