"""Intent: an email is indexed as both a message and its attachments.

An email carries two kinds of content, and indexing either one alone loses
something: only the body drops the documents people actually attached, only the
attachments drops who sent them and what they said.

So `EmailChunker` emits the message as one chunk and hands every attachment to the
chunker registered for that attachment's own file type. The contract:

  - the message chunk carries the readable headers, so sender and subject are
    searchable text and not just metadata
  - every chunk from one email — attachments included — carries the email's headers,
    so a hit deep inside an attachment can still name the message it arrived on
  - an attachment whose own chunker raises costs only that attachment
  - an unsupported attachment is still listed by name in the message chunk, so it
    is findable even though its content is not
  - an email attached to an email is skipped, not recursed into
"""

from email.message import EmailMessage
from pathlib import Path


from conversational_toolkit.chunking.base import Chunk
from conversational_toolkit.chunking.email_chunker import EmailChunker


# ─── fixtures ─────────────────────────────────────────────────────────────────


def write_eml(
    tmp_path: Path,
    *,
    body: str = "Please find the signed contract attached.",
    subtype: str = "plain",
    attachments: list[tuple[str, bytes, str, str]] | None = None,
    subject: str = "Contract for review",
) -> str:
    msg = EmailMessage()
    msg["From"] = "Anna Schmidt <anna@example.com>"
    msg["To"] = "roger@example.com"
    msg["Cc"] = "legal@example.com"
    msg["Date"] = "Wed, 07 Oct 2026 09:15:00 +0200"
    msg["Subject"] = subject
    msg["Message-ID"] = "<abc123@example.com>"
    msg.set_content(body, subtype=subtype)
    for filename, payload, maintype, sub in attachments or []:
        msg.add_attachment(payload, maintype=maintype, subtype=sub, filename=filename)
    path = tmp_path / "mail.eml"
    path.write_bytes(msg.as_bytes())
    return str(path)


class RecordingChunker:
    """Stands in for a real attachment chunker; records how it was called."""

    def __init__(self, chunks: list[Chunk] | None = None, raises: Exception | None = None):
        self.chunks = chunks
        self.raises = raises
        self.calls: list[tuple[str, dict]] = []

    def make_chunks(self, file_path: str, **kwargs) -> list[Chunk]:
        self.calls.append((file_path, kwargs))
        if self.raises is not None:
            raise self.raises
        return list(
            self.chunks
            or [
                Chunk(
                    title="Clause 1",
                    content="payment terms",
                    mime_type="text/markdown",
                    metadata={"chapters": ["Clause 1"]},
                ),
            ]
        )


# ─── the message chunk ────────────────────────────────────────────────────────


def test_the_message_becomes_one_chunk_with_readable_headers(tmp_path):
    """Headers go in the content, not just the metadata: BM25 and the embedding only
    see text, so a search for the sender has to have something to match."""
    chunks = EmailChunker().make_chunks(write_eml(tmp_path))

    assert len(chunks) == 1
    body = chunks[0].content
    assert "From: Anna Schmidt <anna@example.com>" in body
    assert "To: roger@example.com" in body
    assert "Cc: legal@example.com" in body
    assert "Subject: Contract for review" in body
    assert "Please find the signed contract attached." in body


def test_the_subject_becomes_the_title(tmp_path):
    chunks = EmailChunker().make_chunks(write_eml(tmp_path))
    assert chunks[0].title == "Contract for review"


def test_a_subjectless_email_still_gets_a_title(tmp_path):
    chunks = EmailChunker().make_chunks(write_eml(tmp_path, subject=""))
    assert chunks[0].title == "(no subject)"


def test_the_headers_are_also_stamped_as_metadata(tmp_path):
    """Metadata is what a chat pre-filter and the chunk browser filter on."""
    meta = EmailChunker().make_chunks(write_eml(tmp_path))[0].metadata
    assert meta["email_from"] == "Anna Schmidt <anna@example.com>"
    assert meta["email_subject"] == "Contract for review"
    assert meta["email_date"] == "Wed, 07 Oct 2026 09:15:00 +0200"
    assert meta["email_message_id"] == "<abc123@example.com>"
    assert meta["email_part"] == "message"


