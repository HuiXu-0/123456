FROM python:3.12-slim

WORKDIR /app

# 把 Debian 官方源换成阿里云镜像（国内访问快且稳定）
RUN sed -i 's|deb.debian.org|mirrors.aliyun.com|g' /etc/apt/sources.list.d/debian.sources 2>/dev/null; \
    sed -i 's|deb.debian.org|mirrors.aliyun.com|g' /etc/apt/sources.list 2>/dev/null; \
    apt-get update && \
    apt-get install -y --no-install-recommends build-essential && \
    rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
# CPU 版 torch（避免下载几个 GB 的 CUDA 包）
RUN pip install --no-cache-dir --timeout 180 --retries 10 torch --index-url https://download.pytorch.org/whl/cpu
# 其余依赖走清华镜像
RUN pip install --no-cache-dir -i https://pypi.tuna.tsinghua.edu.cn/simple --trusted-host pypi.tuna.tsinghua.edu.cn -r requirements.txt

COPY app/ app/
COPY scripts/ scripts/
COPY seed_docs/ seed_docs/
COPY seed_kb.py .
COPY scripts/ scripts/
COPY seed_docs/ seed_docs/
COPY seed_kb.py .

RUN mkdir -p data/chroma data/models data/tts_cache data/video

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]