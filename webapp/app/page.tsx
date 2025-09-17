"use client";

import { useEffect, useMemo, useState } from "react";
import type { CSSProperties, FormEvent, ReactNode } from "react";

type SourceSnippet = { source: string; snippet: string };
type QueryResp = { answer: string; sources: string[]; snippets?: SourceSnippet[] };
type SessionInfo = { username: string; expires_at?: string; via?: string };
type Toast = { text: string; kind: "ok" | "err" } | null;

const containerStyle: CSSProperties = {
  maxWidth: 880,
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

export default function Page() {
  const [session, setSession] = useState<SessionInfo | null>(null);
  const [checking, setChecking] = useState(true);
  const [loginUser, setLoginUser] = useState("");
  const [loginPass, setLoginPass] = useState("");
  const [loginLoading, setLoginLoading] = useState(false);
  const [logoutLoading, setLogoutLoading] = useState(false);
  const [authError, setAuthError] = useState<string | null>(null);

  const [files, setFiles] = useState<string[]>([]);
  const [file, setFile] = useState("");
  const [question, setQuestion] = useState("");
  const [answer, setAnswer] = useState("");
  const [sources, setSources] = useState<string[]>([]);
  const [toast, setToast] = useState<Toast>(null);
  const [loading, setLoading] = useState(false);
  const [reloading, setReloading] = useState(false);
  const [snippets, setSnippets] = useState<SourceSnippet[]>([]);

  function pushToast(text: string, kind: "ok" | "err") {
    setToast({ text, kind });
    setTimeout(() => setToast(null), 4500);
  }

  function handleUnauthorized(message?: string) {
    setSession(null);
    setFiles([]);
    setFile("");
    setQuestion("");
    setAnswer("");
    setSources([]);
    setSnippets([]);
    setLoading(false);
    setReloading(false);
    setLogoutLoading(false);
    pushToast(message || "登入已過期，請重新登入", "err");
  }

  const canAsk = useMemo(
    () => !!session && !!file && !!question && !loading,
    [session, file, question, loading]
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

  useEffect(() => {
    fetchSession();
  }, []);

  useEffect(() => {
    if (session) {
      refreshFiles();
    } else {
      setFiles([]);
      setFile("");
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [session?.username]);

  async function refreshFiles() {
    if (!session) return;
    setFiles([]);
    setFile("");
    try {
      const r = await fetch("/api/files", { cache: "no-store" });
      if (r.status === 401) {
        handleUnauthorized();
        return;
      }
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const list = (await r.json()) as string[];
      setFiles(list || []);
      if (list?.length) setFile(list[0]);
    } catch (e) {
      pushToast(`載入檔案清單失敗：${safeMessage(e)}`, "err");
    }
  }

  async function doReload() {
    if (!session || !file) return;
    setReloading(true);
    try {
      const r = await fetch("/api/reload", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ file }),
      });
      if (r.status === 401) {
        handleUnauthorized();
        return;
      }
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const data = await r.json();
      pushToast(`重建完成：${data?.file || file}`, "ok");
    } catch (e) {
      pushToast(`重建失敗：${safeMessage(e)}`, "err");
    } finally {
      setReloading(false);
    }
  }

  async function doAsk() {
    if (!session || !file || !question) return;
    setLoading(true);
    setAnswer("(查詢中...)");
    setSources([]);
    setSnippets([]);
    try {
      const r = await fetch("/api/query", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ file, question, top_k: 8, include_snippets: true }),
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
      setSnippets(Array.isArray(data?.snippets) ? data.snippets : []);
    } catch (e) {
      setAnswer(`查詢失敗：${safeMessage(e)}`);
    } finally {
      setLoading(false);
    }
  }

  async function onLogin(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    if (!loginUser || !loginPass) return;
    setAuthError(null);
    setLoginLoading(true);
    try {
      const r = await fetch("/api/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ username: loginUser, password: loginPass }),
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
      await fetchSession();
      pushToast(`歡迎 ${loginUser}`, "ok");
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
      setFiles([]);
      setFile("");
      setQuestion("");
      setAnswer("");
      setSources([]);
      setSnippets([]);
    }
  }

  const highlightTerms = useMemo(() => {
    const raw = question.toLowerCase();
    return Array.from(
      new Set(
        raw
          .split(/[\s,，。.!?？:：;；()/\\-]+/g)
          .map((w) => w.trim())
          .filter((w) => w.length > 1)
      )
    );
  }, [question]);

  function renderSnippet(text: string) {
    if (!text) return text;
    if (!highlightTerms.length) return text;
    const escape = (s: string) => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
    const regex = new RegExp(`(${highlightTerms.map(escape).join("|")})`, "gi");
    const parts = text.split(regex);
    return parts.map((part, idx) =>
      idx % 2 === 1 ? (
        <mark key={`${part}-${idx}`} className="highlight-term">
          {part}
        </mark>
      ) : (
        <span key={`${part}-${idx}`}>{part}</span>
      )
    );
  }

  return (
    <div style={containerStyle}>
      <header style={{ textAlign: "center", marginBottom: 24 }}>
        <h1 style={{ marginBottom: 4 }}>RAG Files</h1>
        <p style={{ color: "#64748b", fontSize: 14 }}>查詢 docs/ 中的文件，並附來源</p>
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
            <label style={labelStyle}>檔案清單</label>
            <select value={file} onChange={(e) => setFile(e.target.value)} style={{ width: "100%", padding: 8 }}>
              {files.map((f) => (
                <option key={f} value={f}>
                  {f}
                </option>
              ))}
            </select>
            <div style={{ display: "flex", gap: 8, marginTop: 8 }}>
              <button onClick={refreshFiles}>重新載入清單</button>
              <button onClick={doReload} disabled={!file || reloading}>
                {reloading ? "重建中..." : "重建索引"}
              </button>
            </div>

            <div style={{ marginTop: 16 }}>
              <label style={labelStyle}>問題</label>
              <textarea
                value={question}
                onChange={(e) => setQuestion(e.target.value)}
                placeholder="例如：C1 密碼是什麼？或：請條列摘要重點"
                style={{ width: "100%", minHeight: 120, padding: 8 }}
              />
              <div style={{ display: "flex", justifyContent: "flex-end", marginTop: 8 }}>
                <button onClick={doAsk} disabled={!canAsk}>
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
            {snippets.length > 0 ? (
              <div style={{ display: "grid", gap: 12, marginTop: 12 }}>
                {snippets.map((item, idx) => (
                  <article key={`${item.source}-${idx}`} style={{ padding: 12, borderRadius: 10, background: "#f1f5f9" }}>
                    <header style={{ fontWeight: 600, marginBottom: 8 }}>{item.source}</header>
                    <p style={{ margin: 0, whiteSpace: "pre-wrap" }}>{renderSnippet(item.snippet)}</p>
                  </article>
                ))}
              </div>
            ) : (
              <ul style={{ marginTop: 12, paddingLeft: 20 }}>
                {sources.map((src) => (
                  <li key={src}>{src}</li>
                ))}
              </ul>
            )}
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
