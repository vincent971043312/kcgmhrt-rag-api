"use client";

import { useEffect, useState } from "react";
import type { CSSProperties, FormEvent, ReactNode } from "react";

type SessionInfo = { username: string; expires_at?: string; via?: string };
type Toast = { text: string; kind: "ok" | "err" } | null;
type CategoryInfo = { key: string; label: string; total: number };

type QueryPayload = {
  question: string;
  top_k: number;
  include_snippets: boolean;
  category?: string;
  file?: string;
};
type QueryResp = {
  answer: string;
  sources?: string[];
};

const containerStyle: CSSProperties = {
  maxWidth: 900,
  margin: "2rem auto",
  padding: "0 1rem 4rem",
};

const boxStyle: CSSProperties = {
  background: "#fff",
  border: "1px solid #d1d5db",
  borderRadius: 12,
  padding: 16,
  marginTop: 16,
  boxShadow: "0 8px 20px rgba(15, 23, 42, 0.08)",
};

const labelStyle: CSSProperties = { display: "block", fontWeight: 600, marginBottom: 6 };

const CATEGORY_ONLY_VALUE = "__category__";

function Box({ children }: { children: ReactNode }) {
  return <section style={boxStyle}>{children}</section>;
}

function safeMessage(value: unknown): string {
  if (!value) return "";
  if (typeof value === "string") return value;
  if (typeof value === "object" && value && "message" in value) {
    return String((value as { message?: unknown }).message ?? "");
  }
  try {
    return JSON.stringify(value);
  } catch {
    return String(value);
  }
}

const basename = (path: string) => path.replace(/\\/g, "/").split("/").filter(Boolean).pop() || path;

