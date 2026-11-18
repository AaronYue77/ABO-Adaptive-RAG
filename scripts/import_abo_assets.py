"""Restore the standalone Kaggle release's data and adapter on Windows."""

import argparse
import json
from hashlib import file_digest
from pathlib import Path
from zipfile import ZipFile

from kaggle_abo import setup_catalog

ROOT = Path(__file__).resolve().parents[1] / "abo_runtime"
SOURCE = Path("offline/abo-source-for-kaggle.zip")


def restore(bundle):
    with ZipFile(bundle) as release:
        manifest = json.loads(release.read("adaptive-release-manifest.json"))
        for name, digest in manifest.items():
            if not (name.startswith(("data/", "reports/")) or name == SOURCE.as_posix()):
                continue
            with release.open(name) as stream:
                if file_digest(stream, "sha256").hexdigest() != digest:
                    raise ValueError(f"Release checksum mismatch: {name}")
            target = ROOT / name
            if target.is_file():
                with target.open("rb") as stream:
                    if file_digest(stream, "sha256").hexdigest() != digest:
                        raise FileExistsError(f"Local file differs from release: {target}")
            else:
                release.extract(name, ROOT)
    setup_catalog(ROOT)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", required=True, type=Path)
    restore(parser.parse_args().bundle)
