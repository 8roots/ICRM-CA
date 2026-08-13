"""Download, verify, and extract the pinned PaddleOCR model artifacts.

Used at image build time (Dockerfile) and for local development. Downloads the
official tarballs from PaddleX's BOS mirror, refuses to proceed when a SHA-256
checksum mismatches, extracts each ``*_infer`` directory, and writes a
``.icrm_sha256`` sidecar that ``app.paddle_engine.verify_artifacts`` checks at
runtime so the worker fails startup instead of ever downloading models itself.

Usage: python scripts/download_models.py [destination_dir]
"""

import argparse
import hashlib
import shutil
import sys
import tarfile
import tempfile
import urllib.request
from pathlib import Path

from app.paddle_engine import MODEL_ARTIFACTS

BASE_URL = "https://paddle-model-ecology.bj.bcebos.com/paddlex/official_inference_model/paddle3.0.0"


def download_and_verify(destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    for artifact in MODEL_ARTIFACTS:
        model_dir = destination / f"{artifact.name}_infer"
        if _is_current(model_dir, artifact.sha256):
            print(f"{artifact.name}: already present and verified")
            continue
        url = f"{BASE_URL}/{artifact.name}_infer.tar"
        print(f"{artifact.name}: downloading {url}")
        with tempfile.NamedTemporaryFile(suffix=".tar") as archive:
            with urllib.request.urlopen(url, timeout=300) as response:
                shutil.copyfileobj(response, archive)
            archive.flush()
            digest = _sha256(archive.name)
            if digest != artifact.sha256:
                raise SystemExit(
                    f"checksum mismatch for {artifact.name}: expected "
                    f"{artifact.sha256}, got {digest}"
                )
            if model_dir.exists():
                shutil.rmtree(model_dir)
            with tarfile.open(archive.name) as tar:
                tar.extractall(destination, filter="data")
        model_dir.joinpath(".icrm_sha256").write_text(artifact.sha256 + "\n")
        print(f"{artifact.name}: verified and extracted to {model_dir}")


def _is_current(model_dir: Path, expected: str) -> bool:
    sidecar = model_dir / ".icrm_sha256"
    return sidecar.is_file() and sidecar.read_text().strip() == expected


def _sha256(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("destination", nargs="?", default="models", type=Path)
    args = parser.parse_args(argv)
    download_and_verify(args.destination)


if __name__ == "__main__":
    main(sys.argv[1:])
