"""Use the initialized Stage D chat through the notebook's Jupyter connection."""

import argparse
from datetime import datetime, timezone
import json
import re
import sys
import time
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

import requests
from websocket import WebSocketException, WebSocketTimeoutException, create_connection


class ChatConnectionError(Exception):
    """An error message suitable for displaying without connection secrets."""


def kaggle_address(address):
    parts = urlsplit(address.strip())
    host = parts.hostname or ""
    kaggle_domains = ("kaggle.com", "kaggle.net", "kaggleusercontent.com")
    if (parts.scheme not in ("https", "http") or parts.username or parts.password or
        not any(host == domain or host.endswith("." + domain) for domain in kaggle_domains)):
        raise ChatConnectionError("请复制 Kaggle Jupyter Server 面板中的 VS Code Compatible URL。")
    path = parts.path.rstrip("/")
    for suffix in ("/tree", "/lab"):
        if path.endswith(suffix):
            path = path.removesuffix(suffix)
    return parts._replace(path=path, fragment="")


def kernel_list(address):
    url = urlunsplit(address._replace(path=address.path + "/api/kernels"))
    with requests.get(url, timeout=20, allow_redirects=False) as response:
        if response.status_code != 200:
            raise ChatConnectionError(
                f"Kaggle 会话连接失败（HTTP {response.status_code}）。请确认 GPU 会话还在运行，再复制新的连接地址。")
        try:
            kernels = response.json()
        except ValueError:
            raise ChatConnectionError("这个地址没有返回 Jupyter 会话。请重新复制 VS Code Compatible URL。") from None
    if not isinstance(kernels, list):
        raise ChatConnectionError("没有找到正在运行的 Jupyter 内核。请先在 Kaggle 运行初始化单元格。")
    return sorted(kernels, key=lambda kernel: kernel.get("last_activity") or "", reverse=True)


class RemoteKernel:
    def __init__(self, address, kernel_id):
        if not re.fullmatch(r"[-a-zA-Z0-9]+", kernel_id):
            raise ChatConnectionError("Kaggle 返回了无效的 Jupyter 内核标识。")
        self.session = uuid4().hex
        query = "&".join(filter(None, (address.query, "session_id=" + self.session)))
        url = urlunsplit(address._replace(
            scheme="wss" if address.scheme == "https" else "ws",
            path=f"{address.path}/api/kernels/{kernel_id}/channels", query=query,
        ))
        self.socket = create_connection(url, timeout=20, redirect_limit=0)

    def close(self):
        self.socket.close()

    def execute(self, expression, timeout):
        identity = uuid4().hex
        marker = f"__ABO_REPLY_{identity}__"
        code = f"print({marker!r} + __import__('json').dumps({expression}))"
        self.socket.send(json.dumps({
            "channel": "shell",
            "header": {
                "msg_id": identity, "msg_type": "execute_request", "username": "powershell",
                "session": self.session, "date": datetime.now(timezone.utc).isoformat(), "version": "5.3",
            },
            "parent_header": {}, "metadata": {}, "buffers": [],
            "content": {"code": code, "silent": False, "store_history": False,
                        "user_expressions": {}, "allow_stdin": False, "stop_on_error": False},
        }))
        reply, idle, chunks = None, False, []
        deadline = time.monotonic() + timeout
        try:
            while reply is None or not idle:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise WebSocketTimeoutException()
                self.socket.settimeout(remaining)
                frame = self.socket.recv()
                if frame == "":
                    raise ChatConnectionError("Kaggle 会话已断开。请确认 Notebook 会话仍在运行。")
                if isinstance(frame, bytes):
                    continue
                message = json.loads(frame)
                if (message.get("parent_header") or {}).get("msg_id") != identity:
                    continue
                kind = message.get("header", {}).get("msg_type")
                content = message.get("content", {})
                if kind == "stream" and content.get("name") == "stdout":
                    chunks.append(content["text"])
                elif kind == "execute_reply":
                    reply = content
                elif kind == "status" and content.get("execution_state") == "idle":
                    idle = True
        except WebSocketTimeoutException:
            raise ChatConnectionError(
                "Kaggle 等待超时。请查看 Notebook 里的运行状态后再连接，当前请求可能仍在处理。") from None
        if reply.get("status") != "ok":
            reason = re.sub(r"[^a-zA-Z0-9_]", "", reply.get("ename", ""))[:40]
            raise ChatConnectionError(
                f"Kaggle 执行失败（{reason or '执行错误'}）。请检查 Notebook 中能否正常调用 ask()。")
        for line in "".join(chunks).splitlines():
            if line.startswith(marker):
                return json.loads(line.removeprefix(marker))
        raise ChatConnectionError("远端未返回商品助手的结果。请确认 Notebook 已初始化。")


