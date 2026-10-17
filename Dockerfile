FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 DEBUG=0 BIND_HOST=0.0.0.0 PORT=8000
WORKDIR /app
COPY requirements*.txt ./
ARG REQUIREMENTS=requirements.txt
RUN pip install --no-cache-dir -r ${REQUIREMENTS}
COPY . .
# Source copied from a zip may carry root-only directories or world-writable files: normalise.
RUN chmod -R a+rX,go-w /app && SECRET_KEY=build-only-key python manage.py collectstatic --noinput && useradd --create-home appuser && mkdir -p /data /backups /app/uploads && chown -R appuser:appuser /data /backups /app/uploads /app/logs
USER appuser
EXPOSE 8000
CMD ["python", "serve.py"]
