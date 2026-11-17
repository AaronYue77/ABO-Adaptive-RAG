"""Build the first-download runtime from the official public ABO snapshot."""

from hashlib import file_digest
import json
import os
from pathlib import Path
from shutil import copy2, copyfileobj
import subprocess
import sys
from tempfile import TemporaryDirectory
from urllib.error import URLError
from urllib.request import urlopen

from app.abo.service import bind_runtime


ASSETS = Path(__file__).with_name('runtime_assets')
ABO = 'https://amazon-berkeley-objects.s3.amazonaws.com/'
FONT = 'https://raw.githubusercontent.com/google/fonts/2894aab31764f10f29c421bdfd2340d3b382d384/ofl/notosanssc/'
ABO_FILES = {
    'archives/abo-listings.tar': 'b7f7ceacb328fa5ab6e143b88e1f948443a877cfc95b67ff09c8ebabd50644e3',
    'images/metadata/images.csv.gz': '915263ef354d7a5d3503e899e610082f510c0011cccd5b4dc4f4c5efa86ef539',
    'README.md': None,
    'LICENSE-CC-BY-4.0.txt': None,
}


def sha(path):
    with path.open('rb') as stream:
        return file_digest(stream,'sha256').hexdigest()


def download(url, target, expected=None):
    if target.is_file() and target.stat().st_size and (expected is None or sha(target) == expected):
        return
    target.parent.mkdir(parents=True,exist_ok=True)
    pending=target.with_suffix(target.suffix+'.part')
    for attempt in range(3):
        print(f'Downloading {target.name}...',flush=True)
        try:
            with urlopen(url,timeout=120) as response, pending.open('wb') as stream:
                copyfileobj(response,stream,length=1024*1024)
            if expected and sha(pending) != expected:
                raise ValueError(f'Checksum mismatch for {target.name}')
            pending.replace(target)
            return
        except (URLError,OSError,ValueError):
            pending.unlink(missing_ok=True)
            if attempt == 2:
                raise


def catalog_ready(directory):
    if not (directory/'catalog.sqlite').is_file() or not (directory/'manifest.json').is_file():
        return False
    try:
        info=json.loads((directory/'manifest.json').read_text('utf-8'))
        return (info['records']==info['fts_records']==147702 and len(info['product_types'])==576 and
                len(info['domains'])==32 and (directory/'catalog.sqlite').stat().st_size>0)
    except (KeyError,TypeError,ValueError):
        return False


def prepare():
    workspace=bind_runtime()
    from common import log_lock, operation

    data=workspace/'data/ecommerce_rag'
    catalog=data/'abo-full-v1'
    source=data/'abo-source'
    font=workspace/'fonts/NotoSansSC.ttf'
    with log_lock(workspace/'.web-setup'):
        for asset in ASSETS.rglob('*.json'):
            dest=data/asset.relative_to(ASSETS)
            if not dest.is_file():
                dest.parent.mkdir(parents=True,exist_ok=True)
                copy2(asset,dest)
        download(FONT+'NotoSansSC%5Bwght%5D.ttf',font,
                 'a3041811a78c361b1de50f953c805e0244951c21c5bd412f7232ef0d899af0da')
        download(FONT+'OFL.txt',font.with_name('OFL.txt'),
                 '1c05c68c34f9708415aada51f17e1b0092d2cea709bf4a94cd38114f9e73d7d9')
        if not catalog_ready(catalog):
            for relative, checksum in ABO_FILES.items():
                download(ABO+relative,source/relative,checksum)
            script=workspace/'upstream/VisRAG/personal/ecommerce_rag/full_catalog.py'
            print('Building the full ABO search index. This runs once on a new computer.',flush=True)
            with TemporaryDirectory(dir=data,prefix='.web-catalog-') as folder:
                staging=Path(folder)
                env={**os.environ,'DOC_VQA_WORKSPACE':str(staging),'ABO_SOURCE_DIR':str(source)}
                subprocess.run([sys.executable,str(script)], env=env,check=True)
                built=staging/'data/ecommerce_rag/abo-full-v1'
                if not catalog_ready(built):
                    raise RuntimeError('The built catalog did not pass the full coverage check.')
                catalog.mkdir(parents=True,exist_ok=True)
                # Publish the manifest last. If the process stops early the next run can rebuild.
                for name in ('SOURCE-README.md','SOURCE-LICENSE-CC-BY-4.0.txt','catalog.sqlite','manifest.json'):
                    (built/name).replace(catalog/name)
            operation('网页首次构建 ABO 目录','下载官方源、核对摘要并在临时目录构建索引。',
                      '索引构建完成。',[catalog/'catalog.sqlite'],
                      '147702 个商品记录和全文索引行，覆盖 576 类与 32 站点。')
    print('ABO catalog ready · 147,702 listings · 576 categories · 32 marketplaces',flush=True)


if __name__ == '__main__':
    prepare()
