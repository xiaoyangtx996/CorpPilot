# CorpPilot Docker Image
# 企业智脑 - 多 Agent 协作系统

FROM python:3.11-slim

LABEL maintainer="CorpPilot Team"
LABEL description="Multi-Agent collaboration system with modern corporate architecture"

# 设置工作目录
WORKDIR /app

# 设置环境变量
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app

# 安装系统依赖
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# 复制项目文件
COPY scripts/ ./scripts/
COPY dashboard/ ./dashboard/
COPY agents/ ./agents/
COPY data/ ./data/
COPY docs/ ./docs/

# 创建必要的目录
RUN mkdir -p /app/data/locks

# 暴露端口
EXPOSE 7891

# 健康检查
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:7891/api/health || exit 1

# 启动命令
CMD ["python", "dashboard/server.py", "--host", "0.0.0.0", "--port", "7891"]
