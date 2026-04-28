#!/usr/bin/env python3
"""Print image dimensions for all images found in PDF files.

Useful for calibrating the _MIN_CAPTION_IMAGE_AREA threshold in ingestion.py.

Usage:
    python scripts/inspect_images.py path/to/file.pdf [more.pdf ...] [dir/]
    python scripts/inspect_images.py data/
    python scripts/inspect_images.py data/ --save /tmp/extracted_images

Only PDFs are supported (docling engine, OCR disabled for speed).

Note: docling only extracts elements it classifies as figures/pictures. Background
images and full-bleed page decorations are typically not captured. Use --save to
inspect exactly what is being extracted.
"""

import sys
from pathlib import Path


def find_pdfs(paths: list[Path]) -> list[Path]:
    result: list[Path] = []
    for p in paths:
        if p.is_dir():
            result.extend(sorted(p.rglob("*.pdf")))
        elif p.suffix.lower() == ".pdf":
            result.append(p)
        else:
            print(f"[skip] {p} — only PDFs supported", file=sys.stderr)
    return result


def extract_images(pdf_path: Path, save_dir: Path | None) -> list[tuple[int, int]]:
    """Return list of (width, height) for each picture in the PDF.

    If save_dir is set, each image is written there as <stem>_NNN.png.
    """
    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import PdfPipelineOptions
    from docling.document_converter import DocumentConverter, PdfFormatOption
    from docling_core.types.doc.document import PictureItem

    pipeline_options = PdfPipelineOptions()
    pipeline_options.do_ocr = False
    pipeline_options.generate_picture_images = True

    converter = DocumentConverter(
        format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline_options)}
    )
    result = converter.convert(str(pdf_path))
    sizes: list[tuple[int, int]] = []
    counter = 0
    for element, _ in result.document.iterate_items():
        if isinstance(element, PictureItem) and element.image and element.image.pil_image:
            img = element.image.pil_image
            sizes.append(img.size)
            if save_dir is not None:
                counter += 1
                out = save_dir / f"{pdf_path.stem}_{counter:03d}.png"
                img.save(out, format="PNG")
    return sizes


def main() -> None:
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        sys.exit(1)

    save_dir: Path | None = None
    if "--save" in args:
        idx = args.index("--save")
        if idx + 1 >= len(args):
            print("--save requires a directory argument", file=sys.stderr)
            sys.exit(1)
        save_dir = Path(args[idx + 1])
        save_dir.mkdir(parents=True, exist_ok=True)
        args = args[:idx] + args[idx + 2:]

    pdfs = find_pdfs([Path(a) for a in args])
    if not pdfs:
        print("No PDF files found.", file=sys.stderr)
        sys.exit(1)

    if save_dir:
        print(f"Saving extracted images to: {save_dir}")

    all_areas: list[int] = []

    for pdf in pdfs:
        print(f"\n{pdf}")
        print(f"  {'#':<5} {'width':>6} {'height':>7} {'area':>10}  note")
        print(f"  {'-'*5} {'-'*6} {'-'*7} {'-'*10}  ----")
        sizes = extract_images(pdf, save_dir)
        if not sizes:
            print("  (no images found)")
            continue
        for i, (w, h) in enumerate(sizes, 1):
            area = w * h
            all_areas.append(area)
            note = ""
            if area < 10_000:
                note = "< 10k — likely decorative"
            elif area < 50_000:
                note = "< 50k — possibly logo/icon"
            print(f"  {i:<5} {w:>6} {h:>7} {area:>10,}  {note}")

    if not all_areas:
        return

    print(f"\n--- summary ({len(all_areas)} image(s) across {len(pdfs)} file(s)) ---")

    thresholds = [1_000, 5_000, 10_000, 25_000, 50_000, 100_000]
    print(f"\n  {'threshold':>12}  {'filtered':>8}  {'remaining':>10}")
    print(f"  {'-'*12}  {'-'*8}  {'-'*10}")
    for t in thresholds:
        filtered = sum(1 for a in all_areas if a < t)
        print(f"  {t:>12,}  {filtered:>8} ({filtered * 100 // len(all_areas):2d}%)  {len(all_areas) - filtered:>10}")

    areas_sorted = sorted(all_areas)
    print(f"\n  min area : {areas_sorted[0]:,} px²")
    print(f"  median   : {areas_sorted[len(areas_sorted) // 2]:,} px²")
    print(f"  max area : {areas_sorted[-1]:,} px²")


if __name__ == "__main__":
    main()
