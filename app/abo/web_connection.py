"""Connect the browser's local server to an already initialized Kaggle notebook."""

from threading import RLock

from requests import RequestException
from websocket import WebSocketException

from app.abo.service import bind_runtime

bind_runtime()
from app.abo.journal import SessionJournal
from kaggle_chat_client import ChatConnectionError, connect, kaggle_address


TRANSPORT_ERRORS = (WebSocketException, RequestException, TypeError, KeyError, ValueError, OSError)
CONNECTION_ERROR = '连接中断。请确认 Kaggle 会话还在运行，并复制新的连接地址。'


class RemoteDialogue:
    def __init__(self, output, address):
        self.output = output
        self.lock = RLock()
        self.journal = SessionJournal(output, '网页连接 Kaggle')
        self.kernel = None
        try:
            addr = kaggle_address(address)
            if addr.scheme != 'https':
                raise ChatConnectionError('请使用 Kaggle 提供的 HTTPS 连接地址。')
            self.kernel = connect(addr)
            config = self.kernel.execute("__import__('json').loads((chat.output/'config.json').read_text('utf-8'))",20)
            self.journal.remote_config(config)
            self.journal.ready('Kaggle GPU')
        except (ChatConnectionError, *TRANSPORT_ERRORS) as error:
            safe = error if isinstance(error, ChatConnectionError) else ChatConnectionError(CONNECTION_ERROR)
            self.journal.failed('远程连接',safe)
            self.close()
            raise safe from None

    def respond(self, question):
        with self.lock:
            try:
                row = self.kernel.execute(f'chat.respond({question!r})',300)
                self.journal.mirror(row)
                return row
            except (ChatConnectionError, *TRANSPORT_ERRORS) as error:
                safe = error if isinstance(error, ChatConnectionError) else ChatConnectionError(CONNECTION_ERROR)
                self.journal.failed('远程问答',safe,question)
                self.close()
                raise safe from None

    def close(self):
        with self.lock:
            try:
                if self.kernel is not None:
                    self.kernel.close()
            finally:
                self.kernel = None
                self.journal.close()
