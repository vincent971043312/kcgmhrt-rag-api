web: gunicorn -k uvicorn.workers.UvicornWorker -w 1 server:app --bind 0.0.0.0:$PORT --timeout 600
