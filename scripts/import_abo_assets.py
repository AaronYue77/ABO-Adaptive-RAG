"""Copy prepared ABO assets into this project from an existing workspace."""

import argparse
from hashlib import file_digest
import json
from pathlib import Path
from shutil import copy2
from tempfile import NamedTemporaryFile


ROOT = Path(__file__).resolve().parents[1] / "abo_runtime"
CATALOG = Path("data/ecommerce_rag/abo-full-v1")
ADAPTER = Path("reports/ecommerce_rag/stage-c-8b-120-20261110/adapter")
DIRECTORIES = (
    Path("data/ecommerce_rag/abo-stage-d-v1"),
    Path("data/ecommerce_rag/abo-full-evaluation-v2"),
    Path("data/ecommerce_rag/policies-us-v1"),
    ADAPTER,
)
FILES = (
    CATALOG/"catalog.sqlite", CATALOG/"manifest.json", CATALOG/"dense-e5.npy",
    CATALOG/"dense-e5-manifest.json", CATALOG/"ATTRIBUTION.txt",
    CATALOG/"SOURCE-LICENSE-CC-BY-4.0.txt", Path("data/ecommerce_rag/stage-c-active.json"),
)


def digest(path):
    with path.open("rb") as stream:
        return file_digest(stream,"sha256").hexdigest()


def import_assets(source):
    config=json.loads((source/"data/ecommerce_rag/stage-c-active.json").read_text("utf-8"))
    catalog=json.loads((source/CATALOG/"manifest.json").read_text("utf-8"))
    assert catalog["records"]==147702 and len(catalog["product_types"])==576
    assert digest(source/CATALOG/"catalog.sqlite")==catalog["database_sha256"]
    assert digest(source/ADAPTER/"adapter_model.safetensors")==config["adapter_sha256"]
    paths = list(FILES)
    for directory in DIRECTORIES:
        entries = [path.relative_to(source) for path in (source/directory).rglob("*") if path.is_file()]
        if not entries:
            raise FileNotFoundError(source/directory)
        paths.extend(entries)
    changes = 0
    for relative in paths:
        original, target = source/relative, ROOT/relative
        if target.is_file():
            if digest(original)!=digest(target):
                raise FileExistsError(f"Existing asset differs from input: {relative}")
            continue
        target.parent.mkdir(parents=True,exist_ok=True)
        with NamedTemporaryFile(dir=target.parent,delete=False) as pending:
            temp=Path(pending.name)
        try:
            copy2(original,temp)
            if digest(original)!=digest(temp):
                raise ValueError(f"Asset copy checksum mismatch: {relative}")
            temp.replace(target)
        finally:
            temp.unlink(missing_ok=True)
        changes+=1
    print(f"{len(paths)} ABO assets verified; {changes} files copied into {ROOT}.")


if __name__=="__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("--source-workspace",required=True,type=Path)
    args=parser.parse_args()
    import_assets(args.source_workspace.resolve())
