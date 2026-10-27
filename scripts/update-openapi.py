from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.config import AppConfig  # noqa: E402
from app.main import create_app  # noqa: E402


def main() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        app = create_app(
            AppConfig(
                data_dir=root / "data",
                secret_key="x" * 48,
                admin_username="admin",
                admin_password="admin-password-for-openapi",
                static_dir=root / "static",
            ),
            background_tasks=False,
        )
        snapshot = app.openapi()
    target = ROOT / "docs" / "api" / "openapi.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(snapshot, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(f"wrote {len(snapshot['paths'])} paths to {target.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
