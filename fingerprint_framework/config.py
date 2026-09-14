from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import Optional


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )

    webshare_api_key: str = Field(default="", alias="WEBSHARE_API_KEY")
    webshare_base_url: str = Field(default="https://proxy.webshare.io/api/v2", alias="WEBSHARE_BASE_URL")
    proxy_timeout: int = Field(default=30, alias="PROXY_TIMEOUT")
    browser_timeout: int = Field(default=60000, alias="BROWSER_TIMEOUT")
    navigation_timeout: int = Field(default=30000, alias="NAVIGATION_TIMEOUT")
    headless: bool = Field(default=True, alias="HEADLESS")
    fingerprint_test_url: str = Field(
        default="https://sannysoft.com/fingerprinttest",
        alias="FINGERPRINT_TEST_URL"
    )
    user_agent: str = Field(
        default="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        alias="USER_AGENT"
    )
    viewport_width: int = Field(default=1920, alias="VIEWPORT_WIDTH")
    viewport_height: int = Field(default=1080, alias="VIEWPORT_HEIGHT")
    locale: str = Field(default="en-US", alias="LOCALE")
    timezone_id: str = Field(default="America/New_York", alias="TIMEZONE_ID")


settings = Settings()