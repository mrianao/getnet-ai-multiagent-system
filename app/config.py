"""Application settings (12-factor: everything comes from the environment / .env)."""
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # --- LLM (default: local Ollama) ---
    # LLM_PROVIDER=anthropic uses Claude via the Anthropic SDK (LLM_BASE_URL is ignored);
    # any other value uses the OpenAI-compatible client.
    llm_provider: str = "ollama"
    llm_base_url: str = "http://localhost:11434/v1"
    llm_model: str = "llama3.2:3b"
    llm_api_key: str = "ollama"  # Ollama ignores it; Groq/OpenAI/Anthropic need a real key
    llm_temperature: float = 0.1
    llm_timeout_s: float = 120.0
    llm_max_tokens: int = 500

    # --- RAG ---
    embedding_model: str = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
    embedding_cache_dir: str = str(ROOT / ".cache" / "fastembed")
    chroma_dir: str = str(ROOT / ".cache" / "chroma")
    chroma_collection: str = "getnet_kb"
    corpus_path: str = str(ROOT / "data" / "corpus" / "pages.jsonl")
    chunk_size: int = 900
    chunk_overlap: int = 150
    rag_top_k: int = 5
    rag_min_score: float = 0.25
    auto_ingest: bool = True  # build the index from the snapshot at startup if empty

    # --- Web tools ---
    tavily_api_key: str = ""
    http_timeout_s: float = 10.0

    # --- State, auth & conversations (SQLite, standard library) ---
    state_db_path: str = str(ROOT / ".cache" / "state" / "app.db")
    history_window: int = 6           # messages (≈3 turns) fed back as conversation context
    token_ttl_hours: float = 24 * 7   # default lifetime of issued tokens

    # --- App ---
    log_level: str = "INFO"
    max_message_chars: int = 2000


@lru_cache
def get_settings() -> Settings:
    return Settings()
