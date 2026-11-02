FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
RUN useradd --create-home --uid 10001 lpc && chown -R lpc:lpc /app
USER lpc
ENV PYTHONUNBUFFERED=1 LPC_INSTANCE=/app/instance
EXPOSE 8000
CMD ["uvicorn","main:app","--host","0.0.0.0","--port","8000","--proxy-headers"]