def test_a_folded_header_is_unfolded_into_one_line(tmp_path):
    """Long headers arrive wrapped across lines; left alone they break the
    "From: ..." line the content relies on."""
    long_subject = "Contract for review " + "and further discussion " * 6
    chunks = EmailChunker().make_chunks(write_eml(tmp_path, subject=long_subject))
    subject_lines = [line for line in chunks[0].content.splitlines() if line.startswith("Subject:")]
    assert len(subject_lines) == 1
    assert "\n" not in chunks[0].metadata["email_subject"]


def test_an_html_body_is_converted_to_text(tmp_path):
    path = write_eml(
        tmp_path,
        body="<html><body><h1>Quarterly report</h1><p>Revenue is <b>up</b>.</p></body></html>",
        subtype="html",
    )
    content = EmailChunker().make_chunks(path)[0].content
    assert "Quarterly report" in content
    assert "Revenue is" in content
    assert "<body>" not in content  # tags gone, text kept


# ─── attachments ──────────────────────────────────────────────────────────────


def test_an_attachment_goes_through_the_chunker_for_its_own_type(tmp_path):
    pdf = RecordingChunker()
    path = write_eml(tmp_path, attachments=[("contract.pdf", b"%PDF-fake", "application", "pdf")])

    chunks = EmailChunker({".pdf": pdf}).make_chunks(path)

    assert len(pdf.calls) == 1
    assert pdf.calls[0][0].endswith(".pdf")  # suffix preserved, chunkers select on it
    assert len(chunks) == 2
    assert chunks[1].content.endswith("payment terms")


def test_attachment_chunks_carry_the_emails_headers(tmp_path):
    """The point of indexing attachments through the email: a hit on a clause can
    still say which message delivered it."""
    path = write_eml(tmp_path, attachments=[("contract.pdf", b"%PDF-fake", "application", "pdf")])
    chunks = EmailChunker({".pdf": RecordingChunker()}).make_chunks(path)

    meta = chunks[1].metadata
    assert meta["email_from"] == "Anna Schmidt <anna@example.com>"
    assert meta["email_subject"] == "Contract for review"
    assert meta["email_attachment"] == "contract.pdf"
    # The attachment's own metadata survives alongside it.
    assert meta["chapters"] == ["Clause 1"]


def test_attachment_chunks_carry_the_envelope_in_their_content(tmp_path):
    """Only `content` and `source_file` reach the LLM prompt, and `source_file` is
    overwritten downstream with the .eml's own name — so an attachment chunk has to
    name its origin in the content or it arrives anonymous."""
    path = write_eml(tmp_path, attachments=[("contract.pdf", b"%PDF-fake", "application", "pdf")])
    chunks = EmailChunker({".pdf": RecordingChunker()}).make_chunks(path)

    content = chunks[1].content
    assert 'Attachment "contract.pdf"' in content
    assert "From: Anna Schmidt <anna@example.com>" in content
    assert "To: roger@example.com" in content
    assert "Subject: Contract for review" in content
    assert content.endswith("payment terms")  # the attachment's own text still follows


def test_every_chunk_of_one_attachment_carries_the_envelope(tmp_path):
    """Retrieval returns chunks one at a time; the message chunk holding the full
    headers may not come with them."""
    many = [Chunk(title=f"Clause {i}", content=f"clause {i} text", mime_type="text/markdown") for i in range(3)]
    path = write_eml(tmp_path, attachments=[("contract.pdf", b"%PDF-fake", "application", "pdf")])
    chunks = EmailChunker({".pdf": RecordingChunker(chunks=many)}).make_chunks(path)

    assert len(chunks) == 4  # message + 3
    for c in chunks[1:]:
        assert c.content.startswith('[Attachment "contract.pdf"')
        assert "From: Anna Schmidt <anna@example.com>" in c.content


