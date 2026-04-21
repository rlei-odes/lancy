from markitdown import MarkItDown  # type: ignore[import-untyped]

from conversational_toolkit.chunking.pdf_chunker import MarkdownConverterEngine, PDFChunker


class MarkItDownChunker(PDFChunker):
    """Chunks DOCX, DOC, EPUB and other MarkItDown-supported formats using
    the same header-based splitting logic as PDFChunker, but bypassing the
    PDF/Docling pipeline entirely.

    Supported extensions: .docx, .doc, .epub
    """

    def _pdf2markdown(
        self,
        file_path: str,
        engine: MarkdownConverterEngine = MarkdownConverterEngine.MARKITDOWN,
        write_images: bool = False,
        image_path: str | None = None,
        do_ocr: bool = False,
    ) -> str:
        result = MarkItDown().convert(file_path)
        return str(result.text_content)

    def make_chunks(self, file_path: str, **kwargs):  # type: ignore[override]
        # Disable image extraction — not supported for these formats
        return super().make_chunks(file_path, write_images=False, image_path=None)
