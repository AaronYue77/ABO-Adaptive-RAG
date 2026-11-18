"""Connect the browser's local server to an already initialized Kaggle notebook."""

from threading import RLock
from uuid import uuid4

from requests import RequestException
from websocket import WebSocketException

from app.abo.service import bind_runtime

bind_runtime()
from kaggle_chat_client import ChatConnectionError, connect, kaggle_address  # noqa: E402

from app.abo.journal import SessionJournal  # noqa: E402 -- the runtime must be bound first.

TRANSPORT_ERRORS = (WebSocketException, RequestException, TypeError, KeyError, ValueError, OSError)
CONNECTION_ERROR = "连接中断。请确认 Kaggle 会话还在运行，并复制新的连接地址。"


class RemoteDialogue:
    def __init__(self, output, address):
        self.output = output
        self.lock = RLock()
        self.journal = SessionJournal(output, "网页连接 Kaggle")
        self.kernel = None
        self.state = None
        self.address = None
        self.closed = False
        try:
            addr = kaggle_address(address)
            if addr.scheme != "https":
                raise ChatConnectionError("请使用 Kaggle 提供的 HTTPS 连接地址。")
            self.address = addr
            self.kernel = connect(addr)
            config = self.kernel.execute(
                "__import__('json').loads((chat.output/'config.json').read_text('utf-8'))", 20
            )
            if not config.get("dialogue_planner"):
                raise ChatConnectionError(
                    "当前 Kaggle 会话仍是旧版。请在新版 ABO Notebook 中重新启动会话并运行初始化，再连接；本机历史对话会保留。"
                )
            self.journal.remote_config(config)
            self.journal.ready("Kaggle GPU")
            # Jupyter proxies can expire an idle websocket. Open a fresh channel per request.
            self.kernel.close()
            self.kernel = None
        except (ChatConnectionError, *TRANSPORT_ERRORS) as error:
            safe = (
                error
                if isinstance(error, ChatConnectionError)
                else ChatConnectionError(CONNECTION_ERROR)
            )
            self.journal.failed("远程连接", safe)
            self.close()
            raise safe from None

    def respond(self, question, *, thread_id=None, request_id=None, state=None):
        with self.lock:
            payload = {
                "thread_id": thread_id or uuid4().hex,
                "request_id": request_id or uuid4().hex,
                "question": question,
                "state": state,
            }
            for attempt in range(2):
                try:
                    self.kernel = connect(self.address)
                    expression = (
                        "__import__('app.abo.cloud',fromlist=['cloud_request']).cloud_request(chat,"
                        + repr(payload)
                        + ")"
                    )
                    reply = self.kernel.execute(expression, 420)
                    heartbeats = getattr(self.kernel, "heartbeats", 0)
                    if isinstance(heartbeats, int) and heartbeats:
                        self.journal.event(
                            "keepalive",
                            "正常",
                            f"本轮发送{heartbeats}次通道心跳，推理期间保持连接。",
                        )
                    self.state = reply["state"]
                    row = reply["row"]
                    self.journal.mirror(row)
                    if attempt:
                        self.journal.event(
                            "reconnect", "恢复", "使用相同请求编号恢复结果，未重复执行问答。"
                        )
                    return row
                except (ChatConnectionError, *TRANSPORT_ERRORS) as error:
                    safe = (
                        error
                        if isinstance(error, ChatConnectionError)
                        else ChatConnectionError(CONNECTION_ERROR)
                    )
                    self.journal.event(
                        "transport_retry",
                        "重试" if not attempt else "未恢复",
                        f"第{attempt + 1}次通道请求：{safe}",
                    )
                    if attempt:
                        self.journal.failed("远程问答", safe, question)
                        # Keep the address, thread and state. A temporary network fault is resumable.
                        raise safe from None
                finally:
                    if self.kernel is not None:
                        try:
                            self.kernel.close()
                        except (WebSocketException, OSError):
                            pass
                        self.kernel = None

    def close(self):
        with self.lock:
            try:
                if self.kernel is not None:
                    self.kernel.close()
            finally:
                self.kernel = None
                self.closed = True
                self.journal.close()
