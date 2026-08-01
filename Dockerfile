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

# Playwright 需要瀏覽器本體，pip 只裝了控制它的 Python 套件。
# 預設會裝到 ~/.cache，指定固定路徑才能在 production 階段 COPY 過去。
# 這層約 1GB，是為了繞過 Cloudflare 付出的代價（見 CLAUDE.md 5.1）。
#
# 為什麼不用 `playwright install --with-deps`？
# 它寫死了 Ubuntu 的字型套件名（ttf-unifont、ttf-ubuntu-font-family），
# 在 Debian 上不存在會直接失敗。這裡改成明列 Chromium 實際需要的函式庫。
# 註：Debian 13 (trixie) 起部分套件改名帶 t64 後綴（64-bit time_t 轉換）。
ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

COPY scripts/chromium-deps.txt ./
RUN apt-get update \
    && grep -vE '^\s*(#|$)' chromium-deps.txt \
       | xargs apt-get install -y --no-install-recommends \
    && rm -rf /var/lib/apt/lists/* \
    && playwright install chromium


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

# 瀏覽器本體從 builder 直接搬過來，避免重複下載 1GB；
# 系統相依則需在本階段重裝一次（前一階段的 apt 安裝不會跟著 COPY 過來）。
ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright
COPY --from=builder /ms-playwright /ms-playwright

COPY scripts/chromium-deps.txt ./
RUN apt-get update \
    && grep -vE '^\s*(#|$)' chromium-deps.txt \
       | xargs apt-get install -y --no-install-recommends \
    && rm -rf /var/lib/apt/lists/* chromium-deps.txt

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
