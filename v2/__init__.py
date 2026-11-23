"""Isolated FNOL user-delegated deployment."""

import hashlib
from pathlib import Path

RUNTIME_MODULES = (
    "__init__.py", "wsgi.py", "app.py", "auth.py", "settings.py", "state.py", "runtime.py",
    "links.py", "email_processing.py", "escalation.py", "recovery.py",
)


def runtime_digest():
    root = Path(__file__).parent
    files = [root / name for name in RUNTIME_MODULES]
    for folder, pattern in (("templates", "*.html"), ("static", "*.js"), ("static", "*.css")):
        files.extend((root / folder).glob(pattern))
    digest = hashlib.sha256()
    for path in sorted(files):
        digest.update(path.relative_to(root).as_posix().encode() + b"\0")
        digest.update(hashlib.sha256(path.read_bytes()).digest())
    return digest.hexdigest()
