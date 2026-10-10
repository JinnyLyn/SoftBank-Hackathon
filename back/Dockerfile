FROM python:3.13.16-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app
COPY requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt \
    && useradd --system --create-home --uid 10001 appuser

COPY app ./app
COPY migrations ./migrations
RUN mkdir -p /var/lib/paved-clouds/uploads /var/lib/paved-clouds/plans \
    && chown -R appuser:appuser /app /var/lib/paved-clouds

USER appuser
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
