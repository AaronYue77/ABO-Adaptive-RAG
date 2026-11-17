from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import time
import ssl
import sys
from threading import RLock
import urllib.request

from log_privacy import redact_log

REPO = Path(__file__).resolve().parents[2]
WORKSPACE = Path(os.environ.get("DOC_VQA_WORKSPACE", REPO.parent.parent)).resolve()
CODE = Path(__file__).resolve().parent
LOG_DIR = REPO / "personal/a_baseline/docs"  # Preserve the user-agreed log locations.
ISSUES = LOG_DIR / "问题与解决记录.txt"
OPERATIONS = LOG_DIR / "操作方法与结果记录.txt"
_LOG_LOCK = RLock()


@contextmanager
def log_lock(path: Path):
    """Serialize append operations within this process and across entrypoints."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with _LOG_LOCK, path.with_name(path.name + ".lock").open("a+b") as lock:
        if os.name == "nt":
            import msvcrt

            if lock.tell() == 0:
                lock.write(b"\0")
                lock.flush()
            lock.seek(0)
            msvcrt.locking(lock.fileno(), msvcrt.LK_LOCK, 1)
            try:
                yield
            finally:
                lock.seek(0)
                msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def append_log(path: Path, text: str) -> None:
    """Append one prepared record and flush it when this call returns."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as stream:
        stream.write(redact_log(text))


def append_log_json(path: Path, row: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as stream:
        stream.write(json.dumps(redact_log(row), ensure_ascii=False) + "\n")


def configure_environment() -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    paths = {
        "HF_HOME": "cache/huggingface", "HF_HUB_CACHE": "cache/huggingface/hub",
        "HF_XET_CACHE": "cache/huggingface/xet", "TORCH_HOME": "cache/torch",
        "TORCH_EXTENSIONS_DIR": "cache/torch-extensions", "TORCHINDUCTOR_CACHE_DIR": "cache/torchinductor",
        "TRITON_CACHE_DIR": "cache/triton", "CUDA_CACHE_PATH": "cache/cuda",
        "TEMP": "cache/tmp", "TMP": "cache/tmp",
    }
    for key, value in paths.items():
        os.environ[key] = str(WORKSPACE / value)
    os.environ["HF_HUB_OFFLINE"] = "1"
    (WORKSPACE / "cache/tmp").mkdir(parents=True, exist_ok=True)


def timestamp() -> str:
    return datetime.now(timezone(timedelta(hours=8))).isoformat(timespec="seconds")


def read_jsonl(path: Path) -> list[dict]:
    # PDF text can contain U+2028/U+0085 inside JSON strings. str.splitlines()
    # treats them as line breaks even though a JSONL boundary is a physical LF.
    with path.open("r", encoding="utf-8") as stream:
        return [json.loads(line) for line in stream if line.strip()]


def write_jsonl(path: Path, rows) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as stream:
        for row in rows:
            stream.write(json.dumps(row, ensure_ascii=False) + "\n")


def write_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", "utf-8", newline="\n")


def file_hash(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def issue(symptom: str, cause: str, solution: str, verification: str = "待验证", status: str = "待解决") -> str:
    with log_lock(ISSUES):
        text = ISSUES.read_text("utf-8") if ISSUES.exists() else ""
        numbers = [int(value) for value in re.findall(r"(?m)^\[.+?\] ISSUE-(\d+)\b", text)]
        key = f"ISSUE-{max(numbers, default=0) + 1:03d}"
        append_log(ISSUES, f"\n[{timestamp()}] {key}\n现象：{symptom}\n原因：{cause}\n解决：{solution}\n验证：{verification}\n状态：{status}\n")
    return key


def update_issue(key: str, cause: str, solution: str, verification: str, status: str) -> None:
    """Append a verified follow-up to the existing issue number."""
    with log_lock(ISSUES):
        text = ISSUES.read_text("utf-8") if ISSUES.exists() else ""
        if not re.fullmatch(r"ISSUE-\d{3,}", key) or not re.search(r"(?m)^\[.+?\] " + key + r"\b", text):
            raise ValueError("找不到该问题编号。")
        append_log(ISSUES, f"\n[{timestamp()}] {key} 更新\n原因：{cause}\n解决：{solution}\n验证：{verification}\n状态：{status}\n")


def operation(action: str, method: str, result: str, outputs=(), verification: str = "见输出文件", status: str = "完成", related_issues: str = "无", event_time: str | None = None) -> str:
    """Record a project operation, including how it was done and what was verified."""
    with log_lock(OPERATIONS):
        previous = OPERATIONS.read_text("utf-8") if OPERATIONS.exists() else ""
        numbers = [int(value) for value in re.findall(r"(?m)^\[.+?\] OP-(\d+)\b", previous)]
        key = f"OP-{max(numbers, default=0) + 1:03d}"
        header = "项目操作记录：记录方法、操作、结果、验证、产物和问题关联。\n" if not previous else ""
        append_log(OPERATIONS, header + f"\n[{timestamp()}] {key}\n操作：{action}\n执行时间：{event_time or '本次记录时刻'}\n方法：{method}\n结果：{result}\n输出：{'；'.join(str(p) for p in outputs) or '见本条说明'}\n验证：{verification}\n关联问题：{related_issues}\n状态：{status}\n")
    return key


def fetch(url: str, retries: int = 5) -> bytes:
    for attempt in range(retries):
        try:
            request = urllib.request.Request(url, headers={"User-Agent": "personal-visrag-a/1.0"})
            context = ssl.create_default_context()
            if attempt >= 2:
                context.set_ecdh_curve("prime256v1")
            if attempt % 2:
                opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), urllib.request.HTTPSHandler(context=context))
            else:
                opener = urllib.request.build_opener(urllib.request.HTTPSHandler(context=context))
            with opener.open(request, timeout=30) as response:
                return response.read()
        except Exception:
            if attempt == retries - 1:
                raise
            time.sleep(min(2 ** attempt, 8))
    raise AssertionError("unreachable")


configure_environment()
