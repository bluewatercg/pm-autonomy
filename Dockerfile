# 部署者自行构建、核实镜像后使用固定 image ID；本候选未执行构建。
FROM python:3.13-slim
RUN apt-get update && apt-get install -y --no-install-recommends git && rm -rf /var/lib/apt/lists/*
ENTRYPOINT ["/usr/local/bin/python3"]
