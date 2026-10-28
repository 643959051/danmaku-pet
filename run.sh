#!/bin/sh
# 抖音云启动脚本。
#
# 平台要求：服务根目录必须有 run.sh；自定义镜像需要把它拷贝到
# /opt/application/run.sh，并保持可执行权限（Dockerfile 里会 chmod a+x）。
# 平台只允许服务监听 8000，因此这里固定 --port 8000，不从环境变量改端口。
set -eu

if [ -d /app/danmaku_pet ]; then
    # 自定义镜像：代码在 /app。
    cd /app
else
    # 模板部署：run.sh 所在目录就是服务根目录。
    SCRIPT_DIR=$(CDPATH= cd "$(dirname "$0")" && pwd)
    cd "$SCRIPT_DIR"
fi

exec python -m danmaku_pet.cloud --host 0.0.0.0 --port 8000
