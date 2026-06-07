FROM python:3.11-slim

RUN groupadd -r agentkit && useradd -r -g agentkit -d /app -s /sbin/nologin agentkit

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .

RUN chown -R agentkit:agentkit /app
USER agentkit

EXPOSE 8000
CMD ["uvicorn", "agent.main:app", "--host", "0.0.0.0", "--port", "8000"]
