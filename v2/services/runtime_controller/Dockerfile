FROM python:3.11-slim
RUN pip install --no-cache-dir 'docker>=7.1,<8' 'fastapi>=0.115,<1' 'uvicorn>=0.30,<1'
COPY services/runtime_controller/app.py /app/app.py
COPY services/common/secret_entrypoint.py /app/secret_entrypoint.py
WORKDIR /app
ENTRYPOINT ["python", "/app/secret_entrypoint.py", "python", "/app/app.py"]
