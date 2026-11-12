"""Stage 3 — evidence retention, deterministic grounding checks and retrieval precision."""
import os
from pathlib import Path

import pytest

from app.models.schemas import AgentName, AgentTask, Intent
from app.rag.grounding import apply_policy, check_grounding, numbers, split_claims
from tests.conftest import FakeLLM

EV = [{"text": "Crediário | parcelamento em até 48x para seus clientes. | Receba em até 2 dias úteis com taxa única"}]


def test_number_normalisation():
    assert {"79.9", "48", "1.69", "2026-11-05", "20000", "20"} <= numbers("R$ 79,90 · 48x · 1,69% · 05/11/2026 · 20 mil")
    assert numbers("November 5, 2026") == {"2026-11-05"}
    assert numbers("05/11/2026") == numbers("2026-11-05")


def test_supported_claim_is_grounded():
    r = check_grounding("You can offer up to 48 installments [1].", EV, "crediário?")
    assert r.verdict == "grounded" and r.invalid_citations == 0 and r.unsupported_number_claims == 0


def test_contradicting_number_is_detected():
    r = check_grounding("You can offer up to 36 installments [1].", EV, "crediário?")
    assert r.unsupported_number_claims == 1 and r.claims[0].unsupported_numbers == ["36"]


def test_invalid_citation_detected():
    r = check_grounding("Installments go up to 48x [3].", EV, citable=1)
    assert r.invalid_citations == 1


def test_numbers_from_question_are_allowed():
    r = check_grounding("Your sale of R$ 250 can be split in up to 48 installments [1].", EV, "Can I split R$ 250?")
    assert r.unsupported_number_claims == 0


def test_policy_removes_only_deterministic_failures():
    answer = ("You can offer up to 48 installments [1].\n- The fee is R$ 3,99 per sale [1].\n"
              "- Merchants appreciate flexible payment options for customers [1].\nContact Getnet support for details.")
    r = check_grounding(answer, EV, "crediário?", citable=1)
    new, removed = apply_policy(answer, r)
    assert removed == ["The fee is R$ 3,99 per sale [1]."]
    assert "48 installments" in new and "3,99" not in new
    # a weakly supported but number-free claim is reported, NOT deleted
    assert "flexible payment options" in new


def test_boilerplate_is_not_a_factual_claim():
    claims = split_claims("Contact Getnet support for more details. (simulated demo data)")
    r = check_grounding(" ".join(claims), EV)
    assert r.factual_claims == 0 and r.verdict == "no_claims"


async def _knowledge(make_container, answer: str):
    class ScriptedLLM(FakeLLM):
        async def chat(self, system, user, **kw):
            if "ONLY the numbered CONTEXT" in system:
                return answer
            return await super().chat(system, user, **kw)
    c = make_container(ScriptedLLM())
    task = AgentTask(trace_id="t-1", user_id="cliente1988", message="How many installments with the crediário?",
                     intent=Intent.PRODUCT_INFO)
    return c, await c.router.agents[AgentName.KNOWLEDGE].run(task)


async def test_knowledge_retains_exact_evidence(make_container):
    c, r = await _knowledge(make_container, "The crediário lets your customers pay in installments [1].")
    ev = r.data["evidence"]
    assert ev and {"chunk_id", "url", "text", "score", "bm25", "rrf", "rank"} <= set(ev[0])
    assert len(ev) <= c.settings.rag_top_k
    # the hash embedder of the offline suite is not semantic: assert the report exists, not its verdict
    assert r.data["grounding"]["verdict"] in {"grounded", "weak", "ungrounded"}
    assert r.data["grounding"]["removed_claims"] == [] and "installments" in r.answer
    # a number is kept only when it literally appears in the retained evidence
    nums_in_evidence = "48" in " ".join(e["text"] for e in ev)
    _, r2 = await _knowledge(make_container, "Up to 48 installments [1].")
    assert ("48" in r2.answer) == nums_in_evidence


async def test_knowledge_removes_fabricated_number(make_container):
    _, r = await _knowledge(make_container, "Up to 48 installments [1]. The monthly fee is R$ 999,99 [1].")
    assert "999" not in r.answer and r.data["grounding"]["removed_claims"]


async def test_knowledge_extractive_fallback_when_nothing_verifiable(make_container):
    _, r = await _knowledge(make_container, "It costs R$ 777,77 per month [1]. Approval takes 13 days [1].")
    assert r.status == "partial" and "777" not in r.answer and "official website" in r.answer
    assert "fallback=extractive" in r.data["grounding"]["method"]


def test_response_metadata_exposes_evidence_with_trace(client):
    body = client.post("/chat", json={"message": "How many installments can I split a sale into with the crediário?",
                                      "user_id": "cliente1988"}).json()
    ev = body["metadata"]["evidence"]
    assert ev and all(e["agent"] == "knowledge" and e["trace_id"] == body["metadata"]["trace_id"] for e in ev)
    assert "knowledge" in body["metadata"]["grounding"]


