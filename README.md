To use: 
Create .env with:
OPENAI_API_KEY=*your key*

sites.txt: 
*sites list*

docker pull unclecode/crawl4ai:latest
docker build -t company-site-parser:latest .
docker run --rm --shm-size=2g --env-file .env -v "$(pwd):/app" company-site-parser:latest
