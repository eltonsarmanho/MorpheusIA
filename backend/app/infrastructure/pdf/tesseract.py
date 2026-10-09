"""OCR de uma página com pdftoppm + tesseract (idioma português)."""

from __future__ import annotations

import csv
import io
import os
import subprocess
import tempfile
from pathlib import Path

from app.domain.ports import OcrResult


class TesseractOcr:
    def __init__(self, lang: str = "por", dpi: int = 250, timeout_s: int = 180) -> None:
        self.lang, self.dpi, self.timeout_s = lang, dpi, timeout_s

    def ocr_page(self, path: Path, page: int) -> OcrResult:
        env = {**os.environ, "OMP_THREAD_LIMIT": "1"}
        with tempfile.TemporaryDirectory(prefix="ocr_") as tmp:
            base = Path(tmp) / "p"
            subprocess.run(
                ["pdftoppm", "-r", str(self.dpi), "-gray", "-png", "-singlefile", "-f", str(page), "-l", str(page), str(path), str(base)],
                capture_output=True, timeout=self.timeout_s, check=True,
            )
            proc = subprocess.run(
                ["tesseract", str(base) + ".png", "stdout", "-l", self.lang, "--psm", "3", "tsv"],
                capture_output=True, timeout=self.timeout_s, env=env, check=True,
            )
        return parse_tsv(proc.stdout.decode("utf-8", errors="replace"))


def parse_tsv(tsv: str) -> OcrResult:
    reader = csv.DictReader(io.StringIO(tsv), delimiter="\t", quoting=csv.QUOTE_NONE)
    lines: dict[tuple[str, str, str, str], list[str]] = {}
    confs: list[float] = []
    for row in reader:
        text = (row.get("text") or "").strip()
        try:
            conf = float(row.get("conf") or -1)
        except ValueError:
            continue
        if conf < 0 or not text:
            continue
        key = (row["page_num"], row["block_num"], row["par_num"], row["line_num"])
        lines.setdefault(key, []).append(text)
        confs.append(conf)
    body = "\n".join(" ".join(words) for words in lines.values())
    return OcrResult(text=body, mean_confidence=sum(confs) / len(confs) if confs else 0.0)
