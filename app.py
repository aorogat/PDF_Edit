"""Simple one-page PDF compression web app.

Uses PyMuPDF + Pillow to re-encode embedded images at lower quality, which
is the only way to meaningfully shrink scanned / image-heavy PDFs. A final
pass also does lossless stream + object cleanup.
"""
from __future__ import annotations

import io
import os
import uuid
from pathlib import Path

import fitz  # PyMuPDF
from flask import Flask, jsonify, render_template, request, send_file
from PIL import Image

# Allow large scanned pages without Pillow refusing to decode them.
Image.MAX_IMAGE_PIXELS = None

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 50 * 1024 * 1024  # 50 MB upload cap

TMP_DIR = Path(os.environ.get("TEMP", "/tmp")) / "pdf-compressor"
TMP_DIR.mkdir(parents=True, exist_ok=True)

# Quality presets: JPEG quality + max image dimension (pixels on the long side).
# Lower values = smaller files, more visible artifacts.
QUALITY_PRESETS: dict[str, dict[str, int]] = {
    "light":   {"jpeg_quality": 75, "max_dim": 2200},
    "medium":  {"jpeg_quality": 55, "max_dim": 1700},
    "strong":  {"jpeg_quality": 38, "max_dim": 1300},
    "extreme": {"jpeg_quality": 15, "max_dim": 900},
}
DEFAULT_LEVEL = "medium"


def _recompress_image(image_bytes: bytes, jpeg_quality: int, max_dim: int) -> bytes | None:
    """Re-encode an embedded image as JPEG. Returns new bytes or None to skip."""
    try:
        pil = Image.open(io.BytesIO(image_bytes))
        pil.load()
    except Exception:
        return None

    # Skip tiny images (icons, logos) - not worth touching.
    if max(pil.size) < 200:
        return None

    # Flatten alpha onto a white background; convert exotic modes to RGB.
    if pil.mode in ("RGBA", "LA") or (pil.mode == "P" and "transparency" in pil.info):
        rgba = pil.convert("RGBA")
        bg = Image.new("RGB", rgba.size, (255, 255, 255))
        bg.paste(rgba, mask=rgba.split()[-1])
        pil = bg
    elif pil.mode == "CMYK":
        pil = pil.convert("RGB")
    elif pil.mode not in ("RGB", "L"):
        pil = pil.convert("RGB")

    # Downsample oversized images.
    if max(pil.size) > max_dim:
        ratio = max_dim / max(pil.size)
        new_size = (
            max(1, int(pil.size[0] * ratio)),
            max(1, int(pil.size[1] * ratio)),
        )
        pil = pil.resize(new_size, Image.LANCZOS)

    buf = io.BytesIO()
    pil.save(buf, format="JPEG", quality=jpeg_quality, optimize=True, progressive=True)
    return buf.getvalue()


def compress_pdf(src_bytes: bytes, level: str) -> tuple[bytes, int, int]:
    """Compress a PDF and return (data, original_size, compressed_size)."""
    preset = QUALITY_PRESETS.get(level, QUALITY_PRESETS[DEFAULT_LEVEL])
    jpeg_quality = preset["jpeg_quality"]
    max_dim = preset["max_dim"]

    original_size = len(src_bytes)
    doc = fitz.open(stream=src_bytes, filetype="pdf")

    seen_xrefs: set[int] = set()
    for page in doc:
        for info in page.get_images(full=True):
            xref = info[0]
            if xref in seen_xrefs:
                continue
            seen_xrefs.add(xref)

            try:
                base = doc.extract_image(xref)
            except Exception:
                continue

            original_image = base.get("image")
            if not original_image:
                continue

            new_image = _recompress_image(original_image, jpeg_quality, max_dim)
            if not new_image or len(new_image) >= len(original_image):
                continue

            try:
                page.replace_image(xref, stream=new_image)
            except Exception:
                # If a specific image can't be replaced (rare formats, masks
                # PyMuPDF dislikes, etc.) just skip it.
                continue

    out_bytes = doc.tobytes(
        garbage=4,
        deflate=True,
        deflate_images=True,
        deflate_fonts=True,
        clean=True,
    )
    doc.close()

    # If our pass somehow made the file bigger, return the original.
    if len(out_bytes) >= original_size:
        return src_bytes, original_size, original_size

    return out_bytes, original_size, len(out_bytes)


@app.route("/")
def index():
    return render_template("index.html")


@app.post("/compress")
def compress():
    if "file" not in request.files:
        return jsonify({"error": "No file uploaded"}), 400

    upload = request.files["file"]
    if not upload.filename:
        return jsonify({"error": "No file selected"}), 400

    if not upload.filename.lower().endswith(".pdf"):
        return jsonify({"error": "Only PDF files are supported"}), 400

    level = (request.form.get("level") or DEFAULT_LEVEL).lower()
    if level not in QUALITY_PRESETS:
        level = DEFAULT_LEVEL

    try:
        src_bytes = upload.stream.read()
        data, original_size, compressed_size = compress_pdf(src_bytes, level)
    except fitz.FileDataError as exc:
        return jsonify({"error": f"Invalid PDF: {exc}"}), 400
    except Exception as exc:  # noqa: BLE001 - surface unexpected errors to UI
        return jsonify({"error": f"Compression failed: {exc}"}), 500

    token = uuid.uuid4().hex
    out_path = TMP_DIR / f"{token}.pdf"
    out_path.write_bytes(data)

    saved = original_size - compressed_size
    ratio = (saved / original_size * 100) if original_size else 0.0

    return jsonify(
        {
            "token": token,
            "original_name": upload.filename,
            "original_size": original_size,
            "compressed_size": compressed_size,
            "saved_bytes": saved,
            "saved_percent": round(ratio, 2),
            "level": level,
        }
    )


@app.get("/download/<token>")
def download(token: str):
    # Guard against path traversal: token must be a plain hex string
    if not token.isalnum() or len(token) > 64:
        return jsonify({"error": "Invalid token"}), 400

    name = request.args.get("name", "file.pdf")
    safe_name = Path(name).name  # strip any directories
    if safe_name.lower().endswith(".pdf"):
        safe_name = safe_name[:-4] + "_compressed.pdf"
    else:
        safe_name = safe_name + "_compressed.pdf"

    path = TMP_DIR / f"{token}.pdf"
    if not path.exists():
        return jsonify({"error": "File expired or not found"}), 404

    response = send_file(
        path,
        mimetype="application/pdf",
        as_attachment=True,
        download_name=safe_name,
    )

    @response.call_on_close
    def _cleanup():
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass

    return response


@app.errorhandler(413)
def too_large(_):
    return jsonify({"error": "File is too large (max 50 MB)"}), 413


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=True)
