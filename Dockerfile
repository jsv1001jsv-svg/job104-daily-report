# =============================================================================
# 多階段建置（multi-stage build）
#
# 為什麼要分階段？把「安裝套件」和「執行程式」拆開，最終 image 只帶執行時
# 需要的東西，體積更小、攻擊面更少。
#
#   builder    → 裝套件（含編譯工具，這些不會進最終 image）
#   dev        → 本機開發用，多裝測試工具，配合 Compose 熱重載
#   production → 正式環境用，非 root 執行，只帶必要檔案
#
# 用 `--target` 指定要建哪個階段：
#   docker build --target dev .
# docker-compose.yml 裡的 `target: dev` 就是在做這件事。
# =============================================================================

# 固定版本，不用 :latest —— latest 會隨時間變動，導致「昨天能跑今天不能跑」
FROM python:3.12-slim AS builder

WORKDIR /app

# uv 是比 pip 快很多的套件安裝器
RUN pip install --no-cache-dir uv==0.5.14

# 先只複製 requirements.txt 再安裝，是為了利用 Docker 的 layer cache：
# 只要 requirements.txt 沒變，這層就直接沿用快取，不用重裝一次所有套件。
# 如果一開始就 COPY . .，那改任何一行程式碼都會導致重裝套件。
COPY requirements.txt requirements-dev.txt ./
RUN uv pip install --system --no-cache -r requirements-dev.txt


# -----------------------------------------------------------------------------
# dev：本機開發階段（docker-compose.yml 用這個）
# -----------------------------------------------------------------------------
FROM builder AS dev

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app

# 注意：dev 階段刻意「不」COPY 程式碼進來。
# Compose 會用 volume 把本機的 /c/Project 掛載到 /app，
# 這樣你在本機改檔案，容器裡立刻生效，不用重 build。
EXPOSE 8000

CMD ["uvicorn", "src.webhook.app:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]


# -----------------------------------------------------------------------------
# production：正式環境階段
# 註：本專案正式環境走 Modal，Modal 可用 Image.from_dockerfile() 讀這個階段，
#     避免本機與雲端的環境定義漂移。
# -----------------------------------------------------------------------------
FROM python:3.12-slim AS production

WORKDIR /app

# 建立非 root 使用者。容器預設用 root 跑，一旦程式被攻破等於拿到 root。
RUN useradd -r -u 1001 appuser

# 只搬「裝好的套件」過來，builder 裡的編譯工具留在原地不進最終 image
COPY --from=builder /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

COPY --chown=appuser:appuser src/ ./src/
COPY --chown=appuser:appuser modal_app.py ./

USER appuser

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app

EXPOSE 8000

# healthcheck：Docker 定期打這個指令判斷容器是否還活著
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')" || exit 1

CMD ["uvicorn", "src.webhook.app:app", "--host", "0.0.0.0", "--port", "8000"]
