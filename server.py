import os
import re
import json
import secrets
import hashlib
import logging
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from logging.handlers import RotatingFileHandler
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException, Depends, Header, Request, Response
from fastapi.middleware.cors import CORSMiddleware
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

# --- CORS (for your Vercel frontend) ---
_origins_env = os.getenv("CORS_ORIGINS", "").strip()
_allow_all = os.getenv("ALLOW_ALL_CORS", "").lower() in {"1", "true", "yes", "*"}
if _allow_all:
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
    include_snippets: Optional[bool] = Field(False, description="Return highlighted snippets")


class SourceSnippet(BaseModel):
    source: str
    snippet: str


class QueryResponse(BaseModel):
    answer: str
    sources: List[str]
    snippets: Optional[List[SourceSnippet]] = None


class ReloadRequest(BaseModel):
    file: str


lock = threading.Lock()

# ---------- Audit logger ----------
AUDIT_LOG_PATH = os.getenv("AUDIT_LOG_PATH", os.path.join(os.getcwd(), "logs", "audit.log"))
AUDIT_MAX_BYTES = int(os.getenv("AUDIT_LOG_MAX_BYTES", "1048576"))
AUDIT_BACKUP_COUNT = int(os.getenv("AUDIT_LOG_BACKUP_COUNT", "10"))

_audit_dir = os.path.dirname(AUDIT_LOG_PATH)
if _audit_dir:
    os.makedirs(_audit_dir, exist_ok=True)

audit_logger = logging.getLogger("rag_audit")
if not audit_logger.handlers:
    audit_logger.setLevel(logging.INFO)
    try:
        _audit_handler = RotatingFileHandler(
            AUDIT_LOG_PATH, maxBytes=AUDIT_MAX_BYTES, backupCount=AUDIT_BACKUP_COUNT, encoding="utf-8"
        )
    except Exception:  # pragma: no cover
        _audit_handler = logging.StreamHandler()
    _audit_handler.setFormatter(logging.Formatter("%(message)s"))
    audit_logger.addHandler(_audit_handler)
    audit_logger.propagate = False


def _clip(text: Optional[str], n: int = 500) -> str:
    if not text:
        return ""
    return text if len(text) <= n else text[:n] + "…"


def log_audit(action: str, *, request: Optional[Request], success: bool, username: Optional[str] = None, details: Optional[Dict[str, Any]] = None) -> None:
    payload: Dict[str, Any] = {
        "ts": datetime.utcnow().isoformat(timespec="seconds") + "Z",
        "action": action,
        "success": success,
        "user": username or "-",
    }
    if request and request.client:
        payload["ip"] = request.client.host
    if request:
        ua = request.headers.get("user-agent")
        if ua:
            payload["ua"] = ua
    if details:
        payload["details"] = details
    audit_logger.info(json.dumps(payload, ensure_ascii=False))


# ---------- Auth (users + sessions) ----------
PBKDF2_ITER = int(os.getenv("AUTH_PBKDF2_ITERATIONS", "480000"))
SESSION_TTL_MINUTES = int(os.getenv("SESSION_TTL_MINUTES", "480"))
SESSION_IDLE_MINUTES = int(os.getenv("SESSION_IDLE_TIMEOUT_MINUTES", str(SESSION_TTL_MINUTES)))
SESSION_REMEMBER_MINUTES = int(os.getenv("SESSION_REMEMBER_MINUTES", "43200"))
SESSION_COOKIE_NAME = os.getenv("SESSION_COOKIE_NAME", "rag_session")
SESSION_COOKIE_SECURE = os.getenv("SESSION_COOKIE_SECURE", "true").lower() in {"1", "true", "yes"}
SESSION_COOKIE_SAMESITE = os.getenv("SESSION_COOKIE_SAMESITE", "none").lower()
SERVICE_BEARER_TOKEN = os.getenv("AUTH_TOKEN")


def _hash_password(password: str, *, iterations: Optional[int] = None, salt: Optional[str] = None) -> str:
    iterations = iterations or PBKDF2_ITER
    salt = salt or secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), iterations)
    return f"pbkdf2_sha256${iterations}${salt}${dk.hex()}"


