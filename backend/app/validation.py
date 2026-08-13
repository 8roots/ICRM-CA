import codecs
import re
import zipfile
from pathlib import Path

from app.material_formats import FORMATS, MANUAL_EXTENSIONS
from app.models import Document


class ValidationError(Exception):
    def __init__(self, code: str, *, manual_handling: bool = False) -> None:
        self.code = code
        self.manual_handling = manual_handling


def validate_package(stream, root: str) -> None:
    stream.seek(0)
    try:
        with zipfile.ZipFile(stream) as package:
            names = set(package.namelist())
    except zipfile.BadZipFile:
        raise ValidationError("signature_mismatch") from None
    if "EncryptedPackage" in names:
        raise ValidationError("encrypted_input", manual_handling=True)
    if any(name.lower().endswith("vbaproject.bin") for name in names):
        raise ValidationError("unsupported_macro", manual_handling=True)
    if "[Content_Types].xml" not in names or root not in names:
        raise ValidationError("signature_mismatch")


def chunks(stream, size: int = 1024 * 1024):
    while chunk := stream.read(size):
        yield chunk


def _validate_text_encoding(stream) -> None:
    """Text materials must decode as UTF-8 or GB18030, never contain NUL bytes.

    GB18030 covers the common Chinese spreadsheet exports that Excel saves
    without a BOM; the parser records which encoding was actually detected.
    """

    for encoding in ("utf-8-sig", "gb18030"):
        stream.seek(0)
        decoder = codecs.getincrementaldecoder(encoding)()
        try:
            for content in chunks(stream):
                if b"\x00" in content:
                    raise ValidationError("signature_mismatch")
                decoder.decode(content)
            decoder.decode(b"", final=True)
            return
        except UnicodeDecodeError:
            continue
    raise ValidationError("signature_mismatch") from None


def validate(document: Document, stream) -> None:
    extension = Path(document.filename).suffix.lower()
    manual_error = MANUAL_EXTENSIONS.get(extension)
    if manual_error:
        raise ValidationError(manual_error, manual_handling=True)
    material_format = FORMATS.get(extension)
    if not material_format or document.declared_mime not in material_format.mimes:
        raise ValidationError("mime_mismatch")

    prefix = stream.read(8)
    if extension == ".pdf":
        if not prefix.startswith(b"%PDF-"):
            raise ValidationError("signature_mismatch")
        tail = prefix
        for chunk in chunks(stream):
            tail = (tail + chunk)[-2 * 1024 * 1024 :]
        trailers = re.findall(rb"trailer\s*<<(.*?)>>", tail, re.DOTALL)
        xref_streams = re.findall(
            rb"\d+\s+\d+\s+obj\s*<<(.*?/Type\s*/XRef.*?)>>\s*stream",
            tail,
            re.DOTALL,
        )
        dictionaries = trailers[-1:] + xref_streams
        if any(re.search(rb"/Encrypt\s+\d+\s+\d+\s+R", item) for item in dictionaries):
            raise ValidationError("encrypted_input", manual_handling=True)
    elif material_format.package_root:
        if prefix.startswith(b"\xd0\xcf\x11\xe0"):
            raise ValidationError("encrypted_input", manual_handling=True)
        validate_package(stream, material_format.package_root)
    elif extension in {".csv", ".md", ".markdown"}:
        _validate_text_encoding(stream)
    elif extension == ".png" and not prefix.startswith(b"\x89PNG\r\n\x1a\n"):
        raise ValidationError("signature_mismatch")
    elif extension in {".jpg", ".jpeg"} and not prefix.startswith(b"\xff\xd8\xff"):
        raise ValidationError("signature_mismatch")
    elif extension in {".tif", ".tiff"} and not prefix.startswith((b"II*\x00", b"MM\x00*")):
        raise ValidationError("signature_mismatch")
