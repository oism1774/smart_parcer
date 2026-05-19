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

from crawl4ai import AsyncWebCrawler, CacheMode, LLMExtractionStrategy
from crawl4ai.async_configs import BrowserConfig, CrawlerRunConfig, LLMConfig

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


def load_env_safe() -> None:
    """
    Подгрузить .env для локального запуска.
    В Docker ключ уже приходит через: docker run --env-file .env
    (файл .env с chmod 600 внутри контейнера часто недоступен — это нормально).
    """
    if os.getenv("OPENAI_API_KEY"):
        return
    for env_path in ("/app/.env", ".env"):
        if os.path.isfile(env_path) and os.access(env_path, os.R_OK):
            load_dotenv(env_path)
            return
    try:
        load_dotenv()
    except PermissionError:
        log.warning(
            "Не удалось прочитать .env (права доступа). "
            "В Docker передайте ключ: docker run --env-file .env ..."
        )


# --- Ключевые слова в URL для доп. страниц ---
CONTACT_KEYWORDS = (
    "contact", "contacts", "kontakt", "kontakty", "контакты",
    "svyaz", "feedback", "connect", "связаться", "свяжитесь", "связаться с нами", "свяжитесь с нами",
)
ABOUT_KEYWORDS = (
    "about", "o-nas", "o-kompanii", "about-us", "company",
    "requisites", "о нас", "о компании", "legal", "info",
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
    """Читает sites.txt и возвращает список URL."""
    if not os.path.isfile(path):  # проверяем, существует ли файл по пути path
        log.error("Файл не найден: %s", path)  # пишем ошибку в лог
        return []  # возвращаем пустой список — дальше main остановится
    urls: list[str] = []  # сюда соберём все URL из файла
    with open(path, "r", encoding="utf-8") as f:  # открываем файл на чтение (кириллица в комментариях ок)
        for line in f:  # читаем построчно, как while read line в bash
            line = line.strip()  # убираем пробелы и перевод строки с краёв
            if not line or line.startswith("#"):  # пустая строка или комментарий — пропуск
                continue  # переходим к следующей строке
            urls.append(line)  # валидная строка — добавляем URL в список
    return urls  # отдаём список вызывающему коду


def load_done_urls(jsonl_path: str) -> set[str]:
    """Какие сайты уже обработаны (для resume после сбоя)."""
    done: set[str] = set()  # множество URL без дублей
    if not os.path.isfile(jsonl_path):  # если output.jsonl ещё нет — никто не готов
        return done  # пустое множество
    with open(jsonl_path, "r", encoding="utf-8") as f:  # открываем jsonl построчно
        for line in f:
            line = line.strip()  # убираем \n
            if not line:  # пустая строка
                continue
            try:
                row = json.loads(line)  # одна строка = один JSON-объект
                if row.get("source_url"):  # если есть поле source_url
                    done.add(row["source_url"])  # помечаем URL как уже обработанный
            except json.JSONDecodeError:  # битая строка в файле
                continue  # пропускаем, не падаем
    return done


def append_jsonl(path: str, record: dict) -> None:
    """Дописывает одну запись в output.jsonl (режим append)."""
    try:
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except PermissionError:
        log.error(
            "Нет прав на запись %s. На хосте выполните:\n"
            "  sudo chown -R $USER:$USER .\n"
            "  touch output.jsonl && chmod 664 output.jsonl\n"
            "Или: docker run --user $(id -u):$(id -g) ...",
            path,
        )
        raise


def build_output_json(jsonl_path: str, json_path: str) -> None:
    """Собирает все строки jsonl в один красивый output.json."""
    rows: list[dict] = []  # массив всех записей
    if os.path.isfile(jsonl_path):  # если jsonl есть
        with open(jsonl_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:  # непустая строка
                    rows.append(json.loads(line))  # парсим JSON и кладём в массив
    with open(json_path, "w", encoding="utf-8") as f:  # перезаписываем output.json целиком
        json.dump(rows, f, ensure_ascii=False, indent=2)  # кириллица как есть, отступ 2 пробела
    log.info("Записано %d записей в %s", len(rows), json_path)


def same_domain(url_a: str, url_b: str) -> bool:
    """Один ли домен у двух URL (example.com vs other.com)."""
    return urlparse(url_a).netloc.lower() == urlparse(url_b).netloc.lower()  # сравниваем host без учёта регистра


def pick_extra_pages(home_url: str, links: list[str], max_pages: int = MAX_EXTRA_PAGES) -> list[str]:
    """До max_pages ссылок на контакты/о компании с того же домена."""
    if not links:  # ссылок с главной нет
        return []
    home_parsed = urlparse(home_url)  # разбираем URL главной
    home_normalized = f"{home_parsed.scheme}://{home_parsed.netloc}{home_parsed.path}".rstrip("/")  # канонический вид главной
    candidates: list[tuple[int, str]] = []  # пары (приоритет, url)

    for href in links:  # перебираем все внутренние ссылки
        if not href or href.startswith(("#", "mailto:", "tel:", "javascript:")):  # не веб-страницы
            continue
        full = urljoin(home_url, href)  # относительный путь → полный URL
        parsed = urlparse(full)
        if parsed.scheme not in ("http", "https"):  # только веб
            continue
        if not same_domain(home_url, full):  # чужой домен — пропуск
            continue
        path_lower = (parsed.path or "").lower()  # путь URL в нижнем регистре
        score = 0  # чем выше — тем важнее страница
        for kw in CONTACT_KEYWORDS:  # contact, kontakt...
            if kw in path_lower:
                score += 10  # контакты важнее
        for kw in ABOUT_KEYWORDS:  # about, o-nas...
            if kw in path_lower:
                score += 5
        if score > 0:  # подошла по ключевым словам
            clean = f"{parsed.scheme}://{parsed.netloc}{parsed.path}".rstrip("/")  # URL без ?query и #
            if clean == home_normalized:  # это снова главная — не дублируем
                continue
            candidates.append((score, clean))  # кандидат в доп. страницы

    seen: set[str] = set()  # уже выбранные URL
    result: list[str] = []
    for _, url in sorted(candidates, key=lambda x: -x[0]):  # сначала с большим score
        if url in seen:
            continue
        seen.add(url)
        result.append(url)
        if len(result) >= max_pages:  # не больше 2 доп. страниц
            break
    return result


def get_markdown_from_result(result) -> str:
    """Достаёт текст страницы из ответа Crawl4AI."""
    md = getattr(result, "markdown", None)  # поле markdown у результата краула
    if md is None:
        return ""
    if hasattr(md, "fit_markdown") and md.fit_markdown:  # урезанный текст (меньше токенов для LLM)
        return md.fit_markdown
    if hasattr(md, "raw_markdown") and md.raw_markdown:  # полный markdown
        return md.raw_markdown
    if isinstance(md, str):  # в старых версиях API строка сразу
        return md
    return str(md)  # запасной вариант — привести к строке


def extract_internal_links(result) -> list[str]:
    """Список href внутренних ссылок со страницы."""
    links: list[str] = []
    raw_links = getattr(result, "links", None)  # объект links из Crawl4AI
    if not raw_links:
        return links
    internal = raw_links.get("internal", []) if isinstance(raw_links, dict) else []  # только свои страницы
    for item in internal:
        if isinstance(item, dict):  # формат {"href": "..."}
            href = item.get("href") or item.get("url")
        else:  # объект с атрибутами
            href = getattr(item, "href", None) or getattr(item, "url", None)
        if href:
            links.append(href)
    return links


async def fetch_page(crawler: AsyncWebCrawler, url: str, crawl_config: CrawlerRunConfig):
    """Открывает одну страницу в браузере, возвращает markdown и ссылки."""
    log.info("  Краул: %s", url)
    result = await crawler.arun(url=url, config=crawl_config)  # асинхронный запрос к сайту
    if not result.success:  # таймаут, 404, блокировка...
        raise RuntimeError(result.error_message or "crawl failed")
    markdown = get_markdown_from_result(result)
    if not markdown.strip():  # страница пустая
        raise RuntimeError("пустой markdown")
    return result, markdown, extract_internal_links(result)  # всё нужное вызывающему коду


def create_llm_strategy() -> LLMExtractionStrategy:
    """Настройка извлечения полей через OpenAI gpt-4o-mini."""
    api_key = os.getenv("OPENAI_API_KEY", "").strip()  # ключ из .env или docker --env-file
    if not api_key:
        log.error("Нет OPENAI_API_KEY. Создайте .env или docker run --env-file .env")
        sys.exit(1)  # выход с кодом 1 — контейнер остановится
    return LLMExtractionStrategy(
        llm_config=LLMConfig(
            provider="openai/gpt-4o-mini",  # модель через LiteLLM
            api_token=api_key,
        ),
        schema=CompanyData.model_json_schema(),  # схема 30 полей для structured output
        extraction_type="schema",  # ответ должен быть JSON по схеме
        instruction=LLM_INSTRUCTION,  # правила: не выдумывать, null если нет данных
        input_format="markdown",  # на вход LLM идёт markdown, не HTML
        apply_chunking=True,  # длинный текст режем на куски
        chunk_token_threshold=8000,  # макс. токенов в одном куске
        overlap_rate=0.1,  # 10% перекрытие между кусками
        extra_args={"temperature": 0.0, "max_tokens": 2500},  # строго, без фантазий
        verbose=True,  # подробные логи Crawl4AI
    )


def parse_llm_result(raw_list: list, source_url: str, pages_crawled: list[str]) -> dict:
    """Превращает ответ LLM в dict и дописывает служебные поля."""
    if not raw_list:
        raise ValueError("пустой ответ LLM")
    chunk = raw_list[0]  # обычно один JSON-объект в списке
    if isinstance(chunk, str):
        data = json.loads(chunk)  # строка → dict
    elif isinstance(chunk, dict):
        data = chunk  # уже dict
    else:
        data = json.loads(str(chunk))
    if isinstance(data, list):  # модель вернула массив — берём первый элемент
        data = data[0] if data else {}
    data["source_url"] = source_url  # какой сайт обрабатывали
    data["scraped_at"] = datetime.now(timezone.utc).isoformat()  # время UTC
    data["pages_crawled"] = pages_crawled  # какие URL открыли
    data["error"] = None  # успех — ошибки нет
    return data


async def process_site(
    crawler: AsyncWebCrawler,
    crawl_config: CrawlerRunConfig,
    llm_strategy: LLMExtractionStrategy,
    url: str,
) -> CompanyData:
    """Полный цикл по одному сайту: краул 1–3 страниц + LLM + валидация."""
    pages_crawled: list[str] = []  # какие URL реально открыли
    md_sections: list[str] = []  # куски markdown для LLM

    _, md_home, internal_links = await fetch_page(crawler, url, crawl_config)  # главная
    pages_crawled.append(url)
    md_sections.append(f"# URL: {url}\n\n{md_home}")  # заголовок + текст главной

    for extra_url in pick_extra_pages(url, internal_links, MAX_EXTRA_PAGES):  # до 2 доп. страниц
        try:
            _, md_extra, _ = await fetch_page(crawler, extra_url, crawl_config)
            pages_crawled.append(extra_url)
            md_sections.append(f"# URL: {extra_url}\n\n{md_extra}")
        except Exception as e:  # доп. страница не критична
            log.warning("  Доп. страница пропущена %s: %s", extra_url, e)

    log.info("  LLM: извлечение полей...")
    raw_list = llm_strategy.run(url, md_sections)  # отправка всего текста в OpenAI
    data = parse_llm_result(raw_list, url, pages_crawled)
    return CompanyData.model_validate(data)  # проверка типов Pydantic


async def main_async() -> None:
    """Главный цикл: все URL из sites.txt."""
    load_env_safe()  # в Docker ключ уже из --env-file, не читаем .env с диска

    urls = load_urls(SITES_FILE)  # список из sites.txt
    if not urls:
        log.error("Нет URL в %s", SITES_FILE)
        sys.exit(1)

    done = load_done_urls(OUTPUT_JSONL)  # уже готовые (resume)
    pending = [u for u in urls if u not in done]  # только оставшиеся
    log.info("Всего URL: %d, уже готово: %d, осталось: %d", len(urls), len(done), len(pending))

    if not pending:  # всё уже в jsonl
        log.info("Нечего обрабатывать.")
        build_output_json(OUTPUT_JSONL, OUTPUT_JSON)  # всё равно обновить output.json
        return

    llm_strategy = create_llm_strategy()  # один раз на весь прогон
    browser_cfg = BrowserConfig(headless=True, verbose=False)  # Chrome без окна
    crawl_config = CrawlerRunConfig(
        cache_mode=CacheMode.BYPASS,  # не использовать кэш — всегда свежая страница
        page_timeout=PAGE_TIMEOUT_MS,  # 90 сек
        wait_until="domcontentloaded",  # ждать загрузки DOM
        remove_consent_popups=True,  # закрыть cookie-баннеры
        scan_full_page=True,  # прокрутка вниз
        max_scroll_steps=5,  # не более 5 прокруток
    )

    async with AsyncWebCrawler(config=browser_cfg) as crawler:  # браузер открыт на весь прогон
        for i, url in enumerate(pending, start=1):  # i = 1, 2, 3...
            log.info("[%d/%d] Сайт: %s", i, len(pending), url)
            try:
                record = await process_site(crawler, crawl_config, llm_strategy, url)
                row = record.model_dump()  # Pydantic-модель → обычный dict
            except Exception as e:  # любая ошибка на этом сайте
                log.error("  ОШИБКА: %s", e)
                row = CompanyData(  # запись только с error
                    source_url=url,
                    scraped_at=datetime.now(timezone.utc).isoformat(),
                    pages_crawled=[],
                    error=str(e),
                ).model_dump()

            append_jsonl(OUTPUT_JSONL, row)  # сразу на диск — не потеряется при сбое

            if i < len(pending):  # не пауза после последнего сайта
                delay = random.uniform(DELAY_MIN_SEC, DELAY_MAX_SEC)  # 2–3 сек случайно
                log.info("  Пауза %.1f сек...", delay)
                await asyncio.sleep(delay)  # не перегружать OpenAI

    build_output_json(OUTPUT_JSONL, OUTPUT_JSON)  # финальный массив
    try:
        llm_strategy.show_usage()  # статистика токенов в лог
    except Exception:
        pass  # если API не вернул usage — не важно
    log.info("Готово.")


def main() -> None:
    """Точка входа: запускает асинхронный main_async."""
    asyncio.run(main_async())  # event loop для async/await


if __name__ == "__main__":
    main()  # выполнится только при python parser.py, не при import