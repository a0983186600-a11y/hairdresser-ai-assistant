# 示範用的映像檔。`docker-compose.demo.yml` 的 `build: .` 指的就是它。
#
# 刻意很小：沒有資料庫客戶端、沒有瀏覽器、沒有排程器。這個助理只讀套件內的
# 假資料然後回答問題，多裝的每一樣東西都只是多一件會壞的事。

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# 先裝相依：assistant/ 改動時不必重跑 pip。
COPY pyproject.toml README.md LICENSE ./
COPY assistant ./assistant
RUN pip install --no-cache-dir '.[dev]'

# 測試也一起放進去：評審可以直接 `docker compose run --rm assistant pytest -q`。
# 放在安裝之後，套件探索才不會把 tests/ 當成第二個頂層套件。
COPY tests ./tests

EXPOSE 8100

CMD ["uvicorn", "assistant.server:app", "--host", "0.0.0.0", "--port", "8100"]
