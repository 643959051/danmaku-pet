# 抖音云 Docker 后端镜像（Dockerfile 位于仓库根目录）。
#
# 只安装云端依赖：不含桌面小人、音频播放和本地 TTS 依赖，也没有 Windows helper。
# 构建上下文 = 仓库根目录；下面所有 COPY 路径都相对于仓库根目录：
#   docker build -t danmaku-pet-cloud:dev .
# 密钥一律由抖音云密钥管理注入，绝不让 .env 进入镜像。

FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONIOENCODING=utf-8 \
    PIP_NO_CACHE_DIR=1 \
    APP_ENV=prod \
    CLOUD_HOST=0.0.0.0 \
    CLOUD_PORT=8000

WORKDIR /app

COPY requirements-cloud.txt ./
RUN pip install --no-cache-dir -r requirements-cloud.txt

COPY danmaku_pet/ ./danmaku_pet/
COPY data/persona.example.json ./data/persona.example.json
# 抖音云规范：根目录 run.sh 必须进入 /opt/application/run.sh 且可执行。
COPY run.sh /opt/application/run.sh
RUN chmod a+x /opt/application/run.sh

# 固定非 root 用户；/app/data 是唯一需要写入的目录（人设、运行数据）。
RUN useradd --create-home --uid 10001 appuser \
    && mkdir -p /app/data \
    && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

# 抖音云用 /v1/ping 判断存活性；Docker 自己用同一个接口做健康检查。
# 端口固定 8000（平台只允许 8000），不读 CLOUD_PORT，避免环境变量把探针带偏。
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import sys,urllib.request;sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/v1/ping',timeout=3).status==200 else 1)"

# 平台通过根目录 run.sh 启动用户服务；镜像内固定复制到 /opt/application/run.sh。
CMD ["/opt/application/run.sh"]
