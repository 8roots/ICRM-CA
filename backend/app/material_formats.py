from dataclasses import dataclass


@dataclass(frozen=True)
class MaterialFormat:
    mimes: frozenset[str]
    package_root: str | None = None


FORMATS = {
    ".pdf": MaterialFormat(frozenset({"application/pdf"})),
    ".docx": MaterialFormat(
        frozenset({"application/vnd.openxmlformats-officedocument.wordprocessingml.document"}),
        "word/document.xml",
    ),
    ".xlsx": MaterialFormat(
        frozenset({"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"}),
        "xl/workbook.xml",
    ),
    ".csv": MaterialFormat(frozenset({"text/csv", "application/csv", "text/plain"})),
    ".md": MaterialFormat(frozenset({"text/markdown", "text/plain"})),
    ".markdown": MaterialFormat(frozenset({"text/markdown", "text/plain"})),
    ".png": MaterialFormat(frozenset({"image/png"})),
    ".jpg": MaterialFormat(frozenset({"image/jpeg"})),
    ".jpeg": MaterialFormat(frozenset({"image/jpeg"})),
    ".tif": MaterialFormat(frozenset({"image/tiff"})),
    ".tiff": MaterialFormat(frozenset({"image/tiff"})),
}

MANUAL_EXTENSIONS = {
    ".doc": "unsupported_legacy_office",
    ".xls": "unsupported_legacy_office",
    ".docm": "unsupported_macro",
    ".xlsm": "unsupported_macro",
    ".zip": "unsupported_archive",
    ".rar": "unsupported_archive",
    ".7z": "unsupported_archive",
}
