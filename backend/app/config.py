from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    llm_base_url: str = "http://localhost/v1"
    llm_api_key: str = ""
    llm_model: str = "DeepSeek-V4-Flash"
    dataset_path: str = "../Dataset.xlsx"
    catalog_path: str = "./data/catalog.normalized.json"
    enable_embeddings: bool = False


@lru_cache
def get_settings() -> Settings:
    return Settings()
