from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="NFM_", env_file=".env", extra="ignore")

    environment: str = "local"
    api_version: str = "0.1.0"
    database_url: str = "postgresql://forest_monitor:forest_monitor@localhost:5433/forest_monitor"
    cors_origins: str = "http://localhost:3000"
    access_token_secret: str = "local-development-signing-key-change-me"
    cursor_signing_secret: str = "local-development-cursor-key-change-me"
    access_token_minutes: int = 15
    jwt_leeway_seconds: int = Field(default=30, ge=0, le=300)
    refresh_token_days: int = 14
    invitation_hours: int = 72
    password_reset_minutes: int = 30
    max_aoi_area_sq_km: float = Field(default=2_000_000, gt=0)
    max_aoi_vertices: int = Field(default=100_000, ge=4, le=1_000_000)
    password_pepper: str = "local-development-pepper-change-me"
    refresh_cookie_name: str = "nfm_refresh_token"
    csrf_cookie_name: str = "nfm_csrf_token"
    cookie_secure: bool = False
    cookie_samesite: str = "strict"
    seed_admin_email: str = "owner@nfm.local"
    seed_admin_password: str = "LocalForest!2026"

    @property
    def allowed_origins(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