def test_the_envelope_is_one_line(tmp_path):
    """It repeats per chunk, so its size is a token cost multiplied by chunk count."""
    path = write_eml(tmp_path, attachments=[("contract.pdf", b"%PDF-fake", "application", "pdf")])
    chunks = EmailChunker({".pdf": RecordingChunker()}).make_chunks(path)
    envelope = chunks[1].content.split("\n")[0]
    assert envelope.startswith("[") and envelope.endswith("]")
    assert chunks[1].content.count("[Attachment") == 1


def test_an_image_attachment_gets_no_envelope_in_its_content(tmp_path):
    """Image chunk content is base64 the image pipeline decodes — prefixing it would
    corrupt the payload."""
    import base64

    raw = b"\x89PNG\r\n\x1a\nfake"
    path = write_eml(tmp_path, attachments=[("photo.png", raw, "image", "png")])
    chunks = EmailChunker(image_extensions={".png"}).make_chunks(path, write_images=True)
    assert base64.b64decode(chunks[1].content) == raw


def test_an_attachment_chunk_is_titled_by_its_filename(tmp_path):
    """Without this the title is the random temp-file name the payload was written to."""
    path = write_eml(tmp_path, attachments=[("contract.pdf", b"%PDF-fake", "application", "pdf")])
    chunks = EmailChunker({".pdf": RecordingChunker()}).make_chunks(path)
    assert chunks[1].title == "contract.pdf — Clause 1"


def test_the_message_chunk_lists_the_attachment_names(tmp_path):
    path = write_eml(
        tmp_path,
        attachments=[
            ("contract.pdf", b"%PDF-fake", "application", "pdf"),
            ("prices.xlsx", b"fake-xlsx", "application", "vnd.ms-excel"),
        ],
    )
    chunks = EmailChunker({".pdf": RecordingChunker()}).make_chunks(path)
    assert "Attachments: contract.pdf, prices.xlsx" in chunks[0].content


def test_an_unsupported_attachment_is_still_findable_by_name(tmp_path):
    """No chunker for .zip, so its content is lost — but the name must not be."""
    path = write_eml(tmp_path, attachments=[("bundle.zip", b"PK\x03\x04", "application", "zip")])
    chunks = EmailChunker().make_chunks(path)

    assert len(chunks) == 1
    assert "Attachments: bundle.zip" in chunks[0].content


def test_a_failing_attachment_does_not_cost_the_message_or_its_siblings(tmp_path):
    path = write_eml(
        tmp_path,
        attachments=[
            ("broken.pdf", b"not really a pdf", "application", "pdf"),
            ("notes.md", b"# notes", "text", "markdown"),
        ],
    )
    chunker = EmailChunker(
        {
            ".pdf": RecordingChunker(raises=RuntimeError("docling exploded")),
            ".md": RecordingChunker(
                chunks=[
                    Chunk(title="notes", content="# notes", mime_type="text/markdown"),
                ]
            ),
        }
    )

    chunks = chunker.make_chunks(path)

    assert chunks[0].metadata["email_part"] == "message"  # message survived
    assert len(chunks) == 2  # sibling survived
    assert chunks[1].content.endswith("# notes")
    assert chunks[1].metadata["email_attachment"] == "notes.md"
    assert "Attachments: broken.pdf, notes.md" in chunks[0].content


def test_pdf_attachments_inherit_the_ocr_and_image_settings(tmp_path):
    """A PDF inside an email must be OCR'd on the same terms as one on disk."""
    pdf = RecordingChunker()
    path = write_eml(tmp_path, attachments=[("scan.pdf", b"%PDF-fake", "application", "pdf")])

    EmailChunker({".pdf": pdf}).make_chunks(path, do_ocr=True, write_images=True, image_path="/tmp/x")

    _, kwargs = pdf.calls[0]
    assert kwargs == {"do_ocr": True, "write_images": True, "image_path": "/tmp/x"}


def test_a_non_pdf_attachment_is_not_handed_pdf_arguments(tmp_path):
    xlsx = RecordingChunker()
    path = write_eml(tmp_path, attachments=[("prices.xlsx", b"fake", "application", "vnd.ms-excel")])

    EmailChunker({".xlsx": xlsx}).make_chunks(path, do_ocr=True)

    assert xlsx.calls[0][1] == {}


