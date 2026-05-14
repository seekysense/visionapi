from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import yaml
from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=BASE_DIR / ".env", extra="ignore")

    api_password: str
    api_admin_password: str

    llm_base_url: str
    llm_api_key: str
    llm_model: str
    llm_reasoning: bool = False
    llm_timeout: int = 60
    llm_context_window: int = 64000

    axis_default_user: str = "root"
    axis_default_pass: str = ""


@lru_cache
def get_settings() -> Settings:
    return Settings()


def _cameras_path() -> Path:
    return BASE_DIR / "cameras.yaml"


def _actions_path() -> Path:
    return BASE_DIR / "actions.yaml"


def load_cameras() -> list[dict]:
    with open(_cameras_path()) as f:
        return yaml.safe_load(f).get("cameras", [])


def save_cameras(cameras: list[dict]) -> None:
    with open(_cameras_path(), "w") as f:
        yaml.safe_dump(
            {"cameras": cameras},
            f,
            allow_unicode=True,
            default_flow_style=False,
            sort_keys=False,
        )


def load_actions() -> list[dict]:
    with open(_actions_path()) as f:
        return yaml.safe_load(f).get("actions", [])


def save_actions(actions: list[dict]) -> None:
    with open(_actions_path(), "w") as f:
        yaml.safe_dump(
            {"actions": actions},
            f,
            allow_unicode=True,
            default_flow_style=False,
            sort_keys=False,
        )


def _sequences_path() -> Path:
    return BASE_DIR / "sequences.yaml"


def load_sequences() -> list[dict]:
    path = _sequences_path()
    if not path.exists():
        return []
    with open(path) as f:
        return yaml.safe_load(f).get("sequences", [])


def save_sequences(sequences: list[dict]) -> None:
    with open(_sequences_path(), "w") as f:
        yaml.safe_dump(
            {"sequences": sequences},
            f,
            allow_unicode=True,
            default_flow_style=False,
            sort_keys=False,
        )
