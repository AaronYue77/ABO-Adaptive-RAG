"""Fetch and verify the official model snapshots used by the ABO runtime."""

import argparse
from hashlib import file_digest
from pathlib import Path

from huggingface_hub import HfApi, snapshot_download

MODELS = {
    "qwen": ("Qwen/Qwen3-VL-8B-Instruct", "0c351dd01ed87e9c1b53cbc748cba10e6187ff3b"),
    "visrag": ("openbmb/VisRAG-Ret", "95ef596df871b606167cb7e4b7215caf1bfdf761"),
    "e5": ("intfloat/multilingual-e5-small", "614241f622f53c4eeff9890bdc4f31cfecc418b3"),
}
ROOT = Path(__file__).resolve().parents[1] / "abo_runtime/models"
ALTERNATE_E5_EXPORTS = [".eval_results/*", "onnx/*", "openvino/*", "pytorch_model.bin"]


def verify(repo, revision, model_dir, *, ignore_patterns):
    from fnmatch import fnmatch

    info = HfApi().model_info(repo, revision=revision, files_metadata=True)
    files = [
        file
        for file in info.siblings
        if not any(fnmatch(file.rfilename, pattern) for pattern in ignore_patterns)
    ]
    for file in files:
        path = model_dir / file.rfilename
        if not path.is_file() or path.stat().st_size != file.size:
            raise ValueError(f"Missing or incomplete official file: {path}")
        if file.lfs:
            with path.open("rb") as stream:
                if file_digest(stream, "sha256").hexdigest() != file.lfs.sha256:
                    raise ValueError(f"Official SHA-256 mismatch: {path}")
    print(f"{repo} @ {revision}: verified {len(files)} files in {model_dir}", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("models", nargs="+", choices=MODELS)
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()
    for name in args.models:
        repo, revision = MODELS[name]
        model_dir = ROOT / repo.split("/")[1]
        ignored = ALTERNATE_E5_EXPORTS if name == "e5" else []
        if not args.verify_only:
            snapshot_download(
                repo, revision=revision, local_dir=model_dir, max_workers=4, ignore_patterns=ignored
            )
        verify(repo, revision, model_dir, ignore_patterns=ignored)


if __name__ == "__main__":
    main()
