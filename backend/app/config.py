import os
from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict

_APP_DIR = os.path.dirname(os.path.abspath(__file__))
_BACKEND_DIR = os.path.dirname(_APP_DIR)
_WORKSPACE_DIR = os.path.dirname(_BACKEND_DIR)
_BACKEND_DATA_DIR = os.path.join(_BACKEND_DIR, "data")
# products.db chuẩn hoá về đúng 1 vị trí trong package agent_core, resolve tuyệt đối
# theo vị trí file (không phụ thuộc cwd khi chạy uvicorn / pytest).
_DEFAULT_AGENT_DB = os.path.join(_APP_DIR, "agent_core", "products.db")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    llm_base_url: str = "http://localhost/v1"
    llm_api_key: str = ""
    llm_model: str = "gpt-oss-120b"
    dataset_path: str = os.path.join(_WORKSPACE_DIR, "data", "Spec_cate_gia.xlsx")
    # Nguồn spec gốc 14 sheet dùng riêng cho bước làm sạch; không dùng DATASET_PATH
    # vì môi trường legacy có thể trỏ DATASET_PATH ngược về workbook cleaned.
    spec_source_path: str = os.path.join(_WORKSPACE_DIR, "data", "Spec_cate_gia.xlsx")
    catalog_path: str = os.path.join(_BACKEND_DATA_DIR, "catalog.normalized.json")
    crawl_cleaned_path: str = os.path.join(_BACKEND_DATA_DIR, "products_detail.cleaned.json")
    crawl_path: str = os.path.join(_WORKSPACE_DIR, "data", "products_detail.json")
    enable_embeddings: bool = False
    # Danh sách origin được phép gọi API (CORS), phân tách bằng dấu phẩy.
    frontend_origins: str = "http://localhost:5173"
    # Luồng phục vụ: "agent_core" (LangGraph + SQLite) hoặc "orchestrator" (bản cũ).
    pipeline: str = "agent_core"
    # DB SQLite của agent_core; đường dẫn tuyệt đối mặc định, override bằng AGENT_DB_PATH.
    agent_db_path: str = _DEFAULT_AGENT_DB
    # Nguồn Excel để rebuild DB (chỉ dùng khi chạy data_ingestion).
    excel_source_path: str = os.path.join(_BACKEND_DATA_DIR, "Spec_cate_gia.cleaned.xlsx")


@lru_cache
def get_settings() -> Settings:
    return Settings()
