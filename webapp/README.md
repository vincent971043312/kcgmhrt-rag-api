# kcgmhrt WebApp

這是 Vercel 上使用的前端介面，會透過 Next.js App Router 將所有請求轉發到 Heroku 的 RAG API。

## 開發方式

1. 安裝依賴

```bash
cd webapp
npm install
```

2. 建立 `.env.local`

```
RAG_API_BASE=https://kcgmhrt-acbc4e82a7da.herokuapp.com
# 若要保留 Bearer token，取消註解：
# RAG_AUTH_TOKEN=your_token
```

3. 啟動開發伺服器

```bash
npm run dev
```

前端會使用 `/api/*` proxy 將登入、查詢等請求帶著 cookie 轉發到 Heroku，因此無需在瀏覽器端直接呼叫後端網域。