export default function Page() {
  const [session, setSession] = useState<SessionInfo | null>(null);
  const [checking, setChecking] = useState(true);
  const [loginUser, setLoginUser] = useState("");
  const [loginPass, setLoginPass] = useState("");
  const [loginLoading, setLoginLoading] = useState(false);
  const [logoutLoading, setLogoutLoading] = useState(false);
  const [authError, setAuthError] = useState<string | null>(null);

  const [categories, setCategories] = useState<CategoryInfo[]>([]);
  const [selectedCategory, setSelectedCategory] = useState<string>("");
  const [files, setFiles] = useState<string[]>([]);
  const [selectedFile, setSelectedFile] = useState<string>(CATEGORY_ONLY_VALUE);
  const [question, setQuestion] = useState("");
  const [answer, setAnswer] = useState("");
  const [sources, setSources] = useState<string[]>([]);
  const [toast, setToast] = useState<Toast>(null);
  const [loading, setLoading] = useState(false);
  const [reloading, setReloading] = useState(false);

  function pushToast(text: string, kind: "ok" | "err") {
    setToast({ text, kind });
    setTimeout(() => setToast(null), 4500);
  }

  function handleUnauthorized(message?: string) {
    setSession(null);
    setCategories([]);
    setSelectedCategory("");
    setFiles([]);
    setSelectedFile(CATEGORY_ONLY_VALUE);
    setQuestion("");
    setAnswer("");
    setSources([]);
    setLoading(false);
    setReloading(false);
    setLogoutLoading(false);
    pushToast(message || "登入已過期，請重新登入", "err");
  }

  const canAsk = Boolean(
    session &&
      question.trim() &&
      (selectedFile === CATEGORY_ONLY_VALUE || selectedFile.length > 0) &&
      selectedCategory
  );

  async function fetchSession() {
    try {
      const r = await fetch("/api/me", { cache: "no-store" });
      if (!r.ok) {
        setSession(null);
      } else {
        const data = (await r.json()) as SessionInfo;
        setSession(data);
      }
    } catch {
      setSession(null);
    } finally {
      setChecking(false);
    }
  }

  async function fetchCategories() {
    try {
      const r = await fetch("/api/categories", { cache: "no-store" });
      if (r.status === 401) {
        handleUnauthorized();
        return;
      }
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const data = (await r.json()) as CategoryInfo[];
      setCategories(data);
      const preferred = data.find((c) => c.key === selectedCategory)
        || data.find((c) => c.total > 0)
        || data[0];
      if (preferred) {
        setSelectedCategory(preferred.key);
        await fetchFiles(preferred.key, true);
      } else {
        setSelectedCategory("");
        setFiles([]);
        setSelectedFile(CATEGORY_ONLY_VALUE);
      }
    } catch (e) {
      pushToast(`讀取分類失敗：${safeMessage(e)}`, "err");
    }
  }

  async function fetchFiles(category: string, resetSelection = false) {
    if (!category) {
      setFiles([]);
      setSelectedFile(CATEGORY_ONLY_VALUE);
      return;
    }
    try {
      const url = `/api/files?category=${encodeURIComponent(category)}`;
      const r = await fetch(url, { cache: "no-store" });
      if (r.status === 401) {
        handleUnauthorized();
        return;
      }
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const list = (await r.json()) as string[];
      const normalized = list?.map((item) => item.replace(/\\/g, "/")) ?? [];
      setFiles(normalized);
      if (resetSelection) {
        setSelectedFile(CATEGORY_ONLY_VALUE);
      } else if (normalized.length === 0) {
        setSelectedFile(CATEGORY_ONLY_VALUE);
      } else if (!normalized.includes(selectedFile)) {
        setSelectedFile(CATEGORY_ONLY_VALUE);
      }
    } catch (e) {
      pushToast(`載入檔案清單失敗：${safeMessage(e)}`, "err");
    }
  }

  useEffect(() => {
    fetchSession();
  }, []);

  useEffect(() => {
    if (session) {
      fetchCategories();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [session?.username]);

  async function doReload() {
    if (!session || !selectedCategory) return;
    setReloading(true);
    try {
      const payload: { category?: string; file?: string } = {};
      if (selectedFile === CATEGORY_ONLY_VALUE) {
        payload.category = selectedCategory;
      } else {
        payload.file = selectedFile;
      }
      const r = await fetch("/api/reload", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      if (r.status === 401) {
        handleUnauthorized();
        return;
      }
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const data = await r.json();
      if (data?.category) {
        pushToast(`分類 ${data.category} 已重建`, "ok");
      } else if (data?.file) {
        pushToast(`重建完成：${basename(data.file)}`, "ok");
      } else {
        pushToast("重建完成", "ok");
      }
    } catch (e) {
      pushToast(`重建失敗：${safeMessage(e)}`, "err");
    } finally {
      setReloading(false);
    }
  }

  async function doAsk() {
    if (!canAsk || !selectedCategory) return;
    setLoading(true);
    setAnswer("(查詢中...)");
    setSources([]);
    try {
      const payload: QueryPayload = {
        question,
        top_k: 8,
        include_snippets: false,
      };
      if (selectedFile === CATEGORY_ONLY_VALUE) {
        payload.category = selectedCategory;
      } else {
        payload.file = selectedFile;
      }
      const r = await fetch("/api/query", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      if (r.status === 401) {
        handleUnauthorized("登入已過期，查詢未送出");
        setAnswer("查詢失敗：請重新登入。");
        return;
      }
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const data = (await r.json()) as QueryResp;
      setAnswer(data?.answer || "(無回答)");
      setSources(Array.isArray(data?.sources) ? data.sources : []);
    } catch (e) {
      setAnswer(`查詢失敗：${safeMessage(e)}`);
    } finally {
      setLoading(false);
    }
  }

  async function onLogin(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();

    const formData = new FormData(e.currentTarget);
    const autofillUser = (formData.get("username") || "").toString().trim();
    const autofillPass = (formData.get("password") || "").toString();
    const user = (loginUser || autofillUser).trim();
    const pwd = loginPass || autofillPass;

    if (!user || !pwd) {
      setAuthError("請輸入帳號與密碼");
      return;
    }

    setLoginUser(user);
    setLoginPass(pwd);
    setAuthError(null);
    setLoginLoading(true);
    try {
      const r = await fetch("/api/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ username: user, password: pwd }),
      });
      const text = await r.text();
      if (!r.ok) {
        let msg = text || `HTTP ${r.status}`;
        try {
          const json = JSON.parse(text);
          msg = json?.detail || json?.message || msg;
        } catch {
          /* ignore */
        }
        setAuthError(msg);
        if (r.status === 401) setSession(null);
        return;
      }
      let loginInfo: SessionInfo | null = null;
      try {
        const parsed = JSON.parse(text) as Partial<SessionInfo> | null;
        if (parsed && parsed.username) {
          loginInfo = {
            username: parsed.username,
            expires_at: parsed.expires_at,
            via: parsed.via ?? "session",
          };
          setSession(loginInfo);
        }
      } catch {
        /* ignore */
      }
      if (!loginInfo) {
        await fetchSession();
      }
      pushToast(`歡迎 ${loginInfo?.username ?? user}`, "ok");
      setLoginUser("");
      setLoginPass("");
    } catch (e) {
      setAuthError(safeMessage(e));
    } finally {
      setLoginLoading(false);
    }
  }

  async function doLogout() {
    if (!session) return;
    setLogoutLoading(true);
    try {
      const r = await fetch("/api/logout", { method: "POST" });
      if (!r.ok) {
        const text = await r.text();
        pushToast(`登出失敗：${text || `HTTP ${r.status}`}`, "err");
      } else {
        pushToast("已登出", "ok");
      }
    } catch (e) {
      pushToast(`登出失敗：${safeMessage(e)}`, "err");
    } finally {
      setLogoutLoading(false);
      setSession(null);
      setCategories([]);
      setSelectedCategory("");
      setFiles([]);
      setSelectedFile(CATEGORY_ONLY_VALUE);
      setQuestion("");
      setAnswer("");
      setSources([]);
    }
  }

  const docUrl = (src: string) => {
    const segments = src.replace(/\\/g, "/").split("/").filter(Boolean);
    return `/api/doc/${segments.map((part) => encodeURIComponent(part)).join('/')}`;
  };

  const currentCategoryLabel =
    categories.find((c) => c.key === selectedCategory)?.label || selectedCategory || "";

  const currentCategoryTotal = categories.find((c) => c.key === selectedCategory)?.total ?? 0;

  return (
    <div style={containerStyle}>
      <header style={{ textAlign: "center", marginBottom: 24 }}>
        <h1 style={{ marginBottom: 4 }}>高雄長庚呼吸治療科 AI 查詢網站</h1>
        <p style={{ color: "#64748b", fontSize: 14 }}>
          內部臨床文件安全檢索，支援來源追蹤與原始檔下載
        </p>
      </header>

      {session ? (
        <>
          <Box>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
              <div>
                <div style={{ fontSize: 12, color: "#6b7280" }}>登入帳號</div>
                <div style={{ fontWeight: 600 }}>{session.username}</div>
                {session.expires_at && (
                  <div style={{ fontSize: 12, color: "#6b7280" }}>
                    工作階段到期：{new Date(session.expires_at).toLocaleString()}
                  </div>
                )}
              </div>
              <button onClick={doLogout} disabled={logoutLoading}>
                {logoutLoading ? "登出中..." : "登出"}
              </button>
            </div>
          </Box>

          <Box>
            <label style={labelStyle}>分類</label>
            <div style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
              {categories.map((cat) => (
                <button
                  key={cat.key}
                  type="button"
                  onClick={() => {
                    setSelectedCategory(cat.key);
                    fetchFiles(cat.key, true);
                  }}
                  className={selectedCategory === cat.key ? "category-button active" : "category-button"}
                  disabled={!cat.total}
                >
                  {`${cat.label} (${cat.total})`}
                </button>
              ))}
            </div>

            <label style={{ ...labelStyle, marginTop: 16 }}>檔案清單</label>
            <select
              value={selectedFile}
              onChange={(e) => setSelectedFile(e.target.value)}
              style={{ width: "100%", padding: 8 }}
              disabled={!selectedCategory}
            >
              {selectedCategory && (
                <option value={CATEGORY_ONLY_VALUE}>
                  {currentCategoryLabel ? `${currentCategoryLabel}（整個分類）` : "整個分類"}
                </option>
              )}
              {files.map((f) => (
                <option key={f} value={f}>
                  {basename(f)}
                </option>
              ))}
            </select>
            <div style={{ display: "flex", gap: 8, marginTop: 8 }}>
              <button onClick={() => fetchFiles(selectedCategory, false)} disabled={!selectedCategory}>
                重新載入清單
              </button>
              <button onClick={doReload} disabled={!selectedCategory || reloading}>
                {reloading ? "重建中..." : "重建索引"}
              </button>
            </div>
            <p style={{ fontSize: 12, color: "#6b7280", marginTop: 4 }}>
              {selectedCategory
                ? `目前分類共有 ${currentCategoryTotal} 份文件，可選擇整個分類或特定文件進行查詢。`
                : "請先選擇分類。"}
            </p>

            <div style={{ marginTop: 16 }}>
              <label style={labelStyle}>問題</label>
              <textarea
                value={question}
                onChange={(e) => setQuestion(e.target.value)}
                placeholder="例如：C1 密碼是什麼？或：請條列摘要重點"
                style={{ width: "100%", minHeight: 120, padding: 8 }}
              />
              <div style={{ display: "flex", justifyContent: "flex-end", marginTop: 8 }}>
                <button onClick={doAsk} disabled={!canAsk || loading}>
                  {loading ? "查詢中..." : "送出查詢"}
                </button>
              </div>
            </div>
          </Box>

          <Box>
            <h2 style={{ margin: 0, fontSize: 18 }}>Answer</h2>
            <p style={{ whiteSpace: "pre-wrap", marginTop: 8 }}>{answer || "(尚未查詢)"}</p>
          </Box>

          <Box>
            <h3 style={{ margin: 0, fontSize: 16 }}>Sources</h3>
            <ul style={{ marginTop: 12, paddingLeft: 20, display: "grid", gap: 6 }}>
              {sources.map((src) => (
                <li key={src}>
                  <a href={docUrl(src)} target="_blank" rel="noopener noreferrer" className="source-link">
                    {basename(src)}
                  </a>
                </li>
              ))}
            </ul>
          </Box>
        </>
      ) : (
        <Box>
          {checking ? (
            <p style={{ textAlign: "center", color: "#6b7280" }}>正在確認登入狀態…</p>
          ) : (
            <form onSubmit={onLogin} style={{ display: "grid", gap: 12 }}>
              <div>
                <h2 style={{ margin: 0 }}>請先登入</h2>
                <p style={{ margin: 0, color: "#6b7280", fontSize: 14 }}>
                  此服務需要帳號密碼方可使用，所有操作都會記錄於審計日誌。
                </p>
              </div>
              {authError && (
                <div style={{ color: "#b91c1c", background: "#fee2e2", border: "1px solid #fecaca", padding: 8, borderRadius: 8 }}>
                  {authError}
                </div>
              )}
              <label style={labelStyle}>
                帳號
                <input
                  name="username"
                  value={loginUser}
                  onChange={(e) => setLoginUser(e.target.value)}
                  autoComplete="username"
                  required
                  style={{ width: "100%", padding: 8 }}
                />
              </label>
              <label style={labelStyle}>
                密碼
                <input
                  name="password"
                  value={loginPass}
                  onChange={(e) => setLoginPass(e.target.value)}
                  type="password"
                  autoComplete="current-password"
                  required
                  style={{ width: "100%", padding: 8 }}
                />
              </label>
              <button type="submit" disabled={loginLoading || !loginUser || !loginPass}>
                {loginLoading ? "登入中..." : "登入"}
              </button>
            </form>
          )}
        </Box>
      )}

      {toast && (
        <div
          style={{
            position: "fixed",
            right: 16,
            top: 16,
            padding: "10px 14px",
            borderRadius: 10,
            background: toast.kind === "ok" ? "#059669" : "#e11d48",
            color: "#fff",
            boxShadow: "0 6px 18px rgba(15, 23, 42, 0.18)",
          }}
        >
          {toast.text}
        </div>
      )}
    </div>
  );
}
