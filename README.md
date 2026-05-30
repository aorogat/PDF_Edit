# PDF Compressor

A tiny one-page web app that compresses PDF files. Built with Flask,
[PyMuPDF](https://pymupdf.readthedocs.io/), and Pillow.

## Features

- Single-page UI with drag & drop or click-to-upload
- Three compression levels: **Light** / **Medium** / **Strong**
- Re-encodes embedded images as JPEG (with downsampling) so scanned /
  image-heavy PDFs actually shrink, then runs a lossless cleanup pass
- Shows original, compressed, and saved sizes
- One-click download of the compressed file
- 50 MB upload limit

## Run locally

Requires Python 3.10+.

```powershell
cd pdf-compressor
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python app.py
```

Then open <http://127.0.0.1:5000>.

## Compression levels

| Level    | JPEG quality | Max image dimension | When to use                          |
| -------- | ------------ | ------------------- | ------------------------------------ |
| Light    | 75           | 2200 px             | Keep things crisp; modest savings    |
| Medium   | 55           | 1700 px             | Good default for scans / passports   |
| Strong   | 38           | 1300 px             | Smallest file; visible JPEG artifacts |

## Notes

- Compression is **lossy** for image-heavy PDFs (it re-encodes embedded
  images as JPEG and may downsample them). If a recompressed image ends up
  larger than the original it's left untouched.
- Text-only PDFs typically have nothing to recompress, so savings will be
  small. In that case the original file is returned.
- Uploaded files are processed in memory; the compressed result is stored
  briefly in your system temp dir and deleted as soon as it's downloaded.
