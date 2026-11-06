"""RAG ingestion: fetch -> extract -> snapshot -> chunk -> embed -> store.

Usage:
    python -m app.rag.ingest            # build index from committed snapshot
    python -m app.rag.ingest --crawl    # re-crawl live Getnet sites, refresh snapshot, rebuild index
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import re
from pathlib import Path
from urllib.parse import urljoin, urldefrag, urlparse

import httpx
from bs4 import BeautifulSoup

from app.config import get_settings
from app.rag.chunker import chunk_text

logger = logging.getLogger(__name__)

# Official sources. getnet.net/en is the PDF's suggested source; site.getnet.com.br is the
# site linked in the PDF ("Getnet | Máquinas de cartão e soluções financeiras para o seu negócio").
SEED_URLS = [
    "https://www.getnet.net/en/",
    "https://www.getnet.net/en/our-solutions/in-person-payments",
    "https://www.getnet.net/en/our-solutions/online-payments",
    "https://www.getnet.net/en/our-solutions/omnichannel",
    "https://www.getnet.net/en/our-solutions/value-added-solutions",
    "https://www.getnet.net/en/corporate/about-us",
    "https://site.getnet.com.br/",
    "https://site.getnet.com.br/todas-as-maquininhas/",
    "https://site.getnet.com.br/pix/",
    "https://site.getnet.com.br/link-de-pagamento/",
    "https://site.getnet.com.br/pagamentos-pelo-whatsapp/",
    "https://site.getnet.com.br/get-ajuda/",
    "https://site.getnet.com.br/get-tap/",
    "https://site.getnet.com.br/getcheckout/",
    "https://site.getnet.com.br/tef/solucao-tef/",
    "https://site.getnet.com.br/proteja-seu-negocio-e-venda-com-seguranca/",
    "https://site.getnet.com.br/ofertas-maquininha-de-cartao-getnet-2/",
    "https://site.getnet.com.br/crediario/",
    "https://site.getnet.com.br/conta-digital/",
    "https://site.getnet.com.br/taxas/",
    # Official FAQ ("Dúvidas") pages — the richest source of support answers
    "https://site.getnet.com.br/duvidas/",
    "https://site.getnet.com.br/duvidas/antecipacao-de-vendas/",
    "https://site.getnet.com.br/duvidas/crediario/",
    "https://site.getnet.com.br/duvidas/get-smart/",
    "https://site.getnet.com.br/duvidas/link-de-pagamento/",
    "https://site.getnet.com.br/duvidas/pix/",
    "https://site.getnet.com.br/duvidas/pix-mais-respostas/",
    "https://site.getnet.com.br/duvidas/operacional/",
    "https://site.getnet.com.br/duvidas/parcelado/",
    "https://site.getnet.com.br/duvidas/taxas/",
    "https://site.getnet.com.br/duvidas/get-conta/",
    "https://site.getnet.com.br/duvidas/app-getnet/",
    "https://site.getnet.com.br/duvidas/aluguel/",
    "https://site.getnet.com.br/duvidas/cartao-getnet/",
]
ALLOWED_HOSTS = {"www.getnet.net", "site.getnet.com.br"}
# Keep the crawl focused on product/help content (skip blog/legal/other locales).
FOLLOW_RE = re.compile(
    r"(maquininha|maquina|pix|link|antecip|credi|conta|ajuda|duvida|tap|checkout|tef|"
    r"whatsapp|recebi|taxa|plano|super|smart|classica|mini|pos|our-solutions|your-business)",
    re.I,
)
SKIP_RE = re.compile(r"(/blog|/legal|/politica|/wp-content|\.pdf$|/ar/|/es/|/mx/|/uy/|/pt/|/tag/|/category/|/author/)", re.I)
UA = "GetnetAssessmentBot/1.0 (+technical assessment; respectful single-pass crawl)"


def extract(html: str, url: str) -> dict:
    soup = BeautifulSoup(html, "html.parser")
    title = (soup.title.string or "").strip() if soup.title else url
    for tag in soup(["script", "style", "noscript", "svg", "nav", "footer", "header", "form", "iframe"]):
        tag.decompose()
    main = soup.find("main") or soup.body or soup
    lines = []
    for el in main.find_all(["h1", "h2", "h3", "h4", "p", "li", "td", "th", "summary", "dt", "dd"]):
        t = re.sub(r"\s+", " ", el.get_text(" ", strip=True)).strip()
        if len(t) >= 3:
            lines.append(t)
    # de-duplicate repeated blocks (menus, carousels) while keeping order
    seen, text_lines = set(), []
    for ln in lines:
        if ln not in seen:
            seen.add(ln)
            text_lines.append(ln)
    return {"url": url, "title": title, "text": "\n".join(text_lines)}


def _links(html: str, base: str) -> list[str]:
    soup = BeautifulSoup(html, "html.parser")
    out = []
    for a in soup.find_all("a", href=True):
        u = urldefrag(urljoin(base, a["href"]))[0]
        p = urlparse(u)
        if p.scheme == "https" and p.netloc in ALLOWED_HOSTS and FOLLOW_RE.search(p.path) and not SKIP_RE.search(u):
            out.append(u)
    return out


async def crawl(seeds: list[str] = SEED_URLS, max_pages: int = 60, depth: int = 1) -> list[dict]:
    seen: set[str] = set()
    pages: list[dict] = []
    frontier = [(u, 0) for u in seeds]
    async with httpx.AsyncClient(timeout=20, follow_redirects=True, headers={"User-Agent": UA}) as client:
        while frontier and len(pages) < max_pages:
            url, d = frontier.pop(0)
            key = url.rstrip("/")
            if key in seen:
                continue
            seen.add(key)
            try:
                r = await client.get(url)
                if r.status_code != 200 or "text/html" not in r.headers.get("content-type", ""):
                    continue
            except httpx.HTTPError as e:
                logger.warning("fetch failed %s: %s", url, e)
                continue
            final = str(r.url).rstrip("/")
            if final != key and final in seen:
                continue  # redirect to an already-fetched page
            seen.add(final)
            page = extract(r.text, str(r.url))
            if len(page["text"]) > 200:
                pages.append(page)
                logger.info("fetched %s (%d chars)", r.url, len(page["text"]))
            if d < depth:
                frontier.extend((u, d + 1) for u in _links(r.text, str(r.url)))
    return pages


def save_snapshot(pages: list[dict], path: str) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for p in pages:
            f.write(json.dumps(p, ensure_ascii=False) + "\n")


def load_snapshot(path: str) -> list[dict]:
    p = Path(path)
    if not p.exists():
        return []
    return [json.loads(line) for line in p.read_text(encoding="utf-8").splitlines() if line.strip()]


def build_chunks(pages: list[dict], size: int, overlap: int) -> list[dict]:
    chunks = []
    for page in pages:
        for i, text in enumerate(chunk_text(page["text"], size, overlap)):
            chunks.append({"text": f"{page['title']}\n{text}", "url": page["url"], "title": page["title"], "chunk": i})
    return chunks


def index_pages(pages: list[dict], store, settings=None) -> int:
    s = settings or get_settings()
    chunks = build_chunks(pages, s.chunk_size, s.chunk_overlap)
    store.upsert(chunks)
    return len(chunks)


def main() -> None:
    from app.services.observability import setup_logging
    from app.rag.store import build_vector_store

    ap = argparse.ArgumentParser()
    ap.add_argument("--crawl", action="store_true", help="re-crawl live sites and refresh the snapshot")
    ap.add_argument("--max-pages", type=int, default=60)
    args = ap.parse_args()
    setup_logging()
    s = get_settings()
    if args.crawl:
        pages = asyncio.run(crawl(max_pages=args.max_pages))
        save_snapshot(pages, s.corpus_path)
        logger.info("snapshot saved: %d pages -> %s", len(pages), s.corpus_path)
    pages = load_snapshot(s.corpus_path)
    store = build_vector_store(s)
    store.reset()
    n = index_pages(pages, store, s)
    logger.info("indexed %d chunks from %d pages into collection '%s'", n, len(pages), s.chroma_collection)


if __name__ == "__main__":
    main()
