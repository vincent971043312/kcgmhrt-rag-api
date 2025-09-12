import os
import threading
from typing import List, Optional

from fastapi import FastAPI, HTTPException, Depends, Header
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
