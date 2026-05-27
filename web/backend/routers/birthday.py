"""Birthday lottery page – serves the uploaded prize PDF."""

import os
from pathlib import Path

from fastapi import APIRouter, UploadFile, File, HTTPException
from fastapi.responses import FileResponse

router = APIRouter(prefix="/api/birthday", tags=["birthday"])

PDF_DIR = Path(__file__).resolve().parent.parent / "birthday_assets"
PDF_PATH = PDF_DIR / "prize.pdf"


@router.get("/pdf")
async def get_prize_pdf():
    """Return the uploaded prize PDF."""
    if not PDF_PATH.exists():
        raise HTTPException(status_code=404, detail="Prize PDF not uploaded yet")
    return FileResponse(
        PDF_PATH,
        media_type="application/pdf",
        filename="happy_birthday_dr_leeven.pdf",
    )


@router.post("/upload-pdf")
async def upload_prize_pdf(file: UploadFile = File(...)):
    """Upload / replace the prize PDF."""
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files are accepted")
    PDF_DIR.mkdir(parents=True, exist_ok=True)
    contents = await file.read()
    PDF_PATH.write_bytes(contents)
    return {"status": "ok", "size_bytes": len(contents)}
