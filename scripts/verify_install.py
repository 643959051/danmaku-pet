"""Verify that the project runtime and assets are inside this workspace."""

from __future__ import annotations

import json
import site
import subprocess
import sys
from importlib import metadata
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXPECTED_PREFIX = (ROOT / ".conda" / "env").resolve()


def is_inside(path: Path) -> bool:
    try:
        path.resolve().relative_to(ROOT)
        return True
    except ValueError:
        return False


def main() -> int:
    prefix = Path(sys.prefix).resolve()
    packages: list[dict[str, str]] = []
    outside: list[str] = []
    for dist in metadata.distributions():
        try:
            location = Path(dist.locate_file("")).resolve()
        except Exception:
            continue
        name = dist.metadata.get("Name", "?")
        version = dist.version or "?"
        packages.append({"name": name, "version": version, "location": str(location)})
        if not is_inside(location):
            outside.append(f"{name}=={version} @ {location}")

    site_paths = [Path(p) for p in site.getsitepackages()]
    site_packages = next(
        (path for path in site_paths if path.name == "site-packages"),
        site_paths[0] if site_paths else None,
    )
    expected = {
        "conda_prefix": EXPECTED_PREFIX,
        "python": Path(sys.executable),
        "site_packages": site_packages,
        "dafeiyu_package": ROOT / "vendor" / "dafeiyu" / "package",
        "dafeiyu_helper": ROOT / "vendor" / "dafeiyu" / "package" / "runtime" / "helper.py",
        "dafeiyu_assets": ROOT / "vendor" / "dafeiyu" / "package" / "assets",
        "tts_cache": ROOT / "assets" / "tts_cache",
        "temp": ROOT / "temp",
        "pip_cache": ROOT / ".cache" / "pip",
        "npm_cache": ROOT / ".cache" / "npm",
        "conda_pkgs": ROOT / ".cache" / "conda-pkgs",
    }
    checks = {
        name: {
            "path": str(path) if path else None,
            "exists": path.exists() if path else False,
            "inside_workspace": is_inside(path) if path else False,
        }
        for name, path in expected.items()
    }
    for name in ("python", "site_packages"):
        checks[name]["inside_workspace"] = is_inside(expected[name]) if expected[name] else False

    pip_check = subprocess.run(
        [sys.executable, "-m", "pip", "check"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    report = {
        "workspace": str(ROOT),
        "python_executable": sys.executable,
        "python_version": sys.version,
        "sys_prefix": str(prefix),
        "prefix_is_project_env": prefix == EXPECTED_PREFIX,
        "user_site": site.getusersitepackages(),
        "outside_workspace_distributions": outside,
        "expected_locations": checks,
        "installed_package_count": len(packages),
        "key_packages": sorted(
            [p for p in packages if p["name"].lower() in {
                "fastapi", "uvicorn", "httpx", "pydantic", "edge-tts", "pygame", "pyside6", "pytest"
            }],
            key=lambda item: item["name"].lower(),
        ),
        "pip_check_ok": pip_check.returncode == 0,
        "pip_check_output": (pip_check.stdout + pip_check.stderr).strip(),
        "all_expected_inside": all(item["inside_workspace"] for item in checks.values()),
        "all_distributions_inside": not outside,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["all_expected_inside"] and report["all_distributions_inside"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
