"""Tests for shared.files — repo-file eligibility for the executor."""

from shared.files import extension, is_editable


def test_source_files_are_editable():
    assert is_editable("src/main.py")
    assert is_editable("README.md")
    assert is_editable("app/components/Button.tsx")
    assert is_editable("Makefile")


def test_binary_and_media_extensions_skipped():
    for path in ("logo.png", "clip.mp4", "font.woff2", "archive.zip", "x.pyc"):
        assert not is_editable(path)


def test_lockfiles_skipped():
    assert not is_editable("package-lock.json")
    assert not is_editable("poetry.lock")
    assert not is_editable("frontend/yarn.lock")


def test_minified_assets_skipped():
    assert not is_editable("vendor/jquery.min.js")
    assert not is_editable("dist/app.min.css")
    assert not is_editable("bundle.js.map")


def test_vendored_and_generated_dirs_skipped():
    assert not is_editable("node_modules/react/index.js")
    assert not is_editable("dist/index.js")
    assert not is_editable("build/output.py")
    assert not is_editable(".venv/lib/python3.12/site.py")


def test_extension_helper():
    assert extension("a/b/c.PY") == ".py"
    assert extension("noext") == ""
    assert extension("dir.with.dots/file.tsx") == ".tsx"
    assert extension("Dockerfile") == ""
