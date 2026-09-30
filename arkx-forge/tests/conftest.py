import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A throwaway git repository with a tiny Python project in it."""
    root = tmp_path / "proj"
    (root / "src").mkdir(parents=True)
    (root / "src" / "app.py").write_text("def add(a, b):\n    return a + b\n")
    (root / "README.md").write_text("# demo\n")
    for args in (["init", "-q", "-b", "main"], ["add", "."],
                 ["-c", "user.email=t@e.st", "-c", "user.name=T",
                  "commit", "-q", "-m", "initial"]):
        subprocess.run(["git", *args], cwd=root, check=True,
                       capture_output=True)
    return root
