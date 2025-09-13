import os
import threading
import re
from typing import List, Optional

from fastapi import FastAPI, HTTPException, Depends, Header
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

# Reuse the existing RAG implementation
import rag as rag_impl
from langchain.chains import RetrievalQA
try:
    from langchain.retrievers.multi_query import MultiQueryRetriever
    from langchain.prompts import PromptTemplate
except Exception:  # pragma: no cover
    MultiQueryRetriever = None  # type: ignore
    PromptTemplate = None  # type: ignore


app = FastAPI(
    title="RAG Files API",
    description="Simple per-file RAG API powered by LangChain + Chroma.",
    version="1.0.0",
    openapi_version="3.1.0",
)

# Enable CORS for your website front-end (set CORS_ORIGINS env)
_origins_env = os.getenv("CORS_ORIGINS", "").strip()
_allow_all = os.getenv("ALLOW_ALL_CORS", "").lower() in {"1", "true", "yes", "*"}
if _allow_all:
    # Wildcard origins cannot be combined with allow_credentials=True per CORS spec
    _allow_origins = ["*"]
    _allow_credentials = False
else:
    _allow_origins = [o.strip() for o in _origins_env.split(",") if o.strip()]
    _allow_credentials = True

app.add_middleware(
    CORSMiddleware,
    allow_origins=_allow_origins,
    allow_credentials=_allow_credentials,
    allow_methods=["*"],
    allow_headers=["*"],
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


# -------- Query augmentation helpers (improve recall for short/ambiguous queries) --------
def _pw_synonyms() -> list[str]:
    env = os.getenv("PW_SYNONYMS")
    if env:
        parts = [p.strip() for p in env.split("|") if p.strip()]
    else:
        parts = [
            "配置密碼", "預設密碼", "初始密碼", "系統密碼", "工程師密碼", "維護密碼", "管理者密碼",
            "Biomed code", "service code", "access code",
            "password", "default password", "configuration password", "service password",
        ]
    # dedupe
    seen = set()
    return [w for w in parts if not (w in seen or seen.add(w))]


def _alpha_digit_variants(token: str) -> list[str]:
    # Generate variants like C1 -> C 1 / C-1; V600 -> V 600 / V-600
    m = re.fullmatch(r"([A-Za-z]+)(\d+)", token)
    if not m:
        return []
    a, d = m.group(1), m.group(2)
    base = [f"{a}{d}", f"{a.upper()}{d}", f"{a.capitalize()}{d}"]
    return list({*base, f"{a} {d}", f"{a}-{d}", f"{a.upper()} {d}", f"{a.upper()}-{d}"})


def _brand_hints(question: str) -> list[str]:
    ql = question.lower()
    hints: list[str] = []
    # Common ventilator/model hints
    if "c1" in ql and "hamilton" not in ql:
        hints += ["Hamilton C1", "哈密頓 C1"]
    if "v600" in ql and "drager" not in ql and "dräger" not in ql:
        hints += ["Drager V600", "Dräger V600"]
    if "v300" in ql and "evita" not in ql:
        hints += ["Evita V300", "Dräger Evita V300"]
    return hints


def augment_question(q: str) -> str:
    expansions: list[str] = []
    ql = q.lower()
    if ("密碼" in q) or ("password" in ql) or ("access code" in ql) or ("service code" in ql) or ("biomed" in ql):
        expansions += _pw_synonyms()
    # Alpha-digit tokens variants
    for tok in re.findall(r"[A-Za-z]+\d+", q):
        expansions += _alpha_digit_variants(tok)
    expansions += _brand_hints(q)
    # Deduplicate while preserving order
    seen = set()
    uniq = [w for w in expansions if not (w in seen or seen.add(w))]
    return q if not uniq else f"{q} \n{ ' '.join(uniq) }"


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

    # Use MMR for diversity and fetch a larger pool for better recall
    k = req.top_k or 3
    retriever = vs.as_retriever(
        search_type="mmr",
        search_kwargs={"k": k, "fetch_k": max(20, k * 5)},
    )

    # Multi-query expansion retriever (if available)
    if MultiQueryRetriever and PromptTemplate:
        try:
            mqr_prompt = PromptTemplate(
                input_variables=["question"],
                template=(
                    "你是一位檢索助理，請根據使用者問題產生 4 個等義或相近的檢索查詢（中英混合皆可），"
                    "特別擴充『密碼/配置密碼/初始密碼/工程模式/維護模式/Biomed code/Service code/Access code』，"
                    "並加入型號變體（如 C1/C 1/C-1、V600/V 600/V-600，附上品牌關鍵字）。請每行一個短查詢，不要解釋。\n\n問題：{question}"
                ),
            )
            retriever = MultiQueryRetriever.from_llm(
                llm=rag_impl._make_chat_llm(max_tokens=256),
                retriever=retriever,
                prompt=mqr_prompt,
            )
        except Exception:
            pass

    qa = RetrievalQA.from_chain_type(
        llm=rag_impl._make_chat_llm(max_tokens=512),
        retriever=retriever,
        return_source_documents=True,
    )

    try:
        q1 = augment_question(req.question)
        result = qa.invoke({"query": q1})
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    answer: str = result.get("result", "")
    # Collect unique source hints
    srcs: List[str] = []
    for d in result.get("source_documents", []) or []:
        src = str((d.metadata or {}).get("source") or "")
        if src and src not in srcs:
            srcs.append(src)

    # Fallback: if no sources or empty answer, retry with bigger k
    if (not answer or not answer.strip()) or (not srcs):
        try:
            retriever2 = vs.as_retriever(search_type="mmr", search_kwargs={"k": max(8, k), "fetch_k": 40})
            qa2 = RetrievalQA.from_chain_type(
                llm=rag_impl._make_chat_llm(max_tokens=512),
                retriever=retriever2,
                return_source_documents=True,
            )
            result2 = qa2.invoke({"query": q1})
            answer2: str = result2.get("result", "")
            srcs2: list[str] = []
            for d in result2.get("source_documents", []) or []:
                src = str((d.metadata or {}).get("source") or "")
                if src and src not in srcs2:
                    srcs2.append(src)
            if answer2 and srcs2:
                return QueryResponse(answer=answer2, sources=srcs2)
        except Exception:
            pass

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
