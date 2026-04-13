"""Tests for profile manager."""

import yaml

from src.profile_manager import (
    _slugify,
    create_profile,
    delete_profile,
    get_profile,
    list_profiles,
)


class TestSlugify:
    def test_basic(self):
        assert _slugify("My Portfolio") == "my-portfolio"

    def test_special_chars(self):
        assert _slugify("John's Test!") == "john-s-test"

    def test_leading_trailing(self):
        assert _slugify("  hello  ") == "hello"

    def test_empty(self):
        assert _slugify("") == "profile"

    def test_numbers(self):
        assert _slugify("Portfolio 2026") == "portfolio-2026"


class TestProfileCrud:
    def test_create_and_list(self, tmp_path, monkeypatch):
        monkeypatch.setattr("src.profile_manager.PROFILES_INDEX", tmp_path / "profiles.yaml")
        monkeypatch.setattr("src.profile_manager.PROFILES_DIR", tmp_path / "profiles")
        monkeypatch.setattr("src.profile_manager.DEFAULTS_DIR", tmp_path / "defaults")

        # Create defaults
        defaults = tmp_path / "defaults"
        defaults.mkdir()
        (defaults / "positions.yaml").write_text(
            yaml.dump({"currencies": ["USD"], "positions": []})
        )
        (defaults / "sources.yaml").write_text(yaml.dump({"rss": []}))
        (defaults / "settings.yaml").write_text(yaml.dump({"output_base_path": "output"}))

        profile = create_profile("Test Profile", username="testuser", default_currency="CAD")
        assert profile["id"] == "test-profile"
        assert profile["name"] == "Test Profile"
        assert profile["username"] == "testuser"
        assert profile["default_currency"] == "CAD"

        # Config files should be copied
        profile_dir = tmp_path / "profiles" / "test-profile"
        assert (profile_dir / "positions.yaml").exists()
        assert (profile_dir / "sources.yaml").exists()
        assert (profile_dir / "settings.yaml").exists()

        # List should return the profile
        profiles = list_profiles()
        assert len(profiles) == 1
        assert profiles[0]["id"] == "test-profile"

    def test_create_duplicate_name_gets_suffix(self, tmp_path, monkeypatch):
        monkeypatch.setattr("src.profile_manager.PROFILES_INDEX", tmp_path / "profiles.yaml")
        monkeypatch.setattr("src.profile_manager.PROFILES_DIR", tmp_path / "profiles")
        monkeypatch.setattr("src.profile_manager.DEFAULTS_DIR", tmp_path / "defaults")

        defaults = tmp_path / "defaults"
        defaults.mkdir()
        (defaults / "positions.yaml").write_text(yaml.dump({"positions": []}))

        p1 = create_profile("Test")
        p2 = create_profile("Test")
        assert p1["id"] == "test"
        assert p2["id"] == "test-2"

    def test_get_profile(self, tmp_path, monkeypatch):
        monkeypatch.setattr("src.profile_manager.PROFILES_INDEX", tmp_path / "profiles.yaml")
        monkeypatch.setattr("src.profile_manager.PROFILES_DIR", tmp_path / "profiles")
        monkeypatch.setattr("src.profile_manager.DEFAULTS_DIR", tmp_path / "defaults")
        (tmp_path / "defaults").mkdir()

        create_profile("My Profile")
        p = get_profile("my-profile")
        assert p is not None
        assert p["name"] == "My Profile"

        assert get_profile("nonexistent") is None

    def test_delete_profile(self, tmp_path, monkeypatch):
        monkeypatch.setattr("src.profile_manager.PROFILES_INDEX", tmp_path / "profiles.yaml")
        monkeypatch.setattr("src.profile_manager.PROFILES_DIR", tmp_path / "profiles")
        monkeypatch.setattr("src.profile_manager.DEFAULTS_DIR", tmp_path / "defaults")

        defaults = tmp_path / "defaults"
        defaults.mkdir()
        (defaults / "positions.yaml").write_text(yaml.dump({"positions": []}))

        create_profile("To Delete")
        profile_dir = tmp_path / "profiles" / "to-delete"
        assert profile_dir.exists()

        result = delete_profile("to-delete")
        assert result is True
        assert not profile_dir.exists()
        assert len(list_profiles()) == 0

    def test_delete_nonexistent(self, tmp_path, monkeypatch):
        monkeypatch.setattr("src.profile_manager.PROFILES_INDEX", tmp_path / "profiles.yaml")
        monkeypatch.setattr("src.profile_manager.PROFILES_DIR", tmp_path / "profiles")
        assert delete_profile("nonexistent") is False

    def test_empty_list_when_no_index(self, tmp_path, monkeypatch):
        monkeypatch.setattr("src.profile_manager.PROFILES_INDEX", tmp_path / "nope.yaml")
        assert list_profiles() == []
