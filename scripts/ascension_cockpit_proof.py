"""Start a self-contained NEXUS stack and prove the rendered cockpit controls.

The browser proof drives visible UI controls only. This harness supplies the
actual local API, worker, frontend, owner, and bounded filesystem root.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import uuid

from operator_runtime_proof import FRONTEND_URL, ROOT, start, stop


def run() -> dict:
    with tempfile.TemporaryDirectory(prefix="nexus-ascension-cockpit-") as temporary:
        root = Path(temporary)
        password = f"AscensionProof-{uuid.uuid4().hex}-secure"
        email = "owner@ascension-proof.local"
        env = {
            **os.environ,
            "PYTHONPATH": str(ROOT),
            "NEXUS_PRODUCT_ROOT": str(ROOT),
            "NEXUS_DATA_ROOT": str(root / "data"),
            "NEXUS_ALLOWED_FILESYSTEM_ROOT": str(ROOT),
            "NEXUS_ALLOW_REAL_READS": "true",
            "NEXUS_WEB_ORIGINS": FRONTEND_URL,
        }
        subprocess.run([sys.executable, "-m", "nexus_independent.cli", "bootstrap", "--email", email, "--password", password, "--tenant", "Ascension Cockpit", "--project-id", "local"], cwd=ROOT, env=env, check=True, capture_output=True, text=True)
        processes = start(env, root / "logs")
        try:
            result = subprocess.run(["node", "scripts/operator_ui_proof.mjs"], cwd=ROOT, env={**env, "NEXUS_UI_PROOF_URL": FRONTEND_URL, "NEXUS_UI_PROOF_EMAIL": email, "NEXUS_UI_PROOF_PASSWORD": password}, check=True, capture_output=True, text=True, timeout=90)
            return json.loads(result.stdout)
        finally:
            stop(processes.all())


if __name__ == "__main__":
    print(json.dumps(run(), indent=2))
