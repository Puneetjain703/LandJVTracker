from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "Land and JV Tracker"
    environment: str = "development"
    api_base_url: str = "http://localhost:8000"
    api_secret_key: str = Field(default="dev-only-change-me")

    app_username: str = "admin"
    app_password: str = "change-me"
    app_password_hash: str | None = None

    database_url: str = "postgresql+psycopg2://land_jv:land_jv@localhost:5432/land_jv_tracker"

    openai_api_key: str | None = None
    openai_model: str = "gpt-4.1-mini"

    notion_api_key: str | None = None
    notion_database_id: str | None = None
    notion_source_name: str = "Pearl Spytech New Projects"
    notion_pearl_projects_page_id: str | None = None
    notion_analyze_lrm_page_id: str | None = "2995c898ef918040a360c467e4837e4c"
    notion_analyze_lrm_source_name: str = "Analyze the Property deals and update LRM"
    notion_brokerage_new_deals_page_id: str | None = "29a5c898ef91801598afdcf276fe057b"
    notion_brokerage_source_name: str = "Brokerage New Deals"

    google_sheet_id: str | None = "1LC7bnveXagIs8Kc4xIaxEMVcAkViJzX7KZQYyOJdmds"
    google_sheet_tabs: str = "Master-2026,Master"
    google_service_account_file: str | None = None
    google_service_account_json: str | None = None

    geocoder_user_agent: str = "land-jv-tracker-internal"
    google_maps_api_key: str | None = None
    
    # Scheduling settings (HH:MM format in UTC or local depending on server time)
    sync_schedule_morning: str = "07:00"
    sync_schedule_evening: str = "19:00"


@lru_cache
def get_settings() -> Settings:
    return Settings()

