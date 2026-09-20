FROM python@sha256:a116514e19457bcb7af7efe9c3dd0b9b71e85b317694e7882a1c52aa15a78134
RUN apt-get update && apt-get install -y --no-install-recommends \
    git=1:2.39.5-0+deb12u3 ripgrep=13.0.0-4+b2 \
    && rm -r /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app.py auth.py graph_access.py inbound.py listing.py ./
ENV PYTHONUNBUFFERED=1
ENTRYPOINT ["python", "/app/app.py"]
CMD ["serve", "--data", "/data"]
