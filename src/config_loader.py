"""Configuration loader with defaults and override support.

Loading priority:
1. User override: config/{name}
2. Default: config/defaults/{name}

For settings, deep merge is applied so user overrides can be partial.
For positions and sources, user override replaces the default entirely.

Future: profiles support via config/profiles/{profile_name}/{name}
"""

import logging
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)


def deep_merge(base: dict, override: dict) -> dict:
    """Deep merge override into base dict. Override values win."""
    result = base.copy()
    for key, value in override.items():
        if (
            key in result
            and isinstance(result[key], dict)
            and isinstance(value, dict)
        ):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def resolve_config_path(
    name: str,
    config_dir: str | Path = "config",
    profile: str | None = None,
) -> Path:
    """Resolve config file path with fallback chain.

    Priority: profile → user override → default.
    """
    config_dir = Path(config_dir)

    # Profile path (future)
    if profile:
        profile_path = config_dir / "profiles" / profile / name
        if profile_path.exists():
            logger.info(f"Using profile config [{profile}]: {profile_path}")
            return profile_path

    # User override
    user_path = config_dir / name
    if user_path.exists():
        logger.info(f"Using user config: {user_path}")
        return user_path

    # Default
    default_path = config_dir / "defaults" / name
    if default_path.exists():
        logger.info(f"Using default config: {default_path}")
        return default_path

    raise FileNotFoundError(
        f"Config '{name}' not found in {config_dir} or {config_dir}/defaults/"
    )


def load_yaml_config(
    name: str,
    config_dir: str | Path = "config",
    merge_with_defaults: bool = False,
    profile: str | None = None,
) -> dict:
    """Load a YAML config file with fallback to defaults.

    If merge_with_defaults=True, the user config is deep-merged on top of
    the default config. This is useful for settings.yaml where partial
    overrides should inherit remaining defaults.
    """
    config_dir = Path(config_dir)
    default_path = config_dir / "defaults" / name

    # Load default
    default_data = {}
    if default_path.exists():
        with open(default_path) as f:
            default_data = yaml.safe_load(f) or {}

    # Resolve the active config path
    active_path = None

    # Profile check (future)
    if profile:
        profile_path = config_dir / "profiles" / profile / name
        if profile_path.exists():
            active_path = profile_path

    # User override check
    if active_path is None:
        user_path = config_dir / name
        if user_path.exists():
            active_path = user_path

    # If we have an active override, load it
    if active_path is not None:
        with open(active_path) as f:
            user_data = yaml.safe_load(f) or {}

        if merge_with_defaults and default_data:
            logger.info(f"Merging config '{name}' (override + defaults)")
            return deep_merge(default_data, user_data)
        else:
            logger.info(f"Using override config: {active_path}")
            return user_data

    # Fall back to default
    if default_data:
        logger.info(f"Using default config: {default_path}")
        return default_data

    raise FileNotFoundError(
        f"Config '{name}' not found in {config_dir} or {config_dir}/defaults/"
    )


def resolve_data_path(
    name: str,
    data_dir: str | Path = "data",
) -> Path:
    """Resolve data file path: user data takes precedence over defaults."""
    data_dir = Path(data_dir)

    user_path = data_dir / name
    if user_path.exists():
        logger.info(f"Using user data: {user_path}")
        return user_path

    default_path = data_dir / "defaults" / name
    if default_path.exists():
        logger.info(f"Using default data: {default_path}")
        return default_path

    raise FileNotFoundError(
        f"Data '{name}' not found in {data_dir} or {data_dir}/defaults/"
    )
