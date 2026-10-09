# StemScore を Render などのクラウドで動かすためのコンテナ
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    TORCH_HOME=/opt/torch \
    STEMSCORE_CLOUD=1 \
    STEMSCORE_DATA=/var/data

WORKDIR /srv/stemscore

# ライブラリ (PyTorch は GPU なしの CPU 版)
COPY requirements.txt .
RUN pip install -r requirements.txt \
 && pip install --no-deps basic-pitch==0.4.0

# 分離モデル (4パート標準・6パート) をイメージに含めて、起動後のダウンロードを省く。
# 高品質モデル (htdemucs_ft) は初めて使うときにダウンロードされる
RUN python -c "from demucs.pretrained import get_model; get_model('htdemucs'); get_model('htdemucs_6s')"

COPY app ./app

# Render は PORT 環境変数で待ち受けポートを指定する。処理キューを共有するため worker は 1 つ
CMD ["sh", "-c", "uvicorn app.server:app --host 0.0.0.0 --port ${PORT:-10000} --workers 1 --log-level warning"]
