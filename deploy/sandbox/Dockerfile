# 沙箱镜像：预装常用数据栈，运行期零安装（默认 --network=none）。
# 用户代码以 uid 65534 运行，非 root、无 shell 特权。
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN pip install --no-cache-dir \
      numpy==2.3.5 \
      pandas==3.0.1 \
      matplotlib==3.10.8

RUN mkdir -p /workspace && chown 65534:65534 /workspace

USER 65534:65534
WORKDIR /workspace

# 容器由 controller 以 `sleep infinity` 启动，实际命令通过 docker exec 下发
CMD ["sleep", "infinity"]
