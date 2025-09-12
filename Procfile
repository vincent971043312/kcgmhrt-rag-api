web: gunicorn -k uvicorn.workers.UvicornWorker -w 2 server:app --bind 0.0.0.0:$PORT --timeout 600
