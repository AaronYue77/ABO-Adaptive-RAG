"""Build a self-contained private Notebook fixture from the new project."""

import argparse
import json
from hashlib import file_digest
from pathlib import Path
from tempfile import NamedTemporaryFile
from zipfile import ZIP_DEFLATED, ZipFile


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = "aaronyue77/abo-adaptive-rag-product-assistant-fixture"
SLUG = "abo-adaptive-rag-product-assistant-8b"
TITLE = "ABO Adaptive RAG Product Assistant 8B"
CATALOG = Path("data/ecommerce_rag/abo-full-v1")
ADAPTER = Path("reports/ecommerce_rag/stage-c-8b-120-20261110/adapter")


def notebook(online=False):
    init = '''from pathlib import Path
from zipfile import ZipFile
import runpy

if "chat" not in globals():
    inputs = Path("/kaggle/input")
    markers = list(inputs.rglob("ABO_8B_FORMAL.txt", recurse_symlinks=True))
    if markers:
        marker, = markers
        script = marker.parent / "scripts/kaggle_abo.py"
    else:
        bundle, = inputs.rglob("abo-8b-formal.zip", recurse_symlinks=True)
        script = Path("/kaggle/working/kaggle_abo.py")
        with ZipFile(bundle) as archive:
            script.write_bytes(archive.read("scripts/kaggle_abo.py"))
    bootstrap = runpy.run_path(str(script))
    chat = bootstrap["start"]()

def ask(text, *, details=False):
    row = chat.respond(text)
    print(row["display_answer"])
    for label in dict.fromkeys(source["label"] for source in row["display_sources"]):
        print("来源", label)
    if details:
        return row

def close_chat():
    if "chat" in globals():
        chat.close()
        print("会话日志", chat.output)
        del globals()["chat"]

print("ABO Adaptive RAG 商品助手已就绪")
print("会话日志", chat.output)
'''
    if online:
        start = init.index('    inputs = Path("/kaggle/input")')
        end = init.index('    bootstrap = runpy.run_path(str(script))')
        code = (ROOT/"scripts/kaggle_abo.py").read_text("utf-8")
        init = (init[:start] + '    script = Path("/kaggle/working/standalone_abo.py")\n'
                + f'    script.write_text({code!r}, encoding="utf-8")\n' + init[end:])
    verify = 'checks = bootstrap["verify"](chat)\n'
    sample = 'ask("重置");\nask("我想查询电风扇这类商品");\n'
    description = ("# ABO Adaptive RAG 商品助手\n"
                   "基于 Adaptive-rag 的独立 ABO 项目。运行第一个代码单元格后，可调用 ask()。\n"
                   "支持历史商品取证、规格比较、原图、配件兼容和现有售后政策快照。\n"
                   "日志随问答写入显示的会话目录。待会话结束时可以调用 close_chat()。\n")
    cells = [{"id":"intro","cell_type":"markdown","metadata":{},"source":description.splitlines(keepends=True)}]
    for key,source in (("start",init),("verify",verify),("sample",sample),("close","close_chat()\n")):
        compile(source,key,"exec")
        cells.append({"id":key,"cell_type":"code","metadata":{},"source":source.splitlines(keepends=True),
                      "execution_count":None,"outputs":[]})
    return {"cells":cells,"metadata":{"kernelspec":{"display_name":"Python 3","language":"python","name":"python3"}},
            "nbformat":4,"nbformat_minor":5}


