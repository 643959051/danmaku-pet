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


def test_persona_seed_is_idempotent(tmp_path, monkeypatch) -> None:
    """seed_persona 只从打包进来的默认人格复制一次，之后不覆盖网页端的改动。

    CI 里 ``data/persona.json`` 不入库（本地个性化配置），所以这里把
    ``RESOURCE_ROOT`` 和 ``persona_path`` 都指到临时目录，不依赖工作副本。
    """

    from dataclasses import replace

    from danmaku_pet import config as config_module
    from danmaku_pet.config import Config

    config = replace(Config.load(), persona_path=tmp_path / "app" / "data" / "persona.json")

    monkeypatch.setattr(config_module, "RESOURCE_ROOT", tmp_path / "empty-bundle")
    config.seed_persona()
    assert not config.persona_path.exists()

    bundled_root = tmp_path / "bundle"
    (bundled_root / "data").mkdir(parents=True)
    (bundled_root / "data" / "persona.json").write_text(
        '{"persona": {"name": "小鱼"}}', encoding="utf-8"
    )
    monkeypatch.setattr(config_module, "RESOURCE_ROOT", bundled_root)

    config.seed_persona()
    assert config.persona_path.read_text(encoding="utf-8") == '{"persona": {"name": "小鱼"}}'

    config.persona_path.write_text('{"persona": {"name": "改过的名字"}}', encoding="utf-8")
    before = config.persona_path.read_text(encoding="utf-8")
    config.seed_persona()
    assert config.persona_path.read_text(encoding="utf-8") == before