def test_support_evidence_records_tool_calls(client):
    body = client.post("/chat", json={"message": "When will the money from yesterday's sales be deposited?",
                                      "user_id": "cliente1988"}).json()
    calls = [e for e in body["metadata"]["evidence"] if e["type"] == "tool"]
    assert calls and all(c["merchant_id"] == "cliente1988" and c["simulated"] for c in calls)


# ---- retrieval precision with the REAL multilingual embedder (skipped if the model cache is absent) ----
MODEL_CACHE = Path(os.environ.get("EMBEDDING_CACHE_DIR", Path(__file__).resolve().parent.parent / ".cache" / "fastembed"))
real_model = pytest.mark.skipif(not MODEL_CACHE.exists() or not any(MODEL_CACHE.iterdir()),
                                reason="fastembed model cache not available (offline CI without the model)")


@pytest.fixture(scope="module")
def real_retriever():
    from app.config import Settings
    from app.rag.ingest import index_pages, load_snapshot
    from app.rag.store import FastEmbedder, Retriever, VectorStore

    s = Settings(embedding_cache_dir=str(MODEL_CACHE))
    store = VectorStore(None, "realmodel_test", FastEmbedder(s.embedding_model, s.embedding_cache_dir))
    index_pages(load_snapshot(s.corpus_path), store, s)
    return Retriever(store, s.rag_top_k, s.rag_min_score, dedupe=s.rag_dedupe, lexical_gate=s.rag_lexical_gate), \
        Retriever(store, 5, 0.25)


@real_model
def test_antecipacao_no_longer_retrieves_card_activation(real_retriever):
    """Root cause of the documented semantic failure (card-activation steps in an antecipação answer)."""
    new, legacy = real_retriever
    q = "How does receivables advance (antecipação) work with Getnet?"
    assert any("cartao-getnet" in h["url"] for h in legacy.retrieve(q))  # the original failure is real
    hits = new.retrieve(q)
    assert hits and "antecipacao-de-vendas" in hits[0]["url"]
    assert not any("cartao-getnet" in h["url"] for h in hits)


@real_model
@pytest.mark.parametrize("q,expected", [
    ("What's the difference between the Get Clássica and the Get Smart?", "todas-as-maquininhas"),
    ("Do I need a bank account to receive my sales via Pix?", "pix"),
    ("How many installments can I split a sale into with the crediário?", "crediario"),
    ("Can I sell through WhatsApp using the Payment Link?", "online-payments"),
    ("Em quantas parcelas posso vender no crediário?", "crediario"),
])
def test_expected_official_page_still_retrieved(real_retriever, q, expected):
    hits = real_retriever[0].retrieve(q)
    assert any(expected in h["url"] for h in hits)


@real_model
def test_no_near_duplicate_chunks_in_context(real_retriever):
    from app.rag.hybrid import tokenize
    hits = real_retriever[0].retrieve("Do I need a bank account to receive my sales via Pix?")
    sets = [set(tokenize(h["text"])) for h in hits]
    for i in range(len(sets)):
        for j in range(i):
            assert len(sets[i] & sets[j]) / min(len(sets[i]), len(sets[j])) < 0.6


def test_scale_error_is_unsupported():
    """eval pt03: 'R$ 20 mil' (thousand) rendered as 'R$ 20 million' must not pass as '20'."""
    ev = [{"text": "Acima de R$ 20mil por mês | Get Clássica"}]
    assert check_grounding("Get Clássica requires above R$ 20 million per month [1].", ev).unsupported_number_claims == 1
    assert check_grounding("Get Clássica requires above R$ 20 thousand per month [1].", ev).unsupported_number_claims == 0
    assert check_grounding("Get Clássica requires above R$ 20,000 per month [1].", ev).unsupported_number_claims == 0


def test_product_topic_filter_drops_other_product_pages():
    """Manual test 2026-11-12: Crediário conditions were presented as antecipação conditions."""
    from app.agents.knowledge import product_topic_filter
    R = "https://site.getnet.com.br"
    hits = [{"url": f"{R}/duvidas/antecipacao-de-vendas/"}, {"url": f"{R}/duvidas/"}, {"url": f"{R}/crediario/"},
            {"url": f"{R}/duvidas/cartao-getnet/"}]
    kept, dropped = product_topic_filter("Como funciona a antecipação de recebíveis na Getnet?", hits)
    assert [h["url"] for h in kept] == [f"{R}/duvidas/antecipacao-de-vendas/", f"{R}/duvidas/"]
    assert dropped == [f"{R}/crediario/", f"{R}/duvidas/cartao-getnet/"]
    # no product named → unchanged; never empties the evidence
    assert product_topic_filter("Quais são as taxas da Getnet?", hits) == (hits, [])
    only_other = [{"url": f"{R}/crediario/"}]
    assert product_topic_filter("Como funciona a antecipação?", only_other) == (only_other, [])
    # an account question keeps the digital-account pages
    acc = [{"url": f"{R}/duvidas/get-conta/"}, {"url": f"{R}/duvidas/pix/"}]
    assert product_topic_filter("Preciso de conta bancária para receber pelo Pix?", acc)[1] == []