def prepare(cloud_input):
    fixture = ROOT/".release/fixture"
    kernel = ROOT/".release/kernel"
    fixture.mkdir(parents=True,exist_ok=True)
    kernel.mkdir(parents=True,exist_ok=True)
    files = {}
    library = ROOT/"abo_runtime"
    for path in sorted((library/"upstream/VisRAG/personal").rglob("*.py")):
        if not path.name.startswith("test_"):
            files[path.relative_to(library).as_posix()] = path
    for group in ("abo","agents","core"):
        for path in (ROOT/"app"/group).glob("*.py"):
            if group!="agents" or path.name in {"__init__.py","abo_graph.py"}:
                files[path.relative_to(ROOT).as_posix()] = path
    for name in ("app/__init__.py", "scripts/kaggle_abo.py", "scripts/kaggle_abo_checks.py"):
        files[name] = ROOT/name
    for name in ("data/ecommerce_rag/stage-c-active.json", "data/ecommerce_rag/policies-us-v1/clauses.json",
                 *[f"data/ecommerce_rag/abo-stage-d-v1/{name}" for name in ("cases.jsonl", "protocol.json", "router.json")],
                 *(ADAPTER/name for name in ("adapter_config.json", "adapter_model.safetensors"))):
        files[Path(name).as_posix()] = library/name
    image_key = "896a0727c940e9d8d717262e14927ec37530de2f59ac0c78dcdec6ca91d6b879.jpg"
    for name in (image_key, image_key+".json"):
        path = CATALOG/"images"/name
        files[path.as_posix()] = library/path
    source = cloud_input/"abo-source-for-kaggle.zip"
    files["offline/abo-source-for-kaggle.zip"] = source
    graph_wheels = sorted((ROOT/".release/wheels").glob("*.whl"))
    model_wheels = sorted(cloud_input.glob("*.whl"))
    for wheels, prefixes in ((graph_wheels, ("langgraph-",)),
                             (model_wheels, ("bitsandbytes-", "pymupdf-", "reportlab-"))):
        for prefix in prefixes:
            if not any(path.name.startswith(prefix) for path in wheels):
                raise FileNotFoundError(f"Missing offline wheel: {prefix}")
    for wheel in (*graph_wheels, *model_wheels):
        files[f"offline/wheels/{wheel.name}"] = wheel

    checks = {}
    with NamedTemporaryFile(dir=fixture, suffix=".tmp", delete=False) as pending:
        temporary = Path(pending.name)
    try:
        with ZipFile(temporary,"w",compression=ZIP_DEFLATED) as output:
            for name,path in sorted(files.items()):
                with path.open("rb") as stream:
                    checks[name] = file_digest(stream,"sha256").hexdigest()
                output.write(path,name)
            output.writestr("ABO_8B_FORMAL.txt", "ABO Adaptive RAG · standalone release\n")
            output.writestr("adaptive-release-manifest.json",json.dumps(checks,indent=2))
        with ZipFile(temporary) as output:
            for name,digest in checks.items():
                with output.open(name) as stream:
                    if file_digest(stream,"sha256").hexdigest() != digest:
                        raise ValueError(f"Packed file changed: {name}")
        temporary.replace(fixture/"abo-8b-formal.zip")
    finally:
        temporary.unlink(missing_ok=True)
    (fixture/"dataset-metadata.json").write_text(json.dumps(
        {"id":FIXTURE,"title":"ABO Adaptive RAG product assistant release fixture",
         "licenses":[{"name":"CC-BY-4.0"}]},indent=2)+"\n","utf-8")
    write_notebook(kernel)
    print(f"{len(checks)} source and dependency hashes validated.",flush=True)


def write_notebook(kernel, online=False):
    kernel.mkdir(parents=True, exist_ok=True)
    release = dict(id=f"aaronyue77/{SLUG}",title=TITLE,code_file=f"{SLUG}.ipynb",language="python",
                   kernel_type="notebook",is_private=True,enable_gpu=True,enable_internet=online,keywords=["gpu"],
                   dataset_sources=[FIXTURE],model_sources=["qwen-lm/qwen-3-vl/Transformers/8b-instruct/1"])
    (kernel/"kernel-metadata.json").write_text(json.dumps(release,indent=2)+"\n","utf-8")
    data=json.dumps(notebook(online),ensure_ascii=False,indent=2)+"\n"
    (kernel/f"{SLUG}.ipynb").write_text(data,"utf-8")
    (ROOT/"scripts/kaggle_abo.ipynb").write_text(data,"utf-8")


if __name__=="__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("--cloud-input",type=Path,default=ROOT/".release/cloud-input")
    parser.add_argument("--online-notebook", action="store_true")
    args=parser.parse_args()
    if args.online_notebook:
        write_notebook(ROOT/".release/kernel", online=True)
    else:
        prepare(args.cloud_input)
