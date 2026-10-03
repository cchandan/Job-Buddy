"""CV upload: pull the text out of a PDF with pypdf. The file is read in memory and never saved."""
import io
import logging

from pypdf import PdfReader

MAX_PAGES = 10  # a CV is a page or two; this stops a huge document tying up the server
MIN_CHARS = 40

logging.getLogger("pypdf").setLevel(logging.ERROR)  # odd PDFs are noisy; we report our own message


def extract_text(data):
    """Return the text of a PDF. Raises ValueError with a message the visitor can act on."""
    if not data.lstrip()[:5].startswith(b"%PDF-"):
        raise ValueError("That file isn't a PDF. Please upload your CV as a PDF, or paste its text instead.")
    locked = False
    try:
        reader = PdfReader(io.BytesIO(data))
        locked = bool(reader.is_encrypted and not reader.decrypt(""))
        text = "" if locked else "\n".join((page.extract_text() or "") for page in reader.pages[:MAX_PAGES])
    except Exception:  # a damaged file can fail in many ways inside the parser
        raise ValueError("We couldn't read that PDF. Please try another copy, or paste the text instead.")
    if locked:
        raise ValueError("That PDF is password-protected. Please upload an unlocked copy, or paste the text instead.")
    text = "\n".join(line.rstrip() for line in text.splitlines()).strip()
    if len(text) < MIN_CHARS:
        raise ValueError("We couldn't find any text in that PDF (it may be a scan). Please paste your CV text instead.")
    return text
