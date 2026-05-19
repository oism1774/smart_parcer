# Пошаговое руководство: с шага 4 (parser.py) и дальше

> **Для кого:** вы умеете Bash и Docker, Python почти не знаете.  
> **Что делать:** читать сверху вниз. Код `parser.py` — в [разделе 11](#11-полный-код-parserpy--копировать-целиком): скопировать в файл `parser.py` одним блоком.  
> **Что не трогать без нужды:** `Dockerfile`, `requirements.txt` (есть одна правка в Dockerfile — [раздел 14](#14-правка-в-вашем-dockerfile)).

Связанный план: [company_site_parser_652a7352.plan.md](.cursor/plans/company_site_parser_652a7352.plan.md)

---

## Оглавление

1. [Что такое parser.py в одной фразе](#1-что-такое-parserpy-в-одной-фразе)
2. [Мини-словарь Python](#2-мини-словарь-python)
3. [Структура файла — карта](#3-структура-файла--карта)
4. [Блок 1: импорты и константы](#4-блок-1-импорты-и-константы)
5. [Блок 2: модель CompanyData](#5-блок-2-модель-companydata)
6. [Блок 3: чтение sites.txt и resume](#6-блок-3-чтение-sitestxt-и-resume)
7. [Блок 4: выбор страниц contacts/about](#7-блок-4-выбор-страниц-contactsabout)
8. [Блок 5: скачивание страницы в Markdown](#8-блок-5-скачивание-страницы-в-markdown)
9. [Блок 6: LLM и обработка одного сайта](#9-блок-6-llm-и-обработка-одного-сайта)
10. [Блок 7: главная функция main](#10-блок-7-главная-функция-main)
11. [Полный код parser.py — копировать целиком](#11-полный-код-parserpy--копировать-целиком)
12. [Шаг 5: сборка Docker](#12-шаг-5-сборка-docker)
13. [Шаг 6: запуск и проверка](#13-шаг-6-запуск-и-проверка)
14. [Правка в вашем Dockerfile](#14-правка-в-вашем-dockerfile)
15. [Частые ошибки](#15-частые-ошибки)
16. [Чеклист](#16-чеклист)

---

## 1. Что такое parser.py в одной фразе

`parser.py` — это **главный скрипт-дирижёр**, как Bash, только на Python:

1. Читает URL из `sites.txt`.
2. Для каждого URL открывает сайт в Chrome (внутри Docker).
3. Собирает текст 1–3 страниц (главная + до 2 страниц контактов/о компании).
4. Отправляет текст в OpenAI (`gpt-4o-mini`).
5. Получает JSON с 30 полями.
6. Дописывает одну строку в `output.jsonl`.
7. В конце собирает `output.json`.

Если один сайт упал — скрипт **не останавливается**, пишет `error` и идёт дальше.

```text
sites.txt  →  parser.py  →  output.jsonl  →  output.json
                  │
                  ├── Chromium (Crawl4AI)
                  └── OpenAI API
```

---

## 2. Мини-словарь Python

| Синтаксис | Что это | Аналогия в Bash |
|-----------|---------|-----------------|
| `import os` | Подключить библиотеку | внешняя утилита |
| `def имя():` | Функция | `имя() { ... }` |
| `async def` | Функция, которая ждёт сеть/браузер | — |
| `await` | «Подожди, пока закончится» | — |
| `class X(BaseModel):` | Схема полей JSON | шаблон записи |
| `Optional[str]` | Строка или `null` | поле может быть пустым |
| `list[str]` | Массив строк | массив в bash |
| `try / except` | Поймать ошибку | `if ! cmd; then ...` |
| `for x in items:` | Цикл | `for x in ...; do` |
| `if __name__ == "__main__":` | Запуск при `python parser.py` | точка входа скрипта |

**Отступы обязательны** — 4 пробела на уровень. Табы не используйте.

---

## 3. Структура файла — карта

```text
parser.py
├── импорты + константы (пути, таймауты)
├── class CompanyData          ← 30 полей + служебные
├── load_urls()                ← читает sites.txt
├── load_done_urls()           ← resume из output.jsonl
├── append_jsonl()             ← дописать строку
├── build_output_json()        ← jsonl → output.json
├── pick_extra_pages()         ← /contacts, /about
├── get_markdown_from_result() ← текст из ответа Crawl4AI
├── extract_internal_links()   ← ссылки с главной
├── fetch_page()               ← открыть 1 URL
├── LLM_INSTRUCTION            ← текст для OpenAI
├── create_llm_strategy()      ← настройка gpt-4o-mini
├── parse_llm_result()         ← JSON → dict
├── process_site()             ← весь сайт
├── main_async() + main()      ← цикл по 100 сайтам
└── if __name__ == "__main__"
```

Можно **собирать по блокам** (разделы 4–10) или **скопировать целиком** из раздела 11.

---

## 4. Блок 1: импорты и константы

**Зачем:** подключить библиотеки и задать пути. В Docker рабочая папка — `/app` (это ваша папка на хосте при `-v "$(pwd):/app"`).

| Константа | Значение | Смысл |
|-----------|----------|--------|
| `SITES_FILE` | `/app/sites.txt` | Список URL |
| `OUTPUT_JSONL` | `/app/output.jsonl` | Результат построчно |
| `OUTPUT_JSON` | `/app/output.json` | Итоговый файл |
| `DELAY_MIN/MAX` | 2.0 / 3.0 | Пауза между сайтами (сек) |
| `PAGE_TIMEOUT_MS` | 90000 | 90 сек на страницу |
| `MAX_EXTRA_PAGES` | 2 | Доп. страницы после главной |

---

## 5. Блок 2: модель CompanyData

**Зачем:** «форма CRM» из 30 полей. Pydantic проверяет типы после ответа ИИ.

**Служебные поля** (заполняет скрипт, не LLM):

| Поле | Смысл |
|------|--------|
| `source_url` | URL из sites.txt |
| `scraped_at` | Время обработки (UTC) |
| `pages_crawled` | Какие URL реально открыли |
| `error` | Текст ошибки, если сайт упал |

**30 бизнес-полей** — маппинг на ваш список:

| # | Ваш вопрос | Имя в JSON |
|---|------------|------------|
| 1 | Название компании | `company_name` |
| 2 | Основной телефон | `primary_phone` |
| 3 | Доп. телефон | `secondary_phone` |
| 4 | Основной email | `primary_email` |
| 5 | Email продаж | `sales_email` |
| 6 | Физический адрес | `physical_address` |
| 7 | Юридический адрес | `legal_address` |
| 8 | Услуги (массив) | `services` |
| 9 | Ссылка на контакты | `contacts_page_url` |
| 10 | Telegram | `social_telegram` |
| 11 | WhatsApp | `social_whatsapp` |
| 12 | VK | `social_vk` |
| 13 | Другие соцсети | `social_other` |
| 14 | Краткое описание | `short_description` |
| 15 | Часы работы | `working_hours` |
| 16 | Форма обратной связи | `has_contact_form` |
| 17 | ИНН/ОГРН/БИН | `legal_ids` |
| 18 | Язык сайта | `site_language` |
| 19 | Год/копирайт | `founded_year_or_copyright` |
| 20 | Блог/новости | `has_blog` |
| 21 | Клиенты/партнёры | `key_clients` |
| 22 | Есть прайс | `has_pricing` |
| 23 | Ссылка на прайс | `pricing_url` |
| 24 | Способы оплаты | `payment_methods` |
| 25 | Отзывы на сайте | `has_reviews` |
| 26 | География | `geography` |
| 27 | Слоган | `tagline` |
| 28 | Фаундеры | `founders` |
| 29 | Вакансии | `has_vacancies` |
| 30 | Тех. стек | `tech_stack` |

В `Field(description="...")` — **русский текст** для подсказки модели.  
`Optional[...] = None` → в JSON будет `null`, если данных нет.

---

## 6. Блок 3: чтение sites.txt и resume

| Функция | Что делает |
|---------|------------|
| `load_urls` | Читает `sites.txt`, пропускает пустые строки и `#` |
| `load_done_urls` | Читает `output.jsonl`, собирает уже обработанные `source_url` |
| `append_jsonl` | Дописывает **одну строку** JSON (формат JSONL) |
| `build_output_json` | В конце собирает массив в `output.json` |

**Resume:** если прогон оборвался на 50-м сайте — при повторном `docker run` первые 49 URL будут пропущены.

Чтобы начать с нуля — удалите или переименуйте `output.jsonl`.

---

## 7. Блок 4: выбор страниц contacts/about

После главной страницы скрипт смотрит **внутренние ссылки** и ищет в URL слова:

- контакты: `contact`, `contacts`, `kontakt`, `kontakty`, …
- о компании: `about`, `o-nas`, `o-kompanii`, `requisites`, …

Берёт до **2 ссылок** на **том же домене**, не дублируя главную.

Зачем: телефон и email часто только на `/contacts`, а не на главной.

---

## 8. Блок 5: скачивание страницы в Markdown

**Фаза без OpenAI** — только браузер:

1. `crawler.arun(url=..., config=crawl_config)` — открыть страницу.
2. Из ответа взять `fit_markdown` или `raw_markdown` — это текст для ИИ.
3. Собрать список внутренних ссылок для шага 7.

Настройки краулера (в `main`):

- `remove_consent_popups=True` — закрыть cookie-баннеры.
- `scan_full_page=True` — прокрутка для подгрузки контента.
- `page_timeout=90000` — 90 секунд на медленные сайты.

---

## 9. Блок 6: LLM и обработка одного сайта

**Порядок для одного URL:**

```text
1. Открыть главную        → markdown_1 + ссылки
2. Открыть до 2 доп. URL  → markdown_2, markdown_3
3. llm_strategy.run(url, [markdown_1, markdown_2, ...])
4. Pydantic: CompanyData.model_validate(data)
5. append_jsonl(...)
```

**Почему `run`, а не LLM при каждом `arun`:**  
один запрос к OpenAI на весь сайт — дешевле и полнее, чем три отдельных.

**Инструкция для модели** (`LLM_INSTRUCTION`):  
«если данных нет — `null`, не выдумывай».

При ошибке сайта в `output.jsonl` попадёт запись с заполненными `source_url`, `scraped_at`, `error` и остальными полями `null`.

---

## 10. Блок 7: главная функция main

```text
main()
  └── asyncio.run(main_async())
        ├── проверить OPENAI_API_KEY
        ├── load_urls + load_done_urls
        ├── открыть AsyncWebCrawler (один браузер на весь прогон)
        ├── for url in pending:
        │     try: process_site → append_jsonl
        │     except: запись с error → append_jsonl
        │     sleep 2–3 сек
        └── build_output_json()
```

Логи идут в консоль → видны через `docker run` (благодаря `PYTHONUNBUFFERED=1` в Dockerfile).

---

## 11. Полный код parser.py — копировать целиком

**Ваши действия:**

1. Откройте файл `parser.py` в редакторе (сейчас он пустой или почти пустой).
2. Выделите всё содержимое и удалите.
3. Скопируйте **весь** блок ниже (от `#!/usr/bin` до `main()`).
4. Вставьте в `parser.py` и сохраните.

```python
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Парсер сайтов клиентов: Crawl4AI + OpenAI gpt-4o-mini.
Читает sites.txt, пишет output.jsonl и output.json.
"""

import asyncio
import json
import logging
import os
import random
import sys
from datetime import datetime, timezone
from typing import Any, Optional
from urllib.parse import urljoin, urlparse

from dotenv import load_dotenv
from pydantic import BaseModel, Field

from crawl4ai import (
    AsyncWebCrawler,
    BrowserConfig,
    CacheMode,
    CrawlerRunConfig,
    LLMConfig,
    LLMExtractionStrategy,
)

# --- Пути (в контейнере /app = ваша папка на хосте при -v) ---
SITES_FILE = "/app/sites.txt"
OUTPUT_JSONL = "/app/output.jsonl"
OUTPUT_JSON = "/app/output.json"

# --- Поведение ---
DELAY_MIN_SEC = 2.0
DELAY_MAX_SEC = 3.0
PAGE_TIMEOUT_MS = 90_000
MAX_EXTRA_PAGES = 2

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger(__name__)

# --- Ключевые слова в URL для доп. страниц ---
CONTACT_KEYWORDS = (
    "contact", "contacts", "kontakt", "kontakty", "контакт",
    "svyaz", "feedback", "connect",
)
ABOUT_KEYWORDS = (
    "about", "o-nas", "o-kompanii", "about-us", "company",
    "requisites", "rekvizit", "legal", "info",
)

LLM_INSTRUCTION = """
Ты извлекаешь данные о компании из текста веб-страниц (markdown).
Верни ОДИН JSON-объект по схеме.
Правила:
- Если данных на сайте нет — ставь null. Не выдумывай.
- Телефоны и email — как на сайте (с кодом страны если есть).
- services, key_clients, payment_methods, founders, social_other — массивы строк или null.
- has_* поля — true, false или null (null если неясно).
- tech_stack — только при явных признаках (WordPress, Tilda, Wix, Bitrix и т.д.).
- Не заполняй поля source_url, scraped_at, pages_crawled, error — их добавит скрипт.
"""


class CompanyData(BaseModel):
    """Структура данных по одной компании."""

    source_url: str = Field(..., description="Исходный URL из sites.txt")
    scraped_at: str = Field(..., description="Время обработки ISO UTC")
    pages_crawled: list[str] = Field(default_factory=list, description="Какие URL открыли")
    error: Optional[str] = Field(None, description="Текст ошибки")

    company_name: Optional[str] = Field(None, description="Название компании")
    primary_phone: Optional[str] = Field(None, description="Основной телефон")
    secondary_phone: Optional[str] = Field(None, description="Доп. телефон продаж/поддержки")
    primary_email: Optional[str] = Field(None, description="Основной email")
    sales_email: Optional[str] = Field(None, description="Email отдела продаж")
    physical_address: Optional[str] = Field(None, description="Физический адрес")
    legal_address: Optional[str] = Field(None, description="Юридический адрес")
    services: Optional[list[str]] = Field(None, description="Услуги / сфера деятельности")
    contacts_page_url: Optional[str] = Field(None, description="Ссылка на страницу контактов")
    social_telegram: Optional[str] = Field(None, description="Telegram")
    social_whatsapp: Optional[str] = Field(None, description="WhatsApp")
    social_vk: Optional[str] = Field(None, description="VK")
    social_other: Optional[list[str]] = Field(None, description="YouTube, Instagram и др.")
    short_description: Optional[str] = Field(None, description="Краткое описание в одном предложении")
    working_hours: Optional[str] = Field(None, description="Часы работы")
    has_contact_form: Optional[bool] = Field(None, description="Есть форма обратной связи")
    legal_ids: Optional[str] = Field(None, description="ИНН, ОГРН, БИН")
    site_language: Optional[str] = Field(None, description="Язык интерфейса")
    founded_year_or_copyright: Optional[str] = Field(None, description="Год основания или ©")
    has_blog: Optional[bool] = Field(None, description="Есть блог или новости")
    key_clients: Optional[list[str]] = Field(None, description="Клиенты или партнёры")
    has_pricing: Optional[bool] = Field(None, description="Есть прайс/цены")
    pricing_url: Optional[str] = Field(None, description="Ссылка на прайс или PDF")
    payment_methods: Optional[list[str]] = Field(None, description="Способы оплаты")
    has_reviews: Optional[bool] = Field(None, description="Есть отзывы на сайте")
    geography: Optional[str] = Field(None, description="География работы")
    tagline: Optional[str] = Field(None, description="Главный слоган")
    founders: Optional[list[str]] = Field(None, description="Фаундеры или ключевые лица")
    has_vacancies: Optional[bool] = Field(None, description="Есть вакансии")
    tech_stack: Optional[str] = Field(None, description="Тех. стек сайта")


def load_urls(path: str) -> list[str]:
    if not os.path.isfile(path):
        log.error("Файл не найден: %s", path)
        return []
    urls: list[str] = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            urls.append(line)
    return urls


def load_done_urls(jsonl_path: str) -> set[str]:
    done: set[str] = set()
    if not os.path.isfile(jsonl_path):
        return done
    with open(jsonl_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
                if row.get("source_url"):
                    done.add(row["source_url"])
            except json.JSONDecodeError:
                continue
    return done


def append_jsonl(path: str, record: dict) -> None:
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def build_output_json(jsonl_path: str, json_path: str) -> None:
    rows: list[dict] = []
    if os.path.isfile(jsonl_path):
        with open(jsonl_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    rows.append(json.loads(line))
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False, indent=2)
    log.info("Записано %d записей в %s", len(rows), json_path)


def same_domain(url_a: str, url_b: str) -> bool:
    return urlparse(url_a).netloc.lower() == urlparse(url_b).netloc.lower()


def pick_extra_pages(home_url: str, links: list[str], max_pages: int = MAX_EXTRA_PAGES) -> list[str]:
    if not links:
        return []
    home_parsed = urlparse(home_url)
    home_normalized = f"{home_parsed.scheme}://{home_parsed.netloc}{home_parsed.path}".rstrip("/")
    candidates: list[tuple[int, str]] = []

    for href in links:
        if not href or href.startswith(("#", "mailto:", "tel:", "javascript:")):
            continue
        full = urljoin(home_url, href)
        parsed = urlparse(full)
        if parsed.scheme not in ("http", "https"):
            continue
        if not same_domain(home_url, full):
            continue
        path_lower = (parsed.path or "").lower()
        score = 0
        for kw in CONTACT_KEYWORDS:
            if kw in path_lower:
                score += 10
        for kw in ABOUT_KEYWORDS:
            if kw in path_lower:
                score += 5
        if score > 0:
            clean = f"{parsed.scheme}://{parsed.netloc}{parsed.path}".rstrip("/")
            if clean == home_normalized:
                continue
            candidates.append((score, clean))

    seen: set[str] = set()
    result: list[str] = []
    for _, url in sorted(candidates, key=lambda x: -x[0]):
        if url in seen:
            continue
        seen.add(url)
        result.append(url)
        if len(result) >= max_pages:
            break
    return result


def get_markdown_from_result(result) -> str:
    md = getattr(result, "markdown", None)
    if md is None:
        return ""
    if hasattr(md, "fit_markdown") and md.fit_markdown:
        return md.fit_markdown
    if hasattr(md, "raw_markdown") and md.raw_markdown:
        return md.raw_markdown
    if isinstance(md, str):
        return md
    return str(md)


def extract_internal_links(result) -> list[str]:
    links: list[str] = []
    raw_links = getattr(result, "links", None)
    if not raw_links:
        return links
    internal = raw_links.get("internal", []) if isinstance(raw_links, dict) else []
    for item in internal:
        if isinstance(item, dict):
            href = item.get("href") or item.get("url")
        else:
            href = getattr(item, "href", None) or getattr(item, "url", None)
        if href:
            links.append(href)
    return links


async def fetch_page(crawler: AsyncWebCrawler, url: str, crawl_config: CrawlerRunConfig):
    log.info("  Краул: %s", url)
    result = await crawler.arun(url=url, config=crawl_config)
    if not result.success:
        raise RuntimeError(result.error_message or "crawl failed")
    markdown = get_markdown_from_result(result)
    if not markdown.strip():
        raise RuntimeError("пустой markdown")
    return result, markdown, extract_internal_links(result)


def create_llm_strategy() -> LLMExtractionStrategy:
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        log.error("Нет OPENAI_API_KEY. Создайте .env или docker run --env-file .env")
        sys.exit(1)
    return LLMExtractionStrategy(
        llm_config=LLMConfig(
            provider="openai/gpt-4o-mini",
            api_token=api_key,
        ),
        schema=CompanyData.model_json_schema(),
        extraction_type="schema",
        instruction=LLM_INSTRUCTION,
        input_format="markdown",
        apply_chunking=True,
        chunk_token_threshold=8000,
        overlap_rate=0.1,
        extra_args={"temperature": 0.0, "max_tokens": 2500},
        verbose=True,
    )


def parse_llm_result(raw_list: list, source_url: str, pages_crawled: list[str]) -> dict:
    if not raw_list:
        raise ValueError("пустой ответ LLM")
    chunk = raw_list[0]
    if isinstance(chunk, str):
        data = json.loads(chunk)
    elif isinstance(chunk, dict):
        data = chunk
    else:
        data = json.loads(str(chunk))
    if isinstance(data, list):
        data = data[0] if data else {}
    data["source_url"] = source_url
    data["scraped_at"] = datetime.now(timezone.utc).isoformat()
    data["pages_crawled"] = pages_crawled
    data["error"] = None
    return data


async def process_site(
    crawler: AsyncWebCrawler,
    crawl_config: CrawlerRunConfig,
    llm_strategy: LLMExtractionStrategy,
    url: str,
) -> CompanyData:
    pages_crawled: list[str] = []
    md_sections: list[str] = []

    _, md_home, internal_links = await fetch_page(crawler, url, crawl_config)
    pages_crawled.append(url)
    md_sections.append(f"# URL: {url}\n\n{md_home}")

    for extra_url in pick_extra_pages(url, internal_links, MAX_EXTRA_PAGES):
        try:
            _, md_extra, _ = await fetch_page(crawler, extra_url, crawl_config)
            pages_crawled.append(extra_url)
            md_sections.append(f"# URL: {extra_url}\n\n{md_extra}")
        except Exception as e:
            log.warning("  Доп. страница пропущена %s: %s", extra_url, e)

    log.info("  LLM: извлечение полей...")
    raw_list = llm_strategy.run(url, md_sections)
    data = parse_llm_result(raw_list, url, pages_crawled)
    return CompanyData.model_validate(data)


async def main_async() -> None:
    load_dotenv()

    urls = load_urls(SITES_FILE)
    if not urls:
        log.error("Нет URL в %s", SITES_FILE)
        sys.exit(1)

    done = load_done_urls(OUTPUT_JSONL)
    pending = [u for u in urls if u not in done]
    log.info("Всего URL: %d, уже готово: %d, осталось: %d", len(urls), len(done), len(pending))

    if not pending:
        log.info("Нечего обрабатывать.")
        build_output_json(OUTPUT_JSONL, OUTPUT_JSON)
        return

    llm_strategy = create_llm_strategy()
    browser_cfg = BrowserConfig(headless=True, verbose=False)
    crawl_config = CrawlerRunConfig(
        cache_mode=CacheMode.BYPASS,
        page_timeout=PAGE_TIMEOUT_MS,
        wait_until="domcontentloaded",
        remove_consent_popups=True,
        scan_full_page=True,
        max_scroll_steps=5,
    )

    async with AsyncWebCrawler(config=browser_cfg) as crawler:
        for i, url in enumerate(pending, start=1):
            log.info("[%d/%d] Сайт: %s", i, len(pending), url)
            try:
                record = await process_site(crawler, crawl_config, llm_strategy, url)
                row = record.model_dump()
            except Exception as e:
                log.error("  ОШИБКА: %s", e)
                row = CompanyData(
                    source_url=url,
                    scraped_at=datetime.now(timezone.utc).isoformat(),
                    pages_crawled=[],
                    error=str(e),
                ).model_dump()

            append_jsonl(OUTPUT_JSONL, row)

            if i < len(pending):
                delay = random.uniform(DELAY_MIN_SEC, DELAY_MAX_SEC)
                log.info("  Пауза %.1f сек...", delay)
                await asyncio.sleep(delay)

    build_output_json(OUTPUT_JSONL, OUTPUT_JSON)
    try:
        llm_strategy.show_usage()
    except Exception:
        pass
    log.info("Готово.")


def main() -> None:
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
```

### Проверка синтаксиса (на хосте, без Docker)

```bash
cd "/home/moonlever/ AI/Code Parser"
python3 -m py_compile parser.py && echo "OK: синтаксис верный"
```

Ошибка `No module named crawl4ai` при этом **нормальна** на хосте — Crawl4AI есть только в контейнере.

---

## 12. Шаг 5: сборка Docker

Перед сборкой исправьте `Dockerfile` ([раздел 14](#14-правка-в-вашем-dockerfile)).

```bash
cd "/home/moonlever/ AI/Code Parser"
docker build -t company-site-parser:latest .
```

Успех: в конце строка `Successfully tagged company-site-parser:latest`.

```bash
docker images | grep company-site-parser
```

---

## 13. Шаг 6: запуск и проверка

### 13.1. Файлы в папке проекта

```text
.env              # OPENAI_API_KEY=sk-...
sites.txt         # один URL на строку
parser.py         # код из раздела 11
Dockerfile
requirements.txt
```

Пример `sites.txt` для первого теста:

```text
https://example.com
```

Создание `.env` (ключ подставьте свой):

```bash
cat > .env <<'EOF'
OPENAI_API_KEY=sk-ВАШ_КЛЮЧ
EOF
chmod 600 .env
```

### 13.2. Запуск

```bash
cd "/home/moonlever/ AI/Code Parser"

docker run --rm \
  --name company-parser-run \
  --shm-size=2g \
  --env-file .env \
  -v "$(pwd):/app" \
  company-site-parser:latest
```

| Флаг | Зачем |
|------|--------|
| `--rm` | Удалить контейнер после работы |
| `--shm-size=2g` | Память для Chrome |
| `--env-file .env` | Ключ OpenAI |
| `-v "$(pwd):/app"` | `sites.txt` и `output.*` на вашем диске |

### 13.3. Проверка результата

```bash
wc -l output.jsonl
tail -n 1 output.jsonl | python3 -m json.tool
```

В логах должны быть строки вида `[1/1] Сайт: https://...` и `Краул:`, `LLM:`.

### 13.4. Долгий прогон (~100 сайтов)

```bash
screen -S parser
# внутри screen — команда docker run ...
# Ctrl+A, затем D — отсоединиться
# screen -r parser — вернуться к логам
```

---

## 14. Правка в вашем Dockerfile

Сейчас у вас:

```dockerfile
CMD ["python", ["parser.py"]]
```

**Неправильно** — второй аргумент не должен быть вложенным списком.

**Исправьте на:**

```dockerfile
CMD ["python", "parser.py"]
```

Сохраните файл и только потом делайте `docker build`.

---

## 15. Частые ошибки

| Симптом | Что проверить |
|---------|----------------|
| Контейнер сразу выходит | `CMD` в Dockerfile; `docker logs` |
| `No module named crawl4ai` | Запуск на хосте без Docker — нужен `docker run` |
| `Нет OPENAI_API_KEY` | Файл `.env`, флаг `--env-file .env` |
| `Файл не найден sites.txt` | Файл в папке проекта + volume `-v` |
| Browser crash | `--shm-size=3g` |
| `IndentationError` | Только 4 пробела, без табов |
| Пустые поля в JSON | На сайте нет данных или нет `/contacts` |
| Дорого по OpenAI | Сначала тест на 1–2 URL |

---

## 16. Чеклист

- [ ] Скопирован полный код в `parser.py` (раздел 11)
- [ ] Исправлен `CMD` в `Dockerfile`
- [ ] Созданы `.env` и `sites.txt` (1–2 URL для теста)
- [ ] `docker build` без ошибок
- [ ] `docker run` создал `output.jsonl`
- [ ] В логах нет падения всего скрипта на одном плохом сайте
- [ ] Готов полный `sites.txt` для прогона ~100 сайтов

---

## Что дальше

1. Тест на 1–2 сайтах.
2. Полный список в `sites.txt`.
3. Запуск в `screen`/`tmux`.
4. Импорт `output.json` в таблицу или CRM.

Если нужно, чтобы агент **сам создал** `parser.py` из этого руководства — напишите в Agent mode: «Создай parser.py из GUIDE_STEP4_parser.md».