def _verify_password(password: str, stored: str) -> bool:
    if stored.startswith("pbkdf2_sha256$"):
        try:
            _, it_s, salt, digest = stored.split("$", 3)
            it = int(it_s)
            check = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), it).hex()
            return secrets.compare_digest(check, digest)
        except Exception:
            return False
    if stored.startswith("sha256$"):
        _, digest = stored.split("$", 1)
        comp = hashlib.sha256(password.encode()).hexdigest()
        return secrets.compare_digest(comp, digest)
    # plaintext fallback
    return secrets.compare_digest(stored, password)


class AuthStore:
    def __init__(self) -> None:
        self._users: Dict[str, str] = {}
        self._load()

    def _normalize(self, raw: str) -> List[str]:
        lines: List[str] = []
        for line in raw.replace(",", "\n").splitlines():
            s = line.strip()
            if s and not s.startswith("#"):
                lines.append(s)
        return lines

    def _load(self) -> None:
        env_raw = os.getenv("AUTH_USERS", "")
        entries = self._normalize(env_raw)
        fp = os.getenv("AUTH_USERS_FILE")
        if fp and os.path.exists(fp):
            with open(fp, "r", encoding="utf-8") as fh:
                entries += self._normalize(fh.read())
        for ent in entries:
            if ":" in ent:
                u, p = ent.split(":", 1)
            elif "=" in ent:
                u, p = ent.split("=", 1)
            else:
                continue
            self._users[u.strip()] = p.strip()

    def verify(self, username: str, password: str) -> bool:
        stored = self._users.get(username)
        if not stored:
            return False
        return _verify_password(password, stored)

    @property
    def users(self) -> Dict[str, str]:
        return self._users


auth_store = AuthStore()


@dataclass
class SessionData:
    username: str
    session_id: str
    issued_at: datetime
    expires_at: datetime
    ip: Optional[str]
    ua: Optional[str]

    def touch(self) -> None:
        if SESSION_IDLE_MINUTES > 0:
            self.expires_at = datetime.utcnow() + timedelta(minutes=SESSION_IDLE_MINUTES)


_sessions: Dict[str, SessionData] = {}
_s_lock = threading.Lock()


def create_session(username: str, request: Request, *, ttl: Optional[int] = None) -> SessionData:
    now = datetime.utcnow()
    sid = secrets.token_urlsafe(32)
    ttl_m = ttl if ttl and ttl > 0 else SESSION_TTL_MINUTES
    data = SessionData(
        username=username,
        session_id=sid,
        issued_at=now,
        expires_at=now + timedelta(minutes=ttl_m),
        ip=request.client.host if request.client else None,
        ua=request.headers.get("user-agent"),
    )
    with _s_lock:
        _sessions[sid] = data
    return data


def remove_session(session_id: str) -> None:
    with _s_lock:
        _sessions.pop(session_id, None)


def get_session(session_id: str) -> Optional[SessionData]:
    with _s_lock:
        s = _sessions.get(session_id)
        if not s:
            return None
        if s.expires_at <= datetime.utcnow():
            _sessions.pop(session_id, None)
            return None
        s.touch()
        return s


class LoginRequest(BaseModel):
    username: str
    password: str
    remember_me: Optional[bool] = False


class LoginResponse(BaseModel):
    username: str
    expires_at: str


class LogoutResponse(BaseModel):
    status: str


@dataclass
class Requester:
    username: str
    via: str
    session: Optional[SessionData] = None


def require_user(request: Request, authorization: Optional[str] = Header(None)) -> Requester:
    # Allow service Bearer token (for ChatGPT Actions)
    if authorization and authorization.startswith("Bearer ") and SERVICE_BEARER_TOKEN:
        provided = authorization.split(" ", 1)[1]
        if secrets.compare_digest(provided, SERVICE_BEARER_TOKEN):
            return Requester(username="token", via="bearer")
        raise HTTPException(status_code=403, detail="Invalid token")
    # Session cookie
    sid = request.cookies.get(SESSION_COOKIE_NAME)
    if not sid:
        raise HTTPException(status_code=401, detail="未登入或工作階段失效")
    s = get_session(sid)
    if not s:
        raise HTTPException(status_code=401, detail="工作階段不存在或已過期")
    return Requester(username=s.username, via="session", session=s)


