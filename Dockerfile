FROM unclecode/crawl4ai:latest

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY parser.py .

ENV PYTHONUNBUFFERED=1

CMD ["python", "parser.py"]