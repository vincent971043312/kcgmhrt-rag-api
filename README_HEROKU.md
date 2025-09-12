在 Heroku 部署 FastAPI RAG（含 ChatGPT Actions）
==============================================

重點：Heroku 的檔案系統是暫存（dyno 重啟即清空），所以請把 `DB_DIR` 設為 `/tmp/db`；索引不會永久保存，必要時再用 `/reload` 或預先重建。若需長期持久化，建議用外部向量庫（如 Pinecone/Qdrant/Milvus）。

必要檔案（已加入）
------------------
- `Procfile`：宣告 Web 進程（gunicorn + uvicorn worker）
- `runtime.txt`：Python 版本（3.11.9）
- `requirements.txt`：已含 `fastapi/uvicorn/gunicorn` 等
- `server.py`：FastAPI 服務
- `rag.py`：RAG 核心，支援 `DOCS_DIR`、`DB_DIR` 環境變數覆寫

部署步驟
--------
1) 登入與建立 App
   - 安裝 Heroku CLI 並登入：`heroku login`
   - 在專案根目錄：`heroku create <你的-app-name>`

2) 設定環境變數（Config Vars）
   - `heroku config:set OPENAI_API_KEY=你的key -a <你的-app-name>`
   - 可選：`heroku config:set AUTH_TOKEN=你的token -a <你的-app-name>`
   - 使用暫存磁碟：`heroku config:set DB_DIR=/tmp/db -a <你的-app-name>`
   - 如需自訂檔案來源資料夾：`heroku config:set DOCS_DIR=docs -a <你的-app-name>`（讀 slug 內的 `docs/`）

3) 將程式推上 Heroku
   - `git init`（若尚未）
   - `git add . && git commit -m "Deploy to Heroku"`
   - `git push heroku HEAD:main`（或 `master`，依預設分支而定）

4) 驗證服務
   - 看 log：`heroku logs -t -a <你的-app-name>`
   - 瀏覽：`https://<你的-app-name>.herokuapp.com/health`、`/docs`、`/openapi.json`

5) 連到 ChatGPT Actions
   - 在 GPT Builder → Actions：
     - Import from URL：貼 `https://<你的-app-name>.herokuapp.com/openapi.json`
     - Root origin 會自動是 `https://<你的-app-name>.herokuapp.com`
     - Authentication：選 API Key（Bearer），填 `AUTH_TOKEN`（若有設定）

注意事項
--------
- Heroku 檔案系統不可持久化：重啟/睡眠會清空 `/tmp/db`；如要保留索引請考慮外部向量庫。
- `docs/` 會被打包進 slug，屬唯讀；如要上傳新文件，需重新部署或改為外部儲存並調整程式。
- 若 `unstructured` 在 Heroku 安裝有系統套件需求，建議：
  - 將 `.md` loader 改成 `TextLoader`；或
  - 使用 Heroku Apt buildpack + `Aptfile` 安裝必要的系統依賴。

最小變更模式
-------------
- 只要把 `DB_DIR` 設為 `/tmp/db`，你的現有流程即可在 Heroku 跑起來；索引會在第一次查詢時建立，之後 dyno 重啟需重建。

