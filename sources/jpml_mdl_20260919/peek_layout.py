"""Compare pypdf layout-mode extraction on the two primary reports (offline)."""
from pathlib import Path
from pypdf import PdfReader

HERE = Path(__file__).resolve().parent
for name in ('07_Pending_MDL_Dockets_By_MDL_Number-September-1-2026.pdf', '06_Pending_MDL_Dockets_By_District-September-1-2026.pdf'):
    reader = PdfReader(str(HERE / 'raw' / name))
    text = reader.pages[0].extract_text(extraction_mode='layout')
    print('=====', name)
    print(text[:3800])
