import io
import csv
from typing import Tuple


def excel_to_text(file_bytes: bytes) -> str:
    try:
        import openpyxl

        wb = openpyxl.load_workbook(io.BytesIO(file_bytes), read_only=True, data_only=True)
        ws = wb.active
        rows = []
        for row in ws.iter_rows(values_only=True):
            clean = [str(c).strip() if c is not None else "" for c in row]
            if any(c for c in clean):
                rows.append(",".join(clean))
        wb.close()
        return "\n".join(rows)
    except Exception as e:
        print(f"[FileProcessor] Excel error: {e }")
        return ""


def csv_to_text(file_bytes: bytes) -> str:
    for enc in ("utf-8", "utf-8-sig", "latin-1", "cp1252"):
        try:
            return file_bytes.decode(enc)
        except UnicodeDecodeError:
            continue
    return file_bytes.decode("utf-8", errors="replace")


def process_upload(filename: str, file_bytes: bytes) -> Tuple[str, str]:
    name_lower = filename.lower()
    if name_lower.endswith((".xlsx", ".xls")):
        return excel_to_text(file_bytes), "excel"
    elif name_lower.endswith((".csv", ".txt")):
        return csv_to_text(file_bytes), "csv"
    elif name_lower.endswith((".jpg", ".jpeg", ".png", ".webp", ".heic")):
        return "", "image"
    else:
        return csv_to_text(file_bytes), "text"


def extract_image_mime(filename: str) -> str:
    ext = filename.lower().split(".")[-1]
    return {
        "jpg": "image/jpeg",
        "jpeg": "image/jpeg",
        "png": "image/png",
        "webp": "image/webp",
        "heic": "image/heic",
    }.get(ext, "image/jpeg")
