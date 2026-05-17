"""File-extension / path filtering for the executor's random file picker.

The executor picks a random *blob* from the repo tree and asks Bedrock to edit
it. We exclude things that aren't safe to edit (binary blobs, lockfiles,
generated code, vendored deps) so the LLM is only ever pointed at human
source files.
"""

SKIP_EXTENSIONS: frozenset[str] = frozenset(
    {
        # Images / media
        ".png",
        ".jpg",
        ".jpeg",
        ".gif",
        ".bmp",
        ".tiff",
        ".ico",
        ".webp",
        ".svg",
        ".mp3",
        ".mp4",
        ".mov",
        ".avi",
        ".wav",
        ".flac",
        ".webm",
        # Documents / archives
        ".pdf",
        ".zip",
        ".gz",
        ".tar",
        ".tgz",
        ".rar",
        ".7z",
        # Fonts
        ".ttf",
        ".otf",
        ".woff",
        ".woff2",
        ".eot",
        # Lockfiles / minified
        ".lock",
        ".min.js",
        ".min.css",
        ".map",
        # Compiled binaries
        ".exe",
        ".dll",
        ".so",
        ".dylib",
        ".bin",
        ".dat",
        ".pyc",
        ".class",
        ".o",
        ".obj",
    }
)

SKIP_BASENAMES: frozenset[str] = frozenset(
    {
        "package-lock.json",
        "yarn.lock",
        "pnpm-lock.yaml",
        "poetry.lock",
        "Pipfile.lock",
        "Cargo.lock",
        "Gemfile.lock",
        "go.sum",
        "composer.lock",
    }
)

SKIP_PATH_PARTS: tuple[str, ...] = (
    "node_modules",
    "dist",
    "build",
    ".git",
    ".venv",
    "venv",
    "__pycache__",
    ".next",
    "target",
    ".idea",
    ".vscode",
    "vendor",
)


def is_editable(path: str) -> bool:
    p = path.lower()
    parts = p.split("/")
    if any(part in SKIP_PATH_PARTS for part in parts):
        return False
    base = parts[-1] if parts else p
    if base in SKIP_BASENAMES:
        return False
    for ext in SKIP_EXTENSIONS:
        if base.endswith(ext):
            return False
    return True


def extension(path: str) -> str:
    base = path.rsplit("/", 1)[-1].lower()
    if "." not in base:
        return ""
    return "." + base.rsplit(".", 1)[1]
