from __future__ import annotations

import hashlib
import tarfile
from pathlib import Path

import httpx


ARXIV_EPRINT = "https://arxiv.org/e-print/{}"


def normalize_id(arxiv_id: str) -> str:
    return arxiv_id.strip().removeprefix("https://arxiv.org/abs/").split("v")[0]


async def download(arxiv_id: str, cache_dir: Path) -> Path:
    arxiv_id = normalize_id(arxiv_id)
    dest = cache_dir / "raw" / arxiv_id
    if dest.exists():
        return dest

    dest.mkdir(parents=True, exist_ok=True)
    url = ARXIV_EPRINT.format(arxiv_id)

    async with httpx.AsyncClient(follow_redirects=True, timeout=60) as client:
        response = await client.get(url)
        response.raise_for_status()
        content_type = response.headers.get("content-type", "")
        data = response.content

    tarball = dest / "source.tar.gz"

    if "pdf" in content_type or data[:4] == b"%PDF":
        (dest / "source.pdf").write_bytes(data)
        return dest

    tarball.write_bytes(data)

    try:
        with tarfile.open(tarball) as tf:
            tf.extractall(dest)
    except tarfile.TarError:
        # single .tex file wrapped in gzip
        import gzip
        with gzip.open(tarball) as gz:
            (dest / "main.tex").write_bytes(gz.read())

    return dest


def find_main_tex(source_dir: Path) -> Path | None:
    tex_files = list(source_dir.glob("*.tex"))
    if not tex_files:
        tex_files = list(source_dir.rglob("*.tex"))
    if not tex_files:
        return None
    for candidate in tex_files:
        text = candidate.read_text(errors="ignore")
        if r"\documentclass" in text:
            return candidate
    return tex_files[0]
