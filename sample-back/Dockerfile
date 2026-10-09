FROM python:3.12-slim

WORKDIR /app

COPY sample-back/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY sample-back /app/backend
COPY sample-front /app/sample-front

EXPOSE 8000

CMD ["uvicorn", "backend.app.main:app", "--host", "0.0.0.0", "--port", "8000"]