READY = (
    "type(globals().get('chat')).__module__ == 'app.abo.service' "
    "and callable(globals().get('ask')) and chat.app.model_available"
)


def connect(address):
    for item in kernel_list(address):
        kernel = RemoteKernel(address, item["id"])
        try:
            ready = kernel.execute(READY, timeout=30)
        except BaseException:
            kernel.close()
            raise
        if ready:
            return kernel
        kernel.close()
    raise ChatConnectionError("请在 Kaggle 选择 GPU 并运行第一个代码单元格，等商品助手就绪后再连接。")


def ask(kernel, question):
    result = kernel.execute(f"chat.respond({question!r})", timeout=300)
    print("助手：" + (result.get("display_answer") or result["answer"]))
    for label in dict.fromkeys(source["label"] for source in result.get("display_sources",[])):
        print("  来源 "+label)
    return result


def main(argv=None, *, output=None):
    parser = argparse.ArgumentParser(description="Connect PowerShell to an initialized Kaggle GPU chat.")
    parser.add_argument("--question")
    args = parser.parse_args(argv)
    journal = None
    if output is not None:
        from app.abo.journal import SessionJournal

        journal = SessionJournal(output, "PowerShell 连接 Kaggle")
    question = None
    try:
        print("请在 Kaggle 打开 Run → Kaggle Jupyter Server，复制 VS Code Compatible URL。")
        address = kaggle_address(input("粘贴连接地址后按 Enter："))
        print("已收到连接地址，正在连接 Kaggle…", flush=True)
        kernel = connect(address)
        try:
            if journal:
                config = kernel.execute("__import__('json').loads((chat.output/'config.json').read_text('utf-8'))", timeout=20)
                journal.remote_config(config)
                journal.ready("远程 Kaggle GPU")
            print("已连接 Kaggle GPU 商品助手。输入「退出」结束本地连接。")
            if args.question:
                question = args.question
                row = ask(kernel, question)
                if journal:
                    journal.mirror(row)
            else:
                while True:
                    question = input("你：").strip()
                    if question.lower() in {"退出", "exit", "quit"}:
                        break
                    if 0 < len(question) <= 1600:
                        row = ask(kernel, question)
                        if journal:
                            journal.mirror(row)
                        question = None
                    elif question:
                        print("请输入 1 到 1600 个字符。")
        finally:
            kernel.close()
    except (EOFError, KeyboardInterrupt):
        return 0
    except ChatConnectionError as error:
        if journal:
            journal.failed("远程问答" if question else "连接", error, question)
        print(str(error), file=sys.stderr)
        return 1
    except (requests.RequestException, WebSocketException, OSError, ValueError, KeyError, TypeError) as error:
        message = "连接已中断或地址无效。请确认 Kaggle 会话正在运行，再复制连接地址重试。"
        if journal:
            journal.failed("远程问答" if question else "连接", ChatConnectionError(f"{type(error).__name__} · {message}"), question)
        print(message, file=sys.stderr)
        return 1
    finally:
        if journal:
            journal.close()
            if (output / "对话记录.txt").is_file():
                print("本地对话记录 "+str(output / "对话记录.txt"))
            print("本地运行记录 "+str(output / "运行记录.txt"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
