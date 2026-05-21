# 多模型协同对话后端 Dockerfile
# 采用多阶段构建:builder 阶段安装依赖,runtime 阶段拷贝产物精简体积

FROM python:3.11-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# 先拷贝 pyproject.toml 与 README,以便利用 Docker 层缓存
COPY pyproject.toml README.md ./
COPY src ./src

# 直接基于 pyproject.toml 安装,不引入 uv/poetry
RUN pip install --upgrade pip && \
    pip install .

FROM python:3.11-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# 拷贝 builder 阶段安装好的 site-packages 与可执行文件
COPY --from=builder /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

# 拷贝源码,运行期由 uvicorn 应用工厂方式启动
COPY src ./src

EXPOSE 8002

CMD ["uvicorn", "multichat.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8002"]