def test_the_temp_file_is_cleaned_up_after_chunking(tmp_path):
    pdf = RecordingChunker()
    path = write_eml(tmp_path, attachments=[("contract.pdf", b"%PDF-fake", "application", "pdf")])
    EmailChunker({".pdf": pdf}).make_chunks(path)
    assert not Path(pdf.calls[0][0]).exists()


def test_the_temp_file_is_cleaned_up_even_when_chunking_fails(tmp_path):
    pdf = RecordingChunker(raises=RuntimeError("boom"))
    path = write_eml(tmp_path, attachments=[("contract.pdf", b"%PDF-fake", "application", "pdf")])
    EmailChunker({".pdf": pdf}).make_chunks(path)
    assert not Path(pdf.calls[0][0]).exists()


# ─── images ───────────────────────────────────────────────────────────────────


def test_an_image_attachment_becomes_a_base64_chunk_when_images_are_on(tmp_path):
    """Same shape PDFChunker emits — bare base64, no data URI — because the
    captioning and image-store paths decode it directly."""
    import base64

    raw = b"\x89PNG\r\n\x1a\nfake"
    path = write_eml(tmp_path, attachments=[("photo.png", raw, "image", "png")])

    chunks = EmailChunker(image_extensions={".png"}).make_chunks(path, write_images=True)

    assert chunks[1].mime_type == "image/png"
    assert base64.b64decode(chunks[1].content) == raw
    assert chunks[1].metadata["email_attachment"] == "photo.png"


def test_an_image_attachment_is_skipped_when_images_are_off(tmp_path):
    path = write_eml(tmp_path, attachments=[("photo.png", b"\x89PNG-fake", "image", "png")])
    chunks = EmailChunker(image_extensions={".png"}).make_chunks(path, write_images=False)
    assert len(chunks) == 1
    assert "Attachments: photo.png" in chunks[0].content


# ─── recursion guard ──────────────────────────────────────────────────────────


def test_an_email_chunker_never_recurses_into_an_attached_email(tmp_path):
    """Nesting is unbounded in principle, so .eml is dropped from the attachment
    registry rather than depth-limited."""
    chunker = EmailChunker({".eml": RecordingChunker(), ".pdf": RecordingChunker()})
    assert ".eml" not in chunker.attachment_chunkers
    assert ".pdf" in chunker.attachment_chunkers


def test_an_attached_email_is_listed_but_not_parsed(tmp_path):
    inner = write_eml(tmp_path, subject="Inner message")
    path = write_eml(
        tmp_path,
        attachments=[("forwarded.eml", Path(inner).read_bytes(), "application", "octet-stream")],
    )
    # Written last, so `path` is a different file than `inner`.
    chunks = EmailChunker({".eml": RecordingChunker()}).make_chunks(path)
    assert len(chunks) == 1
    assert "Attachments: forwarded.eml" in chunks[0].content


# ─── registry wiring ──────────────────────────────────────────────────────────


def test_the_app_registers_eml_without_letting_it_recurse():
    """The live table: .eml is registered, and the copy the email chunker holds
    excludes itself."""
    from lancy.feature0_baseline_rag import _CHUNKERS, _IMAGE_EXTENSIONS

    assert ".eml" in _CHUNKERS
    email_chunker = _CHUNKERS[".eml"]
    assert isinstance(email_chunker, EmailChunker)
    assert ".eml" not in email_chunker.attachment_chunkers
    # Everything else the app can chunk is reachable as an attachment.
    assert {".pdf", ".xlsx", ".docx", ".md", ".txt"} <= set(email_chunker.attachment_chunkers)
    assert email_chunker.image_extensions == _IMAGE_EXTENSIONS


def test_piles_accept_eml_uploads():
    """The pile upload allowlist is derived from the same table, so .eml rides along."""
    from lancy.piles_router import _SUPPORTED_EXTS

    assert ".eml" in _SUPPORTED_EXTS
