from __future__ import annotations

from danmaku_pet import paths


def test_source_mode_uses_the_project_directory() -> None:
    assert paths.FROZEN is False
    assert paths.APP_ROOT == paths.RESOURCE_ROOT
    assert (paths.RESOURCE_ROOT / "danmaku_pet" / "web" / "index.html").is_file()


def test_management_page_lives_under_the_resource_root() -> None:
    from danmaku_pet.server import WEB_DIR

    assert WEB_DIR == paths.RESOURCE_ROOT / "danmaku_pet" / "web"
    assert (WEB_DIR / "index.html").is_file()


def test_vendored_helper_is_discoverable() -> None:
    package = paths.RESOURCE_ROOT / "vendor" / "dafeiyu" / "package"
    assert (package / "runtime" / "helper.py").is_file()
    assert (package / "assets" / "pet-manifest.json").is_file()


def test_config_roots_follow_the_path_module() -> None:
    from danmaku_pet.config import ROOT, Config

    assert ROOT == paths.APP_ROOT
    config = Config.load()
    assert config.root == paths.APP_ROOT
    assert config.persona_path == paths.APP_ROOT / "data" / "persona.json"
    assert config.dafeiyu_package == (
        paths.RESOURCE_ROOT / "vendor" / "dafeiyu" / "package"
    )
    assert config.temp_dir.parent == paths.APP_ROOT


def test_persona_seed_is_idempotent() -> None:
    from danmaku_pet.config import Config

    config = Config.load()
    before = config.persona_path.read_text(encoding="utf-8")
    config.seed_persona()
    assert config.persona_path.read_text(encoding="utf-8") == before