@app.post("/login", response_model=LoginResponse)
def login(req: LoginRequest, request: Request, response: Response):
    if not auth_store.users:
        raise HTTPException(status_code=503, detail="尚未設定帳號，請配置 AUTH_USERS")
    ok = auth_store.verify(req.username, req.password)
    if not ok:
        log_audit("login", request=request, success=False, username=req.username)
        raise HTTPException(status_code=401, detail="帳號或密碼錯誤")

    ttl = SESSION_REMEMBER_MINUTES if req.remember_me else SESSION_TTL_MINUTES
    s = create_session(req.username, request, ttl=ttl)
    max_age = ttl * 60 if ttl > 0 else None
    exp_cookie = s.expires_at.replace(tzinfo=timezone.utc) if max_age else None
    same = SESSION_COOKIE_SAMESITE if SESSION_COOKIE_SAMESITE in {"none", "lax", "strict"} else "none"
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=s.session_id,
        max_age=max_age,
        expires=exp_cookie,
        httponly=True,
        secure=SESSION_COOKIE_SECURE,
        samesite=same,
        path="/",
    )
    log_audit("login", request=request, success=True, username=req.username, details={"ttl": ttl})
    return LoginResponse(username=req.username, expires_at=s.expires_at.isoformat() + "Z")


@app.post("/logout", response_model=LogoutResponse)
def logout(request: Request, response: Response, requester: Requester = Depends(require_user)):
    sid = request.cookies.get(SESSION_COOKIE_NAME)
    if requester.via == "session" and sid:
        remove_session(sid)
    response.delete_cookie(SESSION_COOKIE_NAME, path="/")
    log_audit("logout", request=request, success=True, username=requester.username)
    return LogoutResponse(status="ok")


@app.get("/health")
def health():
    # Ensure API key exists to avoid confusing startup states
    has_key = bool(os.getenv("OPENAI_API_KEY"))
    return {"status": "ok", "openai_key": has_key}


@app.get("/me")
def me(request: Request, requester: Requester = Depends(require_user)):
    payload: Dict[str, Any] = {"username": requester.username, "via": requester.via}
    if requester.session:
        payload["expires_at"] = requester.session.expires_at.isoformat() + "Z"
    log_audit("whoami", request=request, success=True, username=requester.username)
    return payload


@app.get("/files", response_model=List[str])
def list_files(request: Request, requester: Requester = Depends(require_user)):
    files = rag_impl._supported_files()
    log_audit("list_files", request=request, success=True, username=requester.username, details={"count": len(files)})
    return files


@app.post("/query", response_model=QueryResponse)
def query(req: QueryRequest, request: Request, requester: Requester = Depends(require_user)):
    files = rag_impl._supported_files()
    if req.file not in files:
        log_audit("query", request=request, success=False, username=requester.username, details={"file": req.file, "reason": "not_found"})
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
        log_audit("query", request=request, success=False, username=requester.username, details={"file": req.file, "error": str(e)})
        raise HTTPException(status_code=500, detail=str(e))

    docs = result.get("source_documents", []) or []
    answer: str = result.get("result", "")
    srcs: List[str] = []
    for d in docs:
        src = str((d.metadata or {}).get("source") or "")
        if src and src not in srcs:
            srcs.append(src)

    snippets: List[SourceSnippet] = []
    if req.include_snippets:
        seen: set[str] = set()
        limit = max(5, (req.top_k or 3))
        for d in docs[:limit]:
            src = str((d.metadata or {}).get("source") or "")
            content = (d.page_content or "").strip()
            if not src or not content or src in seen:
                continue
            clean = re.sub(r"\s+", " ", content)
            if len(clean) > 700:
                clean = clean[:700].rstrip() + "…"
            snippets.append(SourceSnippet(source=src, snippet=clean))
            seen.add(src)

    log_audit(
        "query",
        request=request,
        success=True,
        username=requester.username,
        details={
            "file": req.file,
            "k": req.top_k,
            "q": _clip(req.question),
            "sources": len(srcs),
            "snippets": len(snippets) if req.include_snippets else 0,
        },
    )
    return QueryResponse(answer=answer, sources=srcs, snippets=snippets or None)


@app.post("/reload")
def reload_file(req: ReloadRequest, request: Request, requester: Requester = Depends(require_user)):
    files = rag_impl._supported_files()
    if req.file not in files:
        log_audit("reload", request=request, success=False, username=requester.username, details={"file": req.file, "reason": "not_found"})
        raise HTTPException(status_code=404, detail=f"File not found or unsupported: {req.file}")
    with lock:
        rag_impl.build_or_load_db_for_file(req.file, force=True)
    log_audit("reload", request=request, success=True, username=requester.username, details={"file": req.file})
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
