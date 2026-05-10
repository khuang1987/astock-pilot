from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "AStockPilot"
    database_path: str = "./data/astockpilot.db"
    tushare_token: str = ""
    default_sync_days: int = 730
    default_symbol_limit: int = 80
    auto_sim_enabled: bool = True
    auto_sim_run_time: str = "15:30"

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    @property
    def resolved_database_path(self) -> Path:
        path = Path(self.database_path)
        if not path.is_absolute():
            path = Path.cwd() / path
        path.parent.mkdir(parents=True, exist_ok=True)
        return path


@lru_cache
def get_settings() -> Settings:
    return Settings()
