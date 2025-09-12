import os
import threading
from typing import List, Optional

from fastapi import FastAPI, HTTPException, Depends, Header
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

# Reuse the existing RAG implementation
import rag as rag_impl
from langchain.chains import RetrievalQA


app = FastAPI(
    title="RAG Files API",
    description="Simple per-file RAG API powered by LangChain + Chroma.",
    version="1.0.0",
    openapi_version="3.1.0",
)


class QueryRequest(BaseModel):
    file: str = Field(..., description="Filename under docs/ to query")
    question: str = Field(..., description="User question")
    top_k: Optional[int] = Field(3, ge=1, le=10, description="Retriever top-k")


class QueryResponse(BaseModel):
    answer: str
    sources: List[str]


class ReloadRequest(BaseModel):
    file: str


lock = threading.Lock()


def ensure_auth(authorization: Optional[str] = Header(None)):
    token = os.getenv("AUTH_TOKEN")
    if not token:
        return
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing Bearer token")
    provided = authorization.split(" ", 1)[1]
    if provided != token:
        raise HTTPException(status_code=403, detail="Invalid token")


@app.get("/health")
def health():
    # Ensure API key exists to avoid confusing startup states
    has_key = bool(os.getenv("OPENAI_API_KEY"))
    return {"status": "ok", "openai_key": has_key}


@app.get("/files", response_model=List[str])
def list_files(_: None = Depends(ensure_auth)):
    return rag_impl._supported_files()


@app.post("/query", response_model=QueryResponse)
def query(req: QueryRequest, _: None = Depends(ensure_auth)):
    files = rag_impl._supported_files()
    if req.file not in files:
        raise HTTPException(status_code=404, detail=f"File not found or unsupported: {req.file}")

    with lock:
        # Build or load the per-file vector DB
        vs = rag_impl.build_or_load_db_for_file(req.file)

    retriever = vs.as_retriever(search_kwargs={"k": req.top_k or 3})

    qa = RetrievalQA.from_chain_type(
        llm=rag_impl._make_chat_llm(max_tokens=512),
        retriever=retriever,
        return_source_documents=True,
    )

    try:
        result = qa.invoke({"query": req.question})
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    answer: str = result.get("result", "")
    # Collect unique source hints
    srcs: List[str] = []
    for d in result.get("source_documents", []) or []:
        src = str((d.metadata or {}).get("source") or "")
        if src and src not in srcs:
            srcs.append(src)

    return QueryResponse(answer=answer, sources=srcs)


@app.post("/reload")
def reload_file(req: ReloadRequest, _: None = Depends(ensure_auth)):
    files = rag_impl._supported_files()
    if req.file not in files:
        raise HTTPException(status_code=404, detail=f"File not found or unsupported: {req.file}")
    with lock:
        rag_impl.build_or_load_db_for_file(req.file, force=True)
    return {"status": "reloaded", "file": req.file}


# Convenience root
@app.get("/")
def root():
    return {
        "name": "RAG Files API",
        "docs_url": "/docs",
        "openapi_url": "/openapi.json",
        "files": "/files",
        "query": {"POST": "/query"},
    }


@app.get("/privacy", response_class=HTMLResponse)
def privacy():
    app_name = os.getenv("APP_NAME", "RAG Files API")
    contact = os.getenv("CONTACT_EMAIL", "")
    retention = os.getenv("DATA_RETENTION", "暫存索引（/tmp/db）會在 dyno 重啟時清除；Heroku 日誌依其保留政策保存。")
    html = f"""
    <html>
      <head>
        <meta charset='utf-8'/>
        <title>Privacy Policy - {app_name}</title>
        <style>body{{font-family:system-ui,-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;max-width:820px;margin:2rem auto;padding:0 1rem;line-height:1.6}}</style>
      </head>
      <body>
        <h1>Privacy Policy</h1>
        <p>Effective date: {os.getenv('PRIVACY_EFFECTIVE_DATE', '2025-01-01')}</p>
        <h2>What we collect</h2>
        <ul>
          <li>Your API requests to this service (endpoints, parameters such as file name and question).</li>
          <li>Server logs (timestamps, IP as provided by platform, and error traces when applicable).</li>
          <li>Document embeddings stored locally for retrieval (Chroma DB under DB_DIR).</li>
        </ul>
        <h2>How we use data</h2>
        <ul>
          <li>Process your questions against selected documents and return answers.</li>
          <li>Maintain temporary vector indexes to speed up retrieval.</li>
          <li>Debug and operate the service (via server logs).</li>
        </ul>
        <h2>Storage and retention</h2>
        <p>{retention}</p>
        <p>在 Heroku 上，檔案系統為暫存性質，索引會定期清空；若需要持久化，請改用外部向量資料庫。</p>
        <h2>Third parties</h2>
        <p>本服務會呼叫 OpenAI API 以產生回應。請同時參考 OpenAI 的隱私權政策。</p>
        <h2>Security</h2>
        <p>僅限內部用途與除錯之必要存取，並使用 Bearer token（若已設定 AUTH_TOKEN）。</p>
        <h2>Your choices</h2>
        <ul>
          <li>若不希望資料被處理，請停止使用本服務。</li>
          <li>如需移除本機索引，請使用 /reload 重建或等待 dyno 重啟。</li>
        </ul>
        <h2>Contact</h2>
        <p>如有疑問，請聯絡：{contact or '請設定 CONTACT_EMAIL 環境變數'}</p>
      </body>
    </html>
    """
    return HTMLResponse(content=html)


@app.get("/terms", response_class=HTMLResponse)
def terms():
    app_name = os.getenv("APP_NAME", "RAG Files API")
    contact = os.getenv("CONTACT_EMAIL", "")
    html = f"""
    <html>
      <head>
        <meta charset='utf-8'/>
        <title>Terms of Service - {app_name}</title>
        <style>body{{font-family:system-ui,-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;max-width:820px;margin:2rem auto;padding:0 1rem;line-height:1.6}}</style>
      </head>
      <body>
        <h1>Terms of Service</h1>
        <p>By using {app_name}, you agree to:</p>
        <ul>
          <li>Provide lawful content and comply with applicable laws.</li>
          <li>Accept that the service is provided “as is” without warranties.</li>
          <li>We may change or discontinue the service at any time.</li>
          <li>Liability is limited to the maximum extent permitted by law.</li>
        </ul>
        <p>Contact: {contact or '請設定 CONTACT_EMAIL 環境變數'}</p>
      </body>
    </html>
    """
    return HTMLResponse(content=html)
