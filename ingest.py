"""Import your notes into Jarvis's memory.

Usage:
    python ingest.py C:\\path\\to\\your\\notes

Supports .txt, .md and .pdf files (PDF needs: pip install pypdf).
Run it again anytime: unchanged files are skipped, edited files are re-imported.
"""
import sys
from pathlib import Path

import memory

TEXT_EXTS = {".txt", ".md"}
ALL_EXTS = TEXT_EXTS | {".pdf"}


def read_file(p: Path) -> str:
    if p.suffix.lower() == ".pdf":
        from pypdf import PdfReader

        reader = PdfReader(str(p))
        return "\n\n".join((page.extract_text() or "") for page in reader.pages)
    return p.read_text(encoding="utf-8", errors="ignore")


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return

    root = Path(sys.argv[1])
    if not root.exists():
        print(f"Folder not found: {root}")
        return

    files = [root] if root.is_file() else [p for p in root.rglob("*") if p.suffix.lower() in ALL_EXTS]
    if not files:
        print("No .txt, .md or .pdf files found.")
        return

    added_total = 0
    for p in files:
        key = str(p.resolve())
        mtime = p.stat().st_mtime
        if memory.get_mtime(key) == mtime:
            print(f"skip (unchanged)  {p.name}")
            continue
        try:
            text = read_file(p)
            chunks = memory.chunk_text(text)
            memory.remove_source(key)
            n = memory.add_chunks(key, chunks)
            memory.set_mtime(key, mtime)
            added_total += n
            print(f"imported {n:4d} chunks  {p.name}")
        except Exception as e:
            print(f"FAILED            {p.name}: {e}")

    print(f"\nDone. {added_total} new chunks added.")
    print(memory.stats())


if __name__ == "__main__":
    main()