# src/config.py
from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        extra="ignore",
        env_file=".env",
        env_file_encoding="utf-8",
    )

    # 数据路径
    TRADE_PATH: Path
    DETAIL_PATH: Path
    FUND_PATH: Path

    # 项目路径
    PROJECT_ROOT: Path = Path("F:/stock_auditor")
    DATA_DIR: Path = PROJECT_ROOT / "data"
    PROCESSED_DIR: Path = DATA_DIR / "processed"
    LOGS_DIR: Path = PROJECT_ROOT / "logs"

settings = Settings()

# 确保目录存在
settings.PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
settings.LOGS_DIR.mkdir(parents=True, exist_ok=True)