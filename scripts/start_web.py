"""Prepare the public ABO catalog and open the local web app."""

import argparse
from hashlib import file_digest
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
from threading import Thread
import time
from urllib.error import URLError
from urllib.request import urlopen
import venv
import webbrowser


ROOT = Path(__file__).resolve().parents[1]
REQUIREMENTS = ROOT / 'requirements-abo.txt'


def python_for_project():
    if sys.prefix != sys.base_prefix:
        return Path(sys.executable)
    directory = ROOT / '.venv-web'
    python = directory / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
    if not python.is_file():
        print('Creating a project Python environment...', flush=True)
        venv.create(directory, with_pip=True)
    return python


def open_when_ready(port):
    url = f'http://127.0.0.1:{port}/'
    for _ in range(100):
        try:
            with urlopen(url, timeout=1) as response:
                if response.status == 200:
                    webbrowser.open(url)
                    return
        except (URLError, OSError):
            time.sleep(0.2)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=8769)
    parser.add_argument('--no-browser', action='store_true')
    parser.add_argument('--prepare-only', action='store_true')
    parser.add_argument('--skip-install', action='store_true', help='Use packages already installed in this environment.')
    args = parser.parse_args()
    if sys.version_info < (3, 11):
        parser.error('Python 3.11 or newer is required.')
    if not 1 <= args.port <= 65535:
        parser.error('Port must be 1–65535.')
    try:
        sqlite3.connect(':memory:').execute('CREATE VIRTUAL TABLE test USING fts5(body)').close()
    except sqlite3.OperationalError:
        parser.error('This Python installation needs SQLite with FTS5 support.')

    try:
        python = python_for_project()
    except (OSError, subprocess.CalledProcessError) as error:
        parser.error(f'Could not create the Python environment: {error}. Check that Python venv and pip are installed.')
    workspace = ROOT / 'abo_runtime'
    env = {**os.environ, 'PYTHONUTF8':'1', 'PYTHONUNBUFFERED':'1', 'DOC_VQA_WORKSPACE': str(workspace),
           'ABO_GPU':'0', 'ABO_FONT_PATH': str(workspace/'fonts/NotoSansSC.ttf')}
    marker = ROOT / '.venv-web' / '.abo-requirements-sha256'
    with REQUIREMENTS.open('rb') as stream:
        digest = file_digest(stream,'sha256').hexdigest()
    try:
        if not args.skip_install and (not marker.is_file() or marker.read_text() != digest or
                                      sys.prefix != sys.base_prefix):
            print('Installing the ABO web dependencies...', flush=True)
            subprocess.run([python,'-m','pip','install','-r',REQUIREMENTS], cwd=ROOT, env=env, check=True)
            if sys.prefix == sys.base_prefix:
                marker.write_text(digest, encoding='ascii')
        subprocess.run([python,'-m','app.abo.web_setup'], cwd=ROOT, env=env, check=True)
        if args.prepare_only:
            return 0
        print(f'ABO Web · http://127.0.0.1:{args.port}/', flush=True)
        if not args.no_browser:
            Thread(target=open_when_ready, args=(args.port,), daemon=True).start()
        return subprocess.call([python,'-m','uvicorn','app.asgi:app','--host','127.0.0.1',
                                '--port',str(args.port)], cwd=ROOT, env=env)
    except subprocess.CalledProcessError as error:
        print('ABO setup could not finish. Review the message above and run the same command again.',file=sys.stderr)
        return error.returncode
    except KeyboardInterrupt:
        return 0


if __name__ == '__main__':
    sys.exit(main())
