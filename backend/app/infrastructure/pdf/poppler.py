"""Extração de texto de PDF com poppler (`pdftotext -layout`). Nunca escreve no PDF."""

from __future__ import annotations

import subprocess
from pathlib import Path


class PdfExtractionError(RuntimeError):
    pass


class PopplerTextExtractor:
    def __init__(self, timeout_s: int = 900) -> None:
        self.timeout_s = timeout_s

    def extract_pages(self, path: Path) -> list[str]:
        try:
            proc = subprocess.run(
                ["pdftotext", "-layout", str(path), "-"], capture_output=True, timeout=self.timeout_s, check=False
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise PdfExtractionError(f"falha ao executar pdftotext: {exc}") from exc
        if proc.returncode != 0 and not proc.stdout:
            raise PdfExtractionError(f"pdftotext retornou {proc.returncode}: {proc.stderr.decode(errors='replace')[:200]}")
        text = proc.stdout.decode("utf-8", errors="replace")
        pages = text.split("\f")
        if pages and not pages[-1].strip():
            pages = pages[:-1]
        return pages


def pdf_page_count(path: Path) -> int:
    try:
        out = subprocess.run(["pdfinfo", str(path)], capture_output=True, timeout=120, check=False).stdout.decode(errors="replace")
    except (OSError, subprocess.TimeoutExpired):
        return 0
    for line in out.splitlines():
        if line.startswith("Pages:"):
            return int(line.split()[1])
    return 0
