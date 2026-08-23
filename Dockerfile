# MyAgent Docker 이미지 (compose에서 태그 myagent:latest)
# 호스트 환경과 분리된 고정된 Python·의존성·Playwright Chromium
FROM python:3.12-slim-bookworm

WORKDIR /app

# Docker CLI + Compose 플러그인 (/ptmquant_build 등 → 호스트 Docker 사용, 소켓 마운트 필요)
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates curl gnupg \
    && install -m 0755 -d /etc/apt/keyrings \
    && curl -fsSL https://download.docker.com/linux/debian/gpg | gpg --dearmor -o /etc/apt/keyrings/docker.gpg \
    && chmod a+r /etc/apt/keyrings/docker.gpg \
    && echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/debian bookworm stable" > /etc/apt/sources.list.d/docker.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends docker-ce-cli docker-compose-plugin git openssh-client \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt \
    && playwright install-deps chromium \
    && playwright install chromium

COPY . .

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# 호스트에서 Ollama를 쓸 때 compose에서 OLLAMA_BASE_URL 설정 권장
# 예: http://host.docker.internal:11434 (Docker Desktop Mac/Windows)
CMD ["python", "main.py"]
