"""filehelper — upload handling. Stores under /opt/sjweb/uploads/ by
default; override with SJWEB_UPLOAD_DIR."""
import os
from werkzeug.utils import secure_filename


_UPLOAD_DIR = os.environ.get("SJWEB_UPLOAD_DIR", "/opt/sjweb/uploads")


class FileHelper:
    def __init__(self, base: str | None = None) -> None:
        self.base = base or _UPLOAD_DIR
        os.makedirs(self.base, exist_ok=True)

    def save_upload(self, fs, filename: str | None = None) -> str:
        name = secure_filename(filename or fs.filename or "upload.png")
        full = os.path.join(self.base, name)
        fs.save(full)
        return full
