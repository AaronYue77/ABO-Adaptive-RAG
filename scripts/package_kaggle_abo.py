"""Build a self-contained private Notebook fixture from the new project."""

import argparse
import json
from hashlib import sha256
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = "aaronyue77/abo-adaptive-rag-product-assistant-fixture"
SLUG = "abo-adaptive-rag-product-assistant-8b"
TITLE = "ABO Adaptive RAG Product Assistant 8B"
OLD_WORKSPACE = ROOT.parent/"doc-vqa-assistant"


def notebook():
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

def ask(text):
    row = chat.respond(text)
    print(row["answer"])
    for citation in row["citations"]:
        print("来源", citation)
    return row

print("ABO Adaptive RAG 商品助手已就绪")
'''
    verify = 'checks = bootstrap["verify"](chat)\n'
    sample = 'ask("重置");\nask("我想查询电风扇这类商品");\n'
    description = ("# ABO Adaptive RAG 商品助手\n"
                   "基于 Adaptive-rag 的独立 ABO 项目。运行第一个代码单元格后，可调用 ask()。\n"
                   "支持历史商品取证、规格比较、原图、配件兼容和现有售后政策快照。\n")
    cells = [{"id":"intro","cell_type":"markdown","metadata":{},"source":description.splitlines(keepends=True)}]
    for key,source in (("start",init),("verify",verify),("sample",sample)):
        compile(source,key,"exec")
        cells.append({"id":key,"cell_type":"code","metadata":{},"source":source.splitlines(keepends=True),
                      "execution_count":None,"outputs":[]})
    return {"cells":cells,"metadata":{"kernelspec":{"display_name":"Python 3","language":"python","name":"python3"}},
            "nbformat":4,"nbformat_minor":5}


def prepare(base,metadata):
    fixture = ROOT/".release/fixture"
    kernel = ROOT/".release/kernel"
    fixture.mkdir(parents=True,exist_ok=True)
    kernel.mkdir(parents=True,exist_ok=True)
    updates = {}
    library = ROOT/"abo_runtime"
    for path in sorted((library/"upstream/VisRAG/personal").rglob("*.py")):
        if not path.name.startswith("test_"):
            updates[path.relative_to(library).as_posix()] = path.read_bytes()
    for group in ("abo","agents","core"):
        for path in (ROOT/"app"/group).glob("*.py"):
            if group!="agents" or path.name in {"__init__.py","abo_graph.py"}:
                updates[path.relative_to(ROOT).as_posix()] = path.read_bytes()
    updates["app/__init__.py"]=(ROOT/"app/__init__.py").read_bytes()
    updates["scripts/kaggle_abo.py"]=(ROOT/"scripts/kaggle_abo.py").read_bytes()
    policy = "data/ecommerce_rag/policies-us-v1/clauses.json"
    updates[policy]=(library/policy).read_bytes()
    wheels = list((ROOT/".release/wheels").glob("*.whl"))
    assert any(path.name.startswith("langgraph-") for path in wheels)
    for wheel in wheels:
        updates[f"graph-wheels/{wheel.name}"]=wheel.read_bytes()
    checks = {path:sha256(content).hexdigest() for path,content in updates.items()}
    updates["adaptive-release-manifest.json"]=json.dumps(checks,indent=2).encode("utf-8")
    with ZipFile(base) as original, ZipFile(fixture/"abo-8b-formal.zip","w",compression=ZIP_DEFLATED) as output:
        for entry in original.infolist():
            if entry.filename not in updates:
                output.writestr(entry.filename,original.read(entry.filename))
        for path,content in updates.items():
            output.writestr(path,content)
    with ZipFile(fixture/"abo-8b-formal.zip") as output:
        assert output.testzip() is None
        assert all(sha256(output.read(path)).hexdigest()==digest for path,digest in checks.items())
    (fixture/"dataset-metadata.json").write_text(json.dumps(
        {"id":FIXTURE,"title":"ABO Adaptive RAG product assistant release fixture",
         "licenses":[{"name":"CC-BY-4.0"}]},indent=2)+"\n","utf-8")
    config = json.loads(metadata.read_text("utf-8"))
    keys=("language","kernel_type","is_private","enable_gpu","enable_internet","model_sources","keywords")
    release={key:config[key] for key in keys}
    release.update(id=f"aaronyue77/{SLUG}",title=TITLE,code_file=f"{SLUG}.ipynb",
                   dataset_sources=[FIXTURE,*config["dataset_sources"][1:]])
    (kernel/"kernel-metadata.json").write_text(json.dumps(release,indent=2)+"\n","utf-8")
    data=json.dumps(notebook(),ensure_ascii=False,indent=2)+"\n"
    (kernel/f"{SLUG}.ipynb").write_text(data,"utf-8")
    (ROOT/"scripts/kaggle_abo.ipynb").write_text(data,"utf-8")
    print(f"{len(checks)} source and dependency hashes validated.",flush=True)


if __name__=="__main__":
    source=OLD_WORKSPACE/"reports/maintenance/abo-service-scope-20261116/launcher-final"
    parser=argparse.ArgumentParser()
    parser.add_argument("--base",type=Path,default=source/"fixture/abo-8b-formal.zip")
    parser.add_argument("--metadata",type=Path,default=source/"daily-after/kernel-metadata.json")
    args=parser.parse_args()
    prepare(args.base,args.metadata)
