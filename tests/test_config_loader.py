"""Tests for the config loader with defaults/override/profile support."""

import pytest
import yaml

from src.config_loader import deep_merge, load_yaml_config, resolve_config_path, resolve_data_path


class TestDeepMerge:
    def test_simple_override(self):
        base = {"a": 1, "b": 2}
        override = {"b": 3}
        assert deep_merge(base, override) == {"a": 1, "b": 3}

    def test_nested_merge(self):
        base = {"scoring": {"weight": 0.5, "threshold": 10}, "other": True}
        override = {"scoring": {"weight": 0.8}}
        result = deep_merge(base, override)
        assert result["scoring"]["weight"] == 0.8
        assert result["scoring"]["threshold"] == 10
        assert result["other"] is True

    def test_new_keys_added(self):
        base = {"a": 1}
        override = {"b": 2}
        assert deep_merge(base, override) == {"a": 1, "b": 2}

    def test_override_replaces_non_dict(self):
        base = {"a": {"x": 1}}
        override = {"a": "replaced"}
        assert deep_merge(base, override) == {"a": "replaced"}

    def test_empty_override(self):
        base = {"a": 1}
        assert deep_merge(base, {}) == {"a": 1}

    def test_empty_base(self):
        override = {"a": 1}
        assert deep_merge({}, override) == {"a": 1}

    def test_deeply_nested(self):
        base = {"l1": {"l2": {"l3": {"val": 1, "keep": True}}}}
        override = {"l1": {"l2": {"l3": {"val": 2}}}}
        result = deep_merge(base, override)
        assert result["l1"]["l2"]["l3"]["val"] == 2
        assert result["l1"]["l2"]["l3"]["keep"] is True


class TestResolveConfigPath:
    def test_user_override_found(self, tmp_path):
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        user_file = config_dir / "positions.yaml"
        user_file.write_text("test")

        result = resolve_config_path("positions.yaml", config_dir=config_dir)
        assert result == user_file

    def test_fallback_to_default(self, tmp_path):
        config_dir = tmp_path / "config"
        defaults_dir = config_dir / "defaults"
        defaults_dir.mkdir(parents=True)
        default_file = defaults_dir / "positions.yaml"
        default_file.write_text("test")

        result = resolve_config_path("positions.yaml", config_dir=config_dir)
        assert result == default_file

    def test_user_override_takes_precedence(self, tmp_path):
        config_dir = tmp_path / "config"
        defaults_dir = config_dir / "defaults"
        defaults_dir.mkdir(parents=True)
        (defaults_dir / "positions.yaml").write_text("default")
        user_file = config_dir / "positions.yaml"
        user_file.write_text("override")

        result = resolve_config_path("positions.yaml", config_dir=config_dir)
        assert result == user_file

    def test_profile_takes_precedence(self, tmp_path):
        config_dir = tmp_path / "config"
        defaults_dir = config_dir / "defaults"
        defaults_dir.mkdir(parents=True)
        (defaults_dir / "positions.yaml").write_text("default")
        (config_dir / "positions.yaml").write_text("override")

        profile_dir = config_dir / "profiles" / "aggressive"
        profile_dir.mkdir(parents=True)
        profile_file = profile_dir / "positions.yaml"
        profile_file.write_text("profile")

        result = resolve_config_path(
            "positions.yaml", config_dir=config_dir, profile="aggressive"
        )
        assert result == profile_file

    def test_not_found_raises(self, tmp_path):
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        with pytest.raises(FileNotFoundError):
            resolve_config_path("missing.yaml", config_dir=config_dir)


class TestLoadYamlConfig:
    def test_load_default(self, tmp_path):
        config_dir = tmp_path / "config"
        defaults_dir = config_dir / "defaults"
        defaults_dir.mkdir(parents=True)
        (defaults_dir / "settings.yaml").write_text(
            yaml.dump({"scoring": {"weight": 0.5}, "schedule": {"hour": 6}})
        )

        result = load_yaml_config("settings.yaml", config_dir=config_dir)
        assert result["scoring"]["weight"] == 0.5

    def test_load_user_override(self, tmp_path):
        config_dir = tmp_path / "config"
        defaults_dir = config_dir / "defaults"
        defaults_dir.mkdir(parents=True)
        (defaults_dir / "sources.yaml").write_text(yaml.dump({"rss": ["a", "b"]}))
        (config_dir / "sources.yaml").write_text(yaml.dump({"rss": ["c"]}))

        result = load_yaml_config("sources.yaml", config_dir=config_dir)
        assert result["rss"] == ["c"]

    def test_deep_merge_settings(self, tmp_path):
        config_dir = tmp_path / "config"
        defaults_dir = config_dir / "defaults"
        defaults_dir.mkdir(parents=True)
        (defaults_dir / "settings.yaml").write_text(
            yaml.dump({"scoring": {"weight": 0.5, "threshold": 10}, "cache": True})
        )
        (config_dir / "settings.yaml").write_text(
            yaml.dump({"scoring": {"weight": 0.9}})
        )

        result = load_yaml_config(
            "settings.yaml", config_dir=config_dir, merge_with_defaults=True
        )
        assert result["scoring"]["weight"] == 0.9
        assert result["scoring"]["threshold"] == 10
        assert result["cache"] is True

    def test_not_found_raises(self, tmp_path):
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        with pytest.raises(FileNotFoundError):
            load_yaml_config("missing.yaml", config_dir=config_dir)


class TestResolveDataPath:
    def test_user_data_found(self, tmp_path):
        data_dir = tmp_path / "data"
        meta_dir = data_dir / "metadata"
        meta_dir.mkdir(parents=True)
        user_file = meta_dir / "ticker_metadata.yaml"
        user_file.write_text("test")

        result = resolve_data_path("metadata/ticker_metadata.yaml", data_dir=data_dir)
        assert result == user_file

    def test_fallback_to_default_data(self, tmp_path):
        data_dir = tmp_path / "data"
        defaults_dir = data_dir / "defaults" / "metadata"
        defaults_dir.mkdir(parents=True)
        default_file = defaults_dir / "ticker_metadata.yaml"
        default_file.write_text("test")

        result = resolve_data_path("metadata/ticker_metadata.yaml", data_dir=data_dir)
        assert result == default_file

    def test_data_not_found_raises(self, tmp_path):
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        with pytest.raises(FileNotFoundError):
            resolve_data_path("metadata/missing.yaml", data_dir=data_dir)
