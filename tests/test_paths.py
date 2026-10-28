from __future__ import annotations

from danmaku_pet import paths


def test_source_mode_uses_the_project_directory() -> None:
    assert paths.FROZEN is False
    assert paths.APP_ROOT == paths.RESOURCE_ROOT


def test_split_web_pages_live_under_the_resource_root() -> None:
    web = paths.RESOURCE_ROOT / "danmaku_pet" / "web"
    assert (web / "cloud" / "index.html").is_file()
    assert (web / "client" / "index.html").is_file()
    # The old single-page console is gone: local UI must not offer a key field.
    assert not (web / "index.html").exists()


def test_vendored_helper_is_discoverable() -> None:
    package = paths.RESOURCE_ROOT / "vendor" / "dafeiyu" / "package"
    assert (package / "runtime" / "helper.py").is_file()
    assert (package / "assets" / "pet-manifest.json").is_file()


def test_client_config_roots_follow_the_path_module() -> None:
    from danmaku_pet.config import ROOT, Config

    assert ROOT == paths.APP_ROOT
    config = Config.load(use_env_file=False)
    assert config.root == paths.APP_ROOT
    assert config.dafeiyu_package == (
        paths.RESOURCE_ROOT / "vendor" / "dafeiyu" / "package"
    )
    assert config.temp_dir.parent == paths.APP_ROOT
    assert config.cloud_url.startswith("ws://")
    # The client has no model key field at all.
    assert not hasattr(config, "deepseek_api_key")


def test_client_config_has_no_persona_dependency() -> None:
    from danmaku_pet.config import Config

    config = Config.load(use_env_file=False)
    assert not hasattr(config, "persona_path")
    assert not hasattr(config, "persona")
