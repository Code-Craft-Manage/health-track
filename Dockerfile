FROM python:3.13-slim

WORKDIR /app

# Install dependencies first so this layer is cached across code changes.
COPY requirements.txt /app/
RUN pip install --no-cache-dir -r requirements.txt

# Copy the application code.
COPY . /app

ENV PYTHONUNBUFFERED=1

CMD ["python", "bot.py"]
