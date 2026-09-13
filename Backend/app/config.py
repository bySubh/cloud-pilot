"""
Central application configuration.

All configuration is sourced from environment variables (see .env.example).
Nothing here should ever contain a real secret - only defaults that are safe
for local development.
"""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_ROOT = Path(__file__).resolve().parent.parent
WORKSPACE_ROOT = BACKEND_ROOT / "workspace"
INFRA_TEMPLATES_ROOT = BACKEND_ROOT / "infra_templates"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # --- Google Vertex AI / Gemini -----------------------------------
    google_cloud_project: str = Field(default="", alias="GOOGLE_CLOUD_PROJECT")
    google_cloud_location: str = Field(default="us-central1", alias="GOOGLE_CLOUD_LOCATION")
    google_genai_use_vertexai: bool = Field(default=True, alias="GOOGLE_GENAI_USE_VERTEXAI")
    gemini_model: str = Field(default="gemini-1.5-pro", alias="GEMINI_MODEL")

    # --- Terraform safety ----------------------------------------------
    sentinel_tf_allow_apply: bool = Field(default=False, alias="SENTINEL_TF_ALLOW_APPLY")
    terraform_binary: str = Field(default="terraform", alias="TERRAFORM_BINARY")
    checkov_binary: str = Field(default="checkov", alias="CHECKOV_BINARY")
    terraform_timeout_seconds: int = Field(default=120, alias="TERRAFORM_TIMEOUT_SECONDS")
    sentinel_allow_mock_terraform: bool = Field(
        default=True,
        alias="SENTINEL_ALLOW_MOCK_TERRAFORM",
        description="If the real terraform binary is missing, run a clearly-labeled local static "
        "validator instead of skipping validation entirely. Never represented as a real GCP plan.",
    )

    # --- Persistence -----------------------------------------------------
    database_url: str = Field(default="", alias="DATABASE_URL")

    # --- RAG / Chroma -------------------------------------------------
    chroma_persist_directory: str = Field(
        default=str(BACKEND_ROOT / ".chroma"), alias="CHROMA_PERSIST_DIRECTORY"
    )
    embedding_model_name: str = Field(
        default="sentence-transformers/all-MiniLM-L6-v2", alias="EMBEDDING_MODEL_NAME"
    )

    # --- Orchestration -------------------------------------------------
    max_heal_attempts: int = Field(default=2, alias="MAX_HEAL_ATTEMPTS")

    # --- Misc -------------------------------------------------------------
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")
    cors_origins: str = Field(default="*", alias="CORS_ORIGINS")

    @property
    def cors_origin_list(self) -> list[str]:
        if self.cors_origins.strip() == "*":
            return ["*"]
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def use_sqlite_fallback(self) -> bool:
        """True when no real DATABASE_URL is configured for local dev."""
        return not self.database_url


@lru_cache
def get_settings() -> Settings:
    return Settings()


def ensure_directories() -> None:
    WORKSPACE_ROOT.mkdir(parents=True, exist_ok=True)
    Path(get_settings().chroma_persist_directory).mkdir(parents=True, exist_ok=True)
