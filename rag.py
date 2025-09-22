import os
import json
import shutil
import hashlib
import argparse
import inspect
try:
    # Load .env if available (non-fatal if missing)
    from dotenv import load_dotenv, find_dotenv  # type: ignore
    load_dotenv(find_dotenv(), override=False)
except Exception:
    # Fallback: basic .env parser (KEY=VALUE lines only)
    env_path = os.path.join(os.getcwd(), ".env")
    if os.path.exists(env_path):
        try:
            with open(env_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    k, v = line.split("=", 1)
                    k = k.strip()
                    v = v.strip().strip('"').strip("'")
                    os.environ.setdefault(k, v)
        except Exception:
            pass

from langchain_openai import OpenAIEmbeddings, ChatOpenAI
from langchain_chroma import Chroma
from langchain_community.document_loaders import (
    TextLoader,
    PyPDFLoader,
    UnstructuredMarkdownLoader,
    UnstructuredWordDocumentLoader,
)
from langchain.text_splitter import RecursiveCharacterTextSplitter
from langchain.chains import RetrievalQA
from langchain.docstore.document import Document

# ========= 設定 =========
# 可用環境變數覆寫（適合 Heroku/雲端）：
#   DOCS_DIR：來源文件目錄（預設 docs）
#   DB_DIR：向量資料庫路徑（預設 db；Heroku 請設為 /tmp/db）
#   COLLECTION_NAME：Chroma 集合前綴（預設 my_rag_db）
DOCS_DIR = os.getenv("DOCS_DIR", "docs")          # 放文件的資料夾
DB_DIR = os.getenv("DB_DIR", "db")                # 向量資料庫路徑
COLLECTION_NAME = os.getenv("COLLECTION_NAME", "my_rag_db")

# Embedding 設定：允許透過環境變數調整，避免一次批量太大造成 OpenAI 503
EMBED_MODEL = os.getenv("OPENAI_EMBED_MODEL", "text-embedding-3-small")
try:
    EMBED_BATCH_SIZE = max(1, int(os.getenv("OPENAI_EMBED_BATCH", "32")))
except ValueError:
    EMBED_BATCH_SIZE = 32
try:
    EMBED_TIMEOUT = float(os.getenv("OPENAI_EMBED_TIMEOUT", "120"))
except ValueError:
    EMBED_TIMEOUT = 120.0
try:
    EMBED_RETRIES = max(1, int(os.getenv("OPENAI_EMBED_RETRIES", "6")))
except ValueError:
    EMBED_RETRIES = 6


def _make_embeddings() -> OpenAIEmbeddings:
    """建立 OpenAIEmbeddings，必要時向下相容舊版參數。"""
    embed_kwargs = {
        "model": EMBED_MODEL,
        "chunk_size": EMBED_BATCH_SIZE,
        "max_retries": EMBED_RETRIES,
    }
    if EMBED_TIMEOUT > 0:
        embed_kwargs["request_timeout"] = EMBED_TIMEOUT
    try:
        return OpenAIEmbeddings(**embed_kwargs)
    except TypeError:
        embed_kwargs.pop("request_timeout", None)
        return OpenAIEmbeddings(**embed_kwargs)

CATEGORY_LABELS = {
    "instrument-manuals": "儀器類操作規範",
    "training-manuals": "教學類操作規範",
    "meetings": "會議紀錄",
}

MEETING_BUNDLES: dict[str, list[str]] = {
    "meetings/2025年6-8月基層主管會議紀錄": [
        "meetings/2025年6月基層主管會議記錄.pdf",
        "meetings/2025年7月基層主管會議記錄.pdf",
        "meetings/2025年8月基層主管會議記錄.pdf",
    ],
}
CATEGORY_KEYS = list(CATEGORY_LABELS.keys())

# ========= 驗證金鑰 =========
if not os.getenv("OPENAI_API_KEY"):
    raise RuntimeError(
        "缺少 OPENAI_API_KEY。請在環境變數或 .env 中設定，例如：\n"
        "1) 匯出：export OPENAI_API_KEY=your_key\n"
        "2) 或建立 .env 檔：OPENAI_API_KEY=your_key"
    )

def _iter_doc_paths():
    for root, _, files in os.walk(DOCS_DIR):
        rel_dir = os.path.relpath(root, DOCS_DIR)
        rel_dir = "" if rel_dir == "." else rel_dir
        for file in files:
            # Skip alternate data stream artifacts
            if ":" in file or file.endswith("Zone.Identifier"):
                print(f"⏭️ 跳過附帶資料檔: {os.path.join(rel_dir, file)}")
                continue
            lower = file.lower()
            if not lower.endswith((".txt", ".pdf", ".md", ".docx", ".doc")):
                print(f"⚠️ 不支援的檔案格式: {os.path.join(rel_dir, file)}")
                continue
            rel_path = os.path.join(rel_dir, file) if rel_dir else file
            yield rel_path.replace("\\", "/")


def _supported_files():
    files = sorted(_iter_doc_paths())
    files.extend(sorted(MEETING_BUNDLES.keys()))
    return files


def _expand_meeting_bundle(path: str) -> list[str]:
    clean = path.replace("\\", "/").strip()
    return MEETING_BUNDLES.get(clean, [clean])


def _load_documents_for_path(rel_path: str) -> list[Document]:
    normalized = rel_path.replace("\\", "/").strip()
    abs_path = os.path.join(DOCS_DIR, *normalized.split("/"))
    lower = normalized.lower()
    if lower.endswith(".txt"):
        loader = TextLoader(abs_path, encoding="utf-8")
    elif lower.endswith(".pdf"):
        loader = PyPDFLoader(abs_path)
    elif lower.endswith(".md"):
        loader = UnstructuredMarkdownLoader(abs_path)
    elif lower.endswith((".docx", ".doc")):
        loader = UnstructuredWordDocumentLoader(abs_path)
    else:
        raise ValueError(f"不支援的檔案格式: {rel_path}")

    raw_docs = loader.load()
    docs: list[Document] = []
    for d in raw_docs:
        meta = dict(d.metadata or {})
        meta["source"] = normalized
        docs.append(Document(page_content=d.page_content, metadata=meta))
    return docs


def _iter_doc_paths_from_category(category: str) -> list[str]:
    prefix = category.replace("\\", "/").strip("/")
    if not prefix:
        return []
    if prefix == "meetings":
        return list(MEETING_BUNDLES.keys())
    prefixed = []
    for rel_path in _iter_doc_paths():
        if rel_path.startswith(prefix + "/"):
            prefixed.append(rel_path)
    return prefixed


def available_categories() -> list[tuple[str, int]]:
    results: list[tuple[str, int]] = []
    for key in CATEGORY_KEYS:
        if key == "meetings":
            count = len(MEETING_BUNDLES)
        else:
            files = _iter_doc_paths_from_category(key)
            count = len(files)
        results.append((key, count))
    return results


def category_label(category: str) -> str:
    return CATEGORY_LABELS.get(category, category)


def _compute_manifest(files):
    manifest = []
    for f in files:
        rel_path = f.replace("\\", "/")
        p = os.path.join(DOCS_DIR, *rel_path.split("/"))
        try:
            st = os.stat(p)
            manifest.append({
                "name": f,
                "size": st.st_size,
                "mtime": int(st.st_mtime),
            })
        except FileNotFoundError:
            continue
    return manifest


def _manifests_equal(a, b):
    if len(a) != len(b):
        return False
    a_sorted = sorted(a, key=lambda x: x["name"])
    b_sorted = sorted(b, key=lambda x: x["name"])
    return a_sorted == b_sorted


def _safe_stem(filename: str) -> str:
    """Return an ASCII-only, Chroma-safe stem.

    Rules (to satisfy Chroma collection name constraints):
    - Only allow [A-Za-z0-9._-]
    - Must start and end with [A-Za-z0-9]
    - Length between 3 and 512 (we cap at 100 for path brevity)
    - Fallback to 'doc' if empty/invalid
    """
    import re
    import unicodedata

    base = os.path.splitext(os.path.basename(filename))[0]
    # Normalize and drop non-ASCII
    norm = unicodedata.normalize('NFKD', base).encode('ascii', 'ignore').decode('ascii')
    # Replace invalid chars with underscore
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", norm)
    # Trim leading/trailing non-alnum to satisfy start/end rule
    safe = re.sub(r"^[^A-Za-z0-9]+", "", safe)
    safe = re.sub(r"[^A-Za-z0-9]+$", "", safe)
    # Collapse consecutive invalid artifacts like multiple underscores or dots around dashes (optional)
    safe = re.sub(r"_{2,}", "_", safe)
    if not safe or len(safe) < 3:
        safe = "doc"
    return safe[:100]


def _make_chat_llm(max_tokens: int = 512) -> ChatOpenAI:
    """建立 ChatOpenAI；若支援 `max_tokens` 則直接傳入，否則退回 `model_kwargs`。
    這可避免『請顯式指定參數』的警告，同時相容舊版型別檢查。
    """
    kwargs: dict = {"model": "gpt-5-2025-08-07", "temperature": 0}
    try:
        params = inspect.signature(ChatOpenAI).parameters
    except Exception:
        params = {}
    if "max_tokens" in params:
        kwargs["max_tokens"] = max_tokens
    else:
        kwargs["model_kwargs"] = {"max_tokens": max_tokens}
    return ChatOpenAI(**kwargs)

# ========= 載入文件（保留頁碼/來源於 metadata） =========
def load_documents():
    all_docs = []
    for file in _supported_files():
        rel_path = file.replace("\\", "/")
        file_path = os.path.join(DOCS_DIR, *rel_path.split("/"))
        lower = file.lower()
        if lower.endswith(".txt"):
            loader = TextLoader(file_path, encoding="utf-8")
        elif lower.endswith(".pdf"):
            loader = PyPDFLoader(file_path)
        elif lower.endswith(".md"):
            loader = UnstructuredMarkdownLoader(file_path)
        elif lower.endswith((".docx", ".doc")):
            loader = UnstructuredWordDocumentLoader(file_path)
        else:
            continue

        raw_docs = loader.load()
        for d in raw_docs:
            meta = dict(d.metadata or {})
            meta["source"] = file  # 保留來源檔名
            all_docs.append(
                Document(
                    page_content=d.page_content,
                    metadata=meta,
                )
            )
    return all_docs

"""
舊版一次性建立整個 docs/ 的資料庫已移除需求。
改為：每個檔案各自建立到 db/<safe_stem>/ 下。
保留 load_documents() 僅供未來需要時使用。
"""


# ========= 針對單一檔案建立 / 載入資料庫 =========
def build_or_load_db_for_file(file: str, force: bool = False) -> Chroma:
    # 驗證檔案
    files = _supported_files()
    if file not in files:
        raise ValueError(f"檔案不存在或不支援: {file}")

    # 嵌入設定：小批次避免超過 OpenAI 單請求 token 上限
    embeddings = _make_embeddings()

    rel_path = file.replace("\\", "/")
    safe_key = rel_path.replace("/", "__")
    safe = _safe_stem(safe_key)
    # 若多個檔案清理後同名，為避免衝突，附加短雜湊
    try:
        files_all = _supported_files()
        collisions = [
            f for f in files_all if _safe_stem(f.replace("/", "__")) == safe
        ]
    except Exception:
        collisions = [file]
    if len(collisions) > 1:
        short = hashlib.sha1(rel_path.encode('utf-8')).hexdigest()[:8]
        safe = f"{safe}-{short}"
    subdir = os.path.join(DB_DIR, safe)
    os.makedirs(subdir, exist_ok=True)
    try:
        os.chmod(subdir, 0o755)
    except Exception:
        pass
    manifest_path = os.path.join(subdir, "manifest.json")
    collection = f"{COLLECTION_NAME}_{safe}"

    bundle_paths = _expand_meeting_bundle(rel_path)
    is_bundle = bundle_paths != [rel_path]
    manifest_sources = bundle_paths if is_bundle else [rel_path]
    new_manifest = _compute_manifest(manifest_sources)

    # 強制重建
    if force:
        print(f"♻️ 重新建立（強制）{file} 的資料庫...")
        shutil.rmtree(subdir, ignore_errors=True)
        os.makedirs(subdir, exist_ok=True)
    # 若目錄已有內容且 manifest 未變更，直接載入
    elif any(os.scandir(subdir)):
        old_manifest = []
        if os.path.exists(manifest_path):
            try:
                with open(manifest_path, "r", encoding="utf-8") as f:
                    old_manifest = json.load(f)
            except Exception:
                old_manifest = []
        if _manifests_equal(old_manifest, new_manifest):
            print(f"🔄 載入既有資料庫（{file}）...")
            return Chroma(
                collection_name=collection,
                embedding_function=embeddings,
                persist_directory=subdir,
            )
        else:
            print(f"♻️ 偵測到 {file} 有變更，重新建立該資料庫...")
            shutil.rmtree(subdir, ignore_errors=True)
            os.makedirs(subdir, exist_ok=True)

    # 建立新資料庫（僅此檔案）
    print(f"📂 建立新資料庫（{file}）...")
    docs: list[Document] = []
    for source_path in manifest_sources:
        docs.extend(_load_documents_for_path(source_path))

    splitter = RecursiveCharacterTextSplitter(
        chunk_size=500,
        chunk_overlap=50,
        separators=["\n\n", "。", "！", "？", "；", "\n", "，", "、", " "]
    )
    docs = splitter.split_documents(docs)

    vectorstore = Chroma.from_documents(
        documents=docs,
        embedding=embeddings,
        collection_name=collection,
        persist_directory=subdir,
    )

    try:
        with open(manifest_path, "w", encoding="utf-8") as f:
            json.dump(new_manifest, f, ensure_ascii=False, indent=2)
    except Exception:
        pass

    print(f"✅ {file} 的資料庫建立完成")
    return vectorstore


def build_or_load_db_for_category(category: str, force: bool = False) -> Chroma:
    key = category.replace("\\", "/").strip("/")
    if not key:
        raise ValueError("分類名稱不可為空")

    files = _iter_doc_paths_from_category(key)
    if not files:
        raise ValueError(f"分類 {category} 尚無可用文件")

    embeddings = _make_embeddings()

    safe = _safe_stem(f"cat-{key}")
    subdir = os.path.join(DB_DIR, safe)
    os.makedirs(subdir, exist_ok=True)
    try:
        os.chmod(subdir, 0o755)
    except Exception:
        pass

    manifest_path = os.path.join(subdir, "manifest.json")
    collection = f"{COLLECTION_NAME}_{safe}"
    expanded_manifest: list[str] = []
    if key == "meetings":
        for item in files:
            expanded_manifest.extend(_expand_meeting_bundle(item))
    else:
        expanded_manifest = files
    new_manifest = _compute_manifest(expanded_manifest)

    if force:
        print(f"♻️ 重新建立分類 {category} 的資料庫...")
        shutil.rmtree(subdir, ignore_errors=True)
        os.makedirs(subdir, exist_ok=True)
    elif any(os.scandir(subdir)):
        old_manifest = []
        if os.path.exists(manifest_path):
            try:
                with open(manifest_path, "r", encoding="utf-8") as f:
                    old_manifest = json.load(f)
            except Exception:
                old_manifest = []
        if _manifests_equal(old_manifest, new_manifest):
            print(f"🔄 載入既有分類資料庫（{category}）...")
            return Chroma(
                collection_name=collection,
                embedding_function=embeddings,
                persist_directory=subdir,
            )
        else:
            print(f"♻️ 偵測到分類 {category} 有變更，重新建立資料庫...")
            shutil.rmtree(subdir, ignore_errors=True)
            os.makedirs(subdir, exist_ok=True)

    print(f"📚 建立分類資料庫（{category}）...")
    all_docs = []
    for rel_path in files:
        for expanded in _expand_meeting_bundle(rel_path):
            all_docs.extend(_load_documents_for_path(expanded))

    splitter = RecursiveCharacterTextSplitter(
        chunk_size=500,
        chunk_overlap=50,
        separators=["\n\n", "。", "！", "？", "；", "\n", "，", "、", " "],
    )
    all_docs = splitter.split_documents(all_docs)

    vectorstore = Chroma.from_documents(
        documents=all_docs,
        embedding=embeddings,
        collection_name=collection,
        persist_directory=subdir,
    )

    try:
        with open(manifest_path, "w", encoding="utf-8") as f:
            json.dump(new_manifest, f, ensure_ascii=False, indent=2)
    except Exception:
        pass

    print(f"✅ {category} 分類資料庫建立完成")
    return vectorstore


def run_chat_for_file(file: str):
    print(f"📚 目前聊天檔案：{file}")
    vectorstore = build_or_load_db_for_file(file)
    retriever = vectorstore.as_retriever(search_kwargs={"k": 3})
    qa = RetrievalQA.from_chain_type(
        llm=_make_chat_llm(max_tokens=512),
        retriever=retriever,
    )

    print("\n🤖 RAG 問答系統已啟動！（輸入 exit 離開；輸入 /help 看指令）")
    while True:
        query = input("\n❓ 輸入你的問題: ").strip()
        if not query:
            continue
        low = query.lower()

        # 控制指令
        if low in {"exit", ":q", "/exit", ":exit"}:
            break
        if low in {"/help", ":help", "help"}:
            print("\n指令：\n  /list              列出可用檔案\n  /switch <檔或序號> 切換聊天檔案\n  /reload            重新建立目前檔案索引\n  /current           顯示目前聊天檔案\n  exit               離開\n")
            continue
        if low in {"/current", ":current"}:
            print(f"📚 目前聊天檔案：{file}")
            continue
        if low in {"/list", ":list", "/files", ":files"}:
            files = _supported_files()
            if files:
                print("📄 可用檔案：")
                for i, f in enumerate(files, 1):
                    print(f"  {i}. {f}")
            else:
                print("📭 docs/ 目前沒有可支援的檔案（支援 .pdf/.txt/.md）")
            continue
        if low.startswith("/switch") or low.startswith(":switch") or low.startswith("/open") or low.startswith(":open"):
            parts = query.split(maxsplit=1)
            target = None
            files = _supported_files()
            if len(parts) == 2:
                arg = parts[1].strip()
                if arg.isdigit() and 1 <= int(arg) <= len(files):
                    target = files[int(arg) - 1]
                elif arg in files:
                    target = arg
            if not target:
                print("❌ 請提供有效的檔名或序號（先用 /list 查詢）。")
                continue
            # 切換
            file = target
            print(f"🔁 切換至：{file}")
            vectorstore = build_or_load_db_for_file(file)
            retriever = vectorstore.as_retriever(search_kwargs={"k": 3})
            qa = RetrievalQA.from_chain_type(
                llm=_make_chat_llm(max_tokens=512),
                retriever=retriever,
            )
            continue
        if low in {"/reload", ":reload"}:
            print(f"♻️ 重新建立索引：{file}")
            vectorstore = build_or_load_db_for_file(file, force=True)
            retriever = vectorstore.as_retriever(search_kwargs={"k": 3})
            qa = RetrievalQA.from_chain_type(
                llm=_make_chat_llm(max_tokens=512),
                retriever=retriever,
            )
            continue

        # 一般問答
        answer = qa.run(query)
        print(f"👉 答案: {answer}")

# ========= 問答系統 =========
def start_chat():
    # 互動式選擇單一檔案再聊天
    files = _supported_files()
    if not files:
        print("📭 docs/ 目前沒有可支援的檔案（支援 .pdf/.txt/.md）")
        return
    print("📄 可用檔案：")
    for i, f in enumerate(files, 1):
        print(f"  {i}. {f}")
    choice = input("請輸入要聊天的檔案序號（或直接輸入檔名）: ").strip()
    if choice.isdigit() and 1 <= int(choice) <= len(files):
        target = files[int(choice) - 1]
    else:
        if choice in files:
            target = choice
        else:
            print("❌ 輸入無效，結束。")
            return
    run_chat_for_file(target)


def preindex_all(
    pdf_only: bool = True,
    force: bool = False,
    offset=None,
    limit=None,
    batch_size=None,
    batch_index=None,
):
    files = _supported_files()
    if pdf_only:
        filtered: list[str] = []
        for f in files:
            lower = f.lower()
            if lower.endswith(('.pdf', '.docx', '.doc')) or f in MEETING_BUNDLES:
                filtered.append(f)
        files = filtered
    total = len(files)
    if not files:
        print("📭 沒有符合的檔案可建立索引")
        return

    selected_info = ""
    # 優先使用 offset/limit（較直觀）
    if offset is not None or limit is not None:
        off = max(0, offset or 0)
        if limit is None:
            files = files[off:]
        else:
            if limit < 0:
                print("❌ --limit 需為非負整數")
                return
            files = files[off: off + limit]
        if not files:
            print("📭 依 offset/limit 沒有可處理的檔案")
            return
        selected_info = f"offset={off}, limit={limit if limit is not None else '∞'}"
    # 否則使用 batch-size/batch-index
    elif batch_size is not None or batch_index is not None:
        if batch_size is None or batch_index is None:
            print("❌ 分批需同時提供 --batch-size 與 --batch-index")
            return
        if batch_size <= 0 or batch_index <= 0:
            print("❌ --batch-size 與 --batch-index 需為正整數")
            return
        num_batches = (total + batch_size - 1) // batch_size
        if batch_index > num_batches:
            print(f"❌ 批次編號超出範圍：共 {num_batches} 批，收到 {batch_index}")
            return
        start = (batch_index - 1) * batch_size
        end = min(total, start + batch_size)
        files = files[start:end]
        if not files:
            print("📭 此批次沒有可處理的檔案")
            return
        selected_info = f"批次 {batch_index}/{num_batches}（索引 {start}..{end-1}）"

    print("🚀 開始建立索引（{}{}）...".format(
        "僅 PDF" if pdf_only else "全部支援檔案",
        f"；{selected_info}" if selected_info else ""
    ))
    success, failed = 0, []
    for i, f in enumerate(files, 1):
        print(f"\n[{i}/{len(files)}] 檔案：{f}")
        try:
            build_or_load_db_for_file(f, force=force)
            success += 1
        except Exception as e:
            print(f"❌ 失敗：{e}")
            failed.append((f, str(e)))
    print("\n✅ 完成：{} 成功，{} 失敗".format(success, len(failed)))
    if failed:
        print("失敗清單：")
        for f, msg in failed:
            print(" -", f, "=>", msg)


def main():
    parser = argparse.ArgumentParser(description="Per-file RAG for docs/")
    parser.add_argument("--list", action="store_true", help="列出可用檔案")
    parser.add_argument("--index", type=str, help="僅為指定檔案建立/更新索引後結束")
    parser.add_argument("--chat", type=str, help="為指定檔案建立/更新索引後進入聊天")
    parser.add_argument("--preindex", action="store_true", help="先為 docs/ 內所有 PDF 建立/更新索引後結束")
    parser.add_argument("--preindex-all", action="store_true", help="先為 docs/ 內所有支援檔案建立/更新索引後結束")
    parser.add_argument("--force", action="store_true", help="搭配 --preindex* 強制重建索引")
    # 分批/分段參數（搭配 --preindex* 使用）
    parser.add_argument("--offset", type=int, default=None, help="從此索引開始（0-based）")
    parser.add_argument("--limit", type=int, default=None, help="最多處理幾個檔案")
    parser.add_argument("--batch-size", type=int, default=None, help="每批處理幾個檔案")
    parser.add_argument("--batch-index", type=int, default=None, help="要處理第幾批（1-based）")
    args = parser.parse_args()

    files = _supported_files()
    if args.preindex or args.preindex_all:
        preindex_all(
            pdf_only=not args.preindex_all,
            force=args.force,
            offset=args.offset,
            limit=args.limit,
            batch_size=args.batch_size,
            batch_index=args.batch_index,
        )
        return
    if args.list:
        if files:
            print("📄 可用檔案：")
            for f in files:
                print(" -", f)
        else:
            print("📭 docs/ 目前沒有可支援的檔案（支援 .pdf/.txt/.md）")
        return

    if args.index:
        if args.index not in files:
            print("❌ 找不到檔案或不支援：", args.index)
            return
        build_or_load_db_for_file(args.index)
        return

    if args.chat:
        if args.chat not in files:
            print("❌ 找不到檔案或不支援：", args.chat)
            return
        run_chat_for_file(args.chat)
        return

    # 無參數：走互動式選擇
    start_chat()

if __name__ == "__main__":
    main()
