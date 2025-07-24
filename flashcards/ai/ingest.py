# ingest.py  – safe for str or pathlib.Path
"""
Turn PDFs, Word docs, or images into plain-text strings.
"""
from pathlib import Path
import fitz          # PyMuPDF
import docx          # python-docx
# import pytesseract
# from PIL import Image

print("in ingest")

def extract_text(path) -> str:
    """
    Accepts str or pathlib.Path.  Detects file type by extension,
    extracts visible text, and returns one big string.
    """
    p = Path(path)            # normalise to Path object
    suffix = p.suffix.lower() # '.pdf', '.docx', '.png', ...

    if suffix == ".pdf":                              # ── PDF
        with fitz.open(p) as pdf:
            return "\n".join(page.get_text() for page in pdf)

    elif suffix in {".docx", ".doc"}:                 # ── DOCX
        doc = docx.Document(p)
        return "\n".join(par.text for par in doc.paragraphs)

    # elif suffix in {".png", ".jpg", ".jpeg", ".tiff", ".bmp"}:  # ── Image
    #     img = Image.open(p)
    #     return pytesseract.image_to_string(img)
    elif suffix == ".txt":                         # ── Plain text
        return p.read_text(encoding="utf-8", errors="ignore")

    raise ValueError(f"Unsupported file type: {suffix}")
