"""FastAPI entry for the ABO profile, with per-session catalogue state."""

from contextlib import asynccontextmanager
import json
import os
from pathlib import Path
from threading import RLock
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, SecretStr

from app.abo.service import AboDialogue, bind_runtime
from app.abo.web_catalog import WebCatalog
from app.abo.web_connection import ChatConnectionError, RemoteDialogue
from app.abo.web_setup import catalog_ready


WEB = Path(__file__).with_name('web')


class ChatRequest(BaseModel):
    question: str = Field(min_length=1, max_length=1600)
    session_id: str | None = Field(default=None, min_length=32, max_length=32)


class RemoteRequest(BaseModel):
    address: SecretStr


def web_sources(row):
    pages = {page['page_id']:page for page in row.get('retrieved_pages',[]) if page.get('page_id')}
    claims = {}
    for claim in row.get('claims',[]):
        values = claim.get('values') or [claim.get('value')]
        claims.setdefault(claim.get('citation'),[]).extend(str(value) for value in values if value is not None)
    sources = []
    for source in row.get('display_sources',[]):
        citation = source['citation']
        page = pages.get(citation,{})
        origin = page.get('source')
        origin = origin if isinstance(origin,dict) else {}
        sources.append({**source,
                        'excerpt':' / '.join(claims.get(citation,[]))[:650] or str(page.get('text') or '')[:650],
                        'url':page.get('source_url'), 'field':page.get('field') or page.get('section'),
                        'member':origin.get('member'), 'line':origin.get('line')})
    return sources


def create_abo_app():
    sessions = {}
    lock = RLock()
    workspace = bind_runtime()
    catalog = workspace/'data/ecommerce_rag/abo-full-v1'
    browser_catalog = WebCatalog(catalog)

    def require_catalog():
        if not catalog_ready(catalog):
            raise HTTPException(503, '请运行项目根目录的 start-web.cmd 初始化本地商品目录。')

    @asynccontextmanager
    async def lifespan(_app):
        try:
            yield
        finally:
            for session in sessions.values():
                session.close()

    app = FastAPI(title="ABO Adaptive RAG", lifespan=lifespan)
    if WEB.is_dir():
        app.mount('/web-assets', StaticFiles(directory=WEB), name='abo-web')

        @app.get('/', include_in_schema=False)
        def index():
            return FileResponse(WEB/'index.html', headers={'Cache-Control':'no-cache'})

    @app.get("/api/v1/health")
    def health():
        ready = catalog_ready(catalog)
        stats = {}
        if ready:
            manifest = json.loads((catalog/'manifest.json').read_text('utf-8'))
            stats = {'records':manifest['records'],'categories':len(manifest['product_types']),
                     'marketplaces':len(manifest['domains'])}
        return {"status":"ready" if ready else 'setup_required', "profile":"abo",
                "gpu":os.environ.get("ABO_GPU")=="1", "catalog":stats}

    @app.get('/api/v1/catalog/categories')
    def categories():
        require_catalog()
        return {'items':browser_catalog.categories()}

    @app.get('/api/v1/catalog/categories/{category}/products')
    def category_products(category: str, after: int = Query(0, ge=0, le=147702),
                          limit: int = Query(24, ge=1, le=60)):
        require_catalog()
        info = next((item for item in browser_catalog.categories() if item['key']==category),None)
        if info is None:
            raise HTTPException(404, '没有找到这个类目。')
        return {'category':info, **browser_catalog.products(category,after,limit)}

    @app.get('/api/v1/catalog/products/{product_id}')
    def product(product_id: int):
        require_catalog()
        detail = browser_catalog.details(product_id)
        if detail is None:
            raise HTTPException(404, '没有找到这条商品资料。')
        return detail

    @app.post('/api/v1/remote')
    def remote(body: RemoteRequest):
        address = body.address.get_secret_value()
        if not 1 <= len(address) <= 4096:
            raise HTTPException(422,'请粘贴 Kaggle 的连接地址。')
        with lock:
            if len(sessions) >= 32:
                raise HTTPException(503,'会话数量达到上限。')
            key = uuid4().hex
            output = workspace/'reports/ecommerce_rag'/('abo-adaptive-web-kaggle-'+key)
            output.mkdir(parents=True)
            try:
                sessions[key] = RemoteDialogue(output,address)
            except ChatConnectionError as error:
                raise HTTPException(502,str(error)) from None
        return {'session_id':key,'mode':'kaggle'}

    @app.delete('/api/v1/chat/{session_id}', status_code=204, response_class=Response)
    def close_session(session_id: str):
        with lock:
            session = sessions.pop(session_id,None)
            if session is not None:
                session.close()

    @app.post("/api/v1/chat")
    def chat(body: ChatRequest):
        if not body.question.strip():
            raise HTTPException(422, "请输入商品问题。")
        with lock:
            key = body.session_id or uuid4().hex
            if key not in sessions:
                if body.session_id:
                    raise HTTPException(404, "会话已失效，请新建会话。")
                require_catalog()
                if len(sessions) >= 32:
                    raise HTTPException(503, "会话数量达到上限。")
                output = workspace / "reports/ecommerce_rag" / ("abo-adaptive-api-"+key)
                output.mkdir(parents=True)
                sessions[key] = AboDialogue(output,gpu=os.environ.get("ABO_GPU")=="1",entry="HTTP")
            session = sessions[key]
            try:
                row = session.respond(body.question)
            except ChatConnectionError as error:
                sessions.pop(key,None)
                raise HTTPException(502,str(error)) from None
            if row["decision"]=="exit":
                sessions.pop(key).close()
        products = browser_catalog.chat_cards(row) if catalog_ready(catalog) else []
        return {"session_id":key,"answer":row["display_answer"],"action":row["decision"],"products":products,
                "sources":web_sources(row),"citations":row["citations"],"strategy":row["adaptive"]["strategy"],
                "run_name":session.output.name,"question_id":row["question_id"],"issue_id":row.get("issue_id")}

    return app
