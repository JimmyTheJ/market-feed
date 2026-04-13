"""Profile manager for multi-profile configuration support.

Each profile gets its own directory under config/profiles/{profile_id}/
with isolated copies of positions.yaml, sources.yaml, and settings.yaml.
Profile metadata is stored in config/profiles.yaml.
"""

import logging
import re
import shutil
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)

PROFILES_INDEX = Path("config/profiles.yaml")
PROFILES_DIR = Path("config/profiles")
DEFAULTS_DIR = Path("config/defaults")

# Config files that get copied for each new profile
CONFIG_FILES = ["positions.yaml", "sources.yaml", "settings.yaml"]


def _slugify(name: str) -> str:
    """Convert a profile name to a filesystem-safe slug."""
    slug = name.lower().strip()
    slug = re.sub(r"[^a-z0-9]+", "-", slug)
    slug = slug.strip("-")
    return slug or "profile"


def _load_index() -> list[dict]:
    """Load the profiles index file."""
    if not PROFILES_INDEX.exists():
        return []
    with open(PROFILES_INDEX) as f:
        data = yaml.safe_load(f) or {}
    return data.get("profiles", [])


def _save_index(profiles: list[dict]) -> None:
    """Save the profiles index file."""
    PROFILES_INDEX.parent.mkdir(parents=True, exist_ok=True)
    with open(PROFILES_INDEX, "w") as f:
        yaml.dump({"profiles": profiles}, f, default_flow_style=False, sort_keys=False)


def list_profiles() -> list[dict]:
    """Return all profiles."""
    return _load_index()


def get_profile(profile_id: str) -> dict | None:
    """Get a single profile by ID."""
    for p in _load_index():
        if p["id"] == profile_id:
            return p
    return None


def create_profile(name: str, username: str = "", default_currency: str = "USD") -> dict:
    """Create a new profile, copying default config files into its directory.

    Returns the created profile dict.
    Raises ValueError if a profile with the same slug already exists.
    """
    profile_id = _slugify(name)
    profiles = _load_index()

    # Ensure unique ID
    existing_ids = {p["id"] for p in profiles}
    if profile_id in existing_ids:
        suffix = 2
        while f"{profile_id}-{suffix}" in existing_ids:
            suffix += 1
        profile_id = f"{profile_id}-{suffix}"

    profile = {
        "id": profile_id,
        "name": name.strip(),
        "username": username.strip(),
        "default_currency": default_currency.upper().strip(),
    }

    # Create profile config directory and copy defaults
    profile_dir = PROFILES_DIR / profile_id
    profile_dir.mkdir(parents=True, exist_ok=True)

    for config_file in CONFIG_FILES:
        dest = profile_dir / config_file
        if not dest.exists():
            src = DEFAULTS_DIR / config_file
            if src.exists():
                shutil.copy2(src, dest)
                logger.info(f"Copied default {config_file} to profile '{profile_id}'")
            else:
                logger.warning(f"Default {config_file} not found, skipping")

    profiles.append(profile)
    _save_index(profiles)
    logger.info(f"Created profile '{profile_id}' ({name})")

    return profile


def delete_profile(profile_id: str) -> bool:
    """Delete a profile and its config directory.

    Returns True if the profile was found and deleted, False otherwise.
    """
    profiles = _load_index()
    new_profiles = [p for p in profiles if p["id"] != profile_id]

    if len(new_profiles) == len(profiles):
        return False

    # Remove config directory
    profile_dir = PROFILES_DIR / profile_id
    if profile_dir.exists():
        shutil.rmtree(profile_dir)
        logger.info(f"Removed profile directory: {profile_dir}")

    _save_index(new_profiles)
    logger.info(f"Deleted profile '{profile_id}'")
    return True


def get_profile_config_dir(profile_id: str) -> Path:
    """Get the config directory for a specific profile."""
    return PROFILES_DIR / profile_id
