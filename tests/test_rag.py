"""RAG pipeline: extraction → chunking → embedding/storage → hybrid retrieval → grounded generation."""
import pytest

from app.rag.chunker import chunk_text
from app.rag.hybrid import BM25, rrf, tokenize
from app.rag.ingest import build_chunks, extract, load_snapshot
from app.rag.store import Retriever, VectorStore
from tests.conftest import HashEmbedder

HTML = """<html><head><title>Pix | Getnet</title><script>var x=1</script></head><body>
<nav><a href='/'>Menu</a></nav><main><h1>Receba via Pix</h1><p>Com a Getnet você recebe Pix na hora.</p>
<p>Com a Getnet você recebe Pix na hora.</p><li>Sem custo adicional</li></main><footer>©</footer></body></html>"""


def test_extract_removes_boilerplate_and_duplicates():
    page = extract(HTML, "https://site.getnet.com.br/pix/")
    assert page["title"] == "Pix | Getnet"
    assert "var x" not in page["text"] and "Menu" not in page["text"]
    assert page["text"].count("recebe Pix na hora") == 1
    assert "Sem custo adicional" in page["text"]


def test_chunker_respects_size_and_overlap():
    text = "\n".join(f"Paragraph {i} " + "x" * 120 for i in range(30))
    chunks = chunk_text(text, size=500, overlap=100)
    assert len(chunks) > 5
    assert all(len(c) <= 500 + 100 + 1 for c in chunks)
    with pytest.raises(ValueError):
        chunk_text("abc", size=100, overlap=100)


def test_snapshot_is_real_getnet_content():
    pages = load_snapshot("data/corpus/pages.jsonl")
    assert len(pages) >= 30
    hosts = {p["url"].split("/")[2] for p in pages}
    assert {"www.getnet.net", "site.getnet.com.br"} <= hosts


def test_bm25_and_rrf():
    assert tokenize("Get Clássica e Crediário") == ["get", "classica", "crediario"]
    bm = BM25(["get classica maquininha", "pix conta digital", "crediario parcelas"])
    s = bm.scores("Get Clássica")
    assert s[0] > s[1] and s[0] > s[2]
    fused = rrf([["a", "b"], ["b", "c"]])
    assert max(fused, key=fused.get) == "b"


@pytest.fixture
def retriever(tmp_path):
    store = VectorStore(str(tmp_path / "c"), "test_kb", HashEmbedder())
    store.upsert(build_chunks(load_snapshot("data/corpus/pages.jsonl"), 900, 150))
    return Retriever(store, top_k=5, min_score=0.0)


@pytest.mark.parametrize("query,expected_url_part", [
    ("Get Clássica Get Smart", "maquininha"),
    ("antecipação de vendas", "antecipacao"),
    ("crediário parcelas", "crediario"),
    ("Pix conta", "pix"),
])
def test_hybrid_retrieval_finds_relevant_official_page(retriever, query, expected_url_part):
    hits = retriever.retrieve(query)
    assert hits, "retrieval returned nothing"
    assert any(expected_url_part in h["url"] for h in hits[:5])
    assert all(h["url"].startswith("https://") for h in hits)


def test_upsert_is_idempotent(tmp_path):
    store = VectorStore(str(tmp_path / "c"), "test_kb", HashEmbedder())
    chunks = [{"text": "hello getnet", "url": "https://x", "title": "x", "chunk": 0}]
    store.upsert(chunks)
    store.upsert(chunks)
    assert store.count() == 1
