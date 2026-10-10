"""
RFC 822 email (.eml) chunking, including attachments.

An email is two things at once: a short message and a carrier for the documents
attached to it. Indexing only the message body loses the documents, which are
usually the part worth searching; indexing only the attachments loses who sent
them, when, and what they said about them.

So `EmailChunker` emits both. The message becomes one chunk carrying the headers
and body, and every attachment is handed to the chunker registered for its own
file type — a PDF attachment goes through the PDF pipeline, OCR included. All of
them keep the email's headers in their metadata, so a hit on page 4 of an
attachment can still say which message it arrived on.

Attachment chunkers are injected rather than imported: the registry mapping
extensions to chunkers belongs to the application, not to this package.
"""

import base64
import logging
import tempfile
import unicodedata
from email import message_from_binary_file, policy
from email.message import EmailMessage
from pathlib import Path
from typing import Any, Iterable

from conversational_toolkit.chunking.base import Chunk, Chunker

logger = logging.getLogger(__name__)

# Rendered into the message chunk so the human-readable envelope is searchable
# and visible in a citation, in the order a mail client would show them.
_HEADERS = ("From", "To", "Cc", "Date", "Subject")


class EmailChunker(Chunker):
    """Chunks a .eml file into its message plus one group per attachment.

    Attributes:
        attachment_chunkers: extension -> chunker for attachment payloads.
        image_extensions: attachment extensions emitted as base64 image chunks
            when `write_images` is set, matching what PDFChunker produces.
    """

    def __init__(
        self,
        attachment_chunkers: dict[str, Any] | None = None,
        image_extensions: Iterable[str] = (),
    ) -> None:
        # ".eml" is dropped defensively: an email attached to an email would
        # otherwise recurse as deep as the nesting goes. Such an attachment is
        # skipped with a warning, and its filename still appears in the message
        # chunk's attachment list.
        self.attachment_chunkers = {
            ext: c for ext, c in (attachment_chunkers or {}).items() if ext != ".eml"
        }
        self.image_extensions = set(image_extensions)

    # ── Parsing ───────────────────────────────────────────────────────────────

    @staticmethod
    def _read(file_path: str) -> EmailMessage:
        with open(file_path, "rb") as fh:
            # policy=default gives decoded, unfolded header values and the
            # get_body/iter_attachments API; the compat32 default gives neither.
            return message_from_binary_file(fh, policy=policy.default)  # type: ignore[return-value]

    @staticmethod
    def _header(msg: EmailMessage, name: str) -> str:
        try:
            value = msg.get(name)
        except Exception as exc:
            # A malformed address header raises on access rather than on parse.
            logger.warning(f"Email header {name!r} unreadable: {exc}")
            return ""
        return " ".join(str(value).split()) if value else ""

    def _body_text(self, msg: EmailMessage) -> str:
        """The message body as text, preferring text/plain over text/html."""
        try:
            part = msg.get_body(preferencelist=("plain", "html"))
        except Exception as exc:
            logger.warning(f"Could not locate an email body part: {exc}")
            return ""
        if part is None:
            return ""

        try:
            content = part.get_content()
        except Exception as exc:
            logger.warning(f"Could not decode the email body: {exc}")
            return ""

        if part.get_content_subtype() == "html":
            return self._html_to_text(str(content))
        return str(content)

    @staticmethod
    def _html_to_text(html: str) -> str:
        """Convert an HTML body to markdown via MarkItDown, the same converter the
        .docx/.epub path uses. Falls back to the raw HTML, which is ugly but still
        searchable, rather than dropping the body entirely."""
        try:
            from markitdown import MarkItDown  # type: ignore[import-untyped]

            with tempfile.NamedTemporaryFile(suffix=".html", delete=False) as fh:
                fh.write(html.encode("utf-8", errors="replace"))
                tmp = Path(fh.name)
            try:
                return str(MarkItDown().convert(str(tmp)).text_content)
            finally:
                tmp.unlink(missing_ok=True)
        except Exception as exc:
            logger.warning(f"HTML email body conversion failed ({exc}); using raw HTML")
            return html

    # ── Chunk construction ────────────────────────────────────────────────────

    def _email_metadata(self, msg: EmailMessage) -> dict[str, Any]:
        """Headers stamped on every chunk this email produces, attachments included,
        so a hit inside an attachment can still name the message it arrived on."""
        meta = {
            f"email_{name.lower()}": self._header(msg, name)
            for name in _HEADERS
        }
        meta["email_message_id"] = self._header(msg, "Message-ID")
        return {k: v for k, v in meta.items() if v}

    def _message_chunk(
        self, msg: EmailMessage, attachment_names: list[str]
    ) -> Chunk:
        lines = [f"{name}: {self._header(msg, name)}" for name in _HEADERS if self._header(msg, name)]
        if attachment_names:
            # Listed in the body so an attachment is findable by name even when its
            # own content could not be parsed.
            lines.append(f"Attachments: {', '.join(attachment_names)}")
        header_block = "\n".join(lines)
        body = self._body_text(msg).strip()

        subject = self._header(msg, "Subject")
        return Chunk(
            title=subject or "(no subject)",
            content=unicodedata.normalize("NFC", f"{header_block}\n\n{body}".strip()),
            mime_type="text/plain",
            metadata={**self._email_metadata(msg), "chapters": [], "email_part": "message"},
        )

    def _attachment_chunks(
        self,
        msg: EmailMessage,
        write_images: bool,
        image_path: str | None,
        do_ocr: bool,
    ) -> tuple[list[Chunk], list[str]]:
        chunks: list[Chunk] = []
        names: list[str] = []
        email_meta = self._email_metadata(msg)

        try:
            attachments = list(msg.iter_attachments())
        except Exception as exc:
            logger.warning(f"Could not enumerate email attachments: {exc}")
            return [], []

        for part in attachments:
            filename = part.get_filename() or ""
            if not filename:
                continue
            names.append(filename)
            ext = Path(filename).suffix.lower()

            try:
                payload = part.get_content()
            except Exception as exc:
                logger.warning(f"Attachment {filename!r}: payload unreadable ({exc})")
                continue
            if isinstance(payload, str):
                payload = payload.encode("utf-8", errors="replace")
            if not isinstance(payload, bytes):
                logger.warning(f"Attachment {filename!r}: unexpected payload type")
                continue

            if ext in self.attachment_chunkers:
                chunks.extend(
                    self._chunk_attachment(
                        filename, ext, payload, email_meta, write_images, image_path, do_ocr
                    )
                )
            elif ext in self.image_extensions:
                if write_images:
                    chunks.append(
                        Chunk(
                            title=filename,
                            # Same shape PDFChunker emits: bare base64, no data URI.
                            content=base64.b64encode(payload).decode("utf-8"),
                            mime_type=f"image/{ext[1:]}",
                            metadata={**email_meta, "email_attachment": filename},
                        )
                    )
            else:
                logger.warning(
                    f"Attachment {filename!r}: unsupported type {ext!r}, indexed by name only"
                )

        return chunks, names

    def _chunk_attachment(
        self,
        filename: str,
        ext: str,
        payload: bytes,
        email_meta: dict[str, Any],
        write_images: bool,
        image_path: str | None,
        do_ocr: bool,
    ) -> list[Chunk]:
        """Run an attachment through the chunker for its own file type.

        The payload is written to a temp file because every chunker takes a path —
        they hand it to docling, pandas or MarkItDown, none of which read bytes.
        """
        with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as fh:
            fh.write(payload)
            tmp = Path(fh.name)
        try:
            kwargs: dict[str, Any] = {}
            if ext == ".pdf":
                kwargs = {"do_ocr": do_ocr, "write_images": write_images, "image_path": image_path}
            produced = self.attachment_chunkers[ext].make_chunks(str(tmp), **kwargs)
        except Exception as exc:
            # One bad attachment must not cost the message or its siblings.
            logger.warning(
                f"Attachment {filename!r}: chunking failed ({type(exc).__name__}: {exc})"
            )
            return []
        finally:
            tmp.unlink(missing_ok=True)

        envelope = self._provenance_line(filename, email_meta)
        for chunk in produced:
            chunk.metadata = {**chunk.metadata, **email_meta, "email_attachment": filename}
            # The temp file's random name would otherwise be the only title an
            # attachment chunk carries.
            chunk.title = f"{filename} — {chunk.title}" if chunk.title else filename
            # Prepended to the content, not left in metadata: only `content` and
            # `source_file` reach the LLM prompt, and `source_file` is overwritten
            # downstream with the .eml's own name. Without this an attachment chunk
            # reaches the model as anonymous text attributed to "mail.eml", with no
            # way to say which attachment it came from or who sent it.
            #
            # Content rather than a prompt attribute so the retrievers see it too —
            # BM25, the embedding and the reranker all read content, so "what did
            # Anna send about the contract" can match the attachment itself.
            if envelope:
                chunk.content = f"{envelope}\n\n{chunk.content}"
        return produced

    @staticmethod
    def _provenance_line(filename: str, email_meta: dict[str, Any]) -> str:
        """One compact line naming the attachment and the message that carried it.

        Repeated on every chunk of the attachment, because retrieval returns chunks
        individually — the message chunk holding the full headers may well not be
        retrieved alongside. Kept to a single line to bound that repetition.
        """
        parts = [f'Attachment "{filename}"']
        for key, label in (
            ("email_from", "From"),
            ("email_to", "To"),
            ("email_date", "Date"),
            ("email_subject", "Subject"),
        ):
            if email_meta.get(key):
                parts.append(f"{label}: {email_meta[key]}")
        return f"[{' · '.join(parts)}]"

    # ── Entry point ───────────────────────────────────────────────────────────

    def make_chunks(  # type: ignore[override]
        self,
        file_path: str,
        write_images: bool = False,
        image_path: str | None = None,
        do_ocr: bool = True,
        **_kwargs: Any,
    ) -> list[Chunk]:
        msg = self._read(file_path)
        attachment_chunks, names = self._attachment_chunks(
            msg, write_images=write_images, image_path=image_path, do_ocr=do_ocr
        )
        # The message chunk is built second so it can list the attachment names, but
        # comes first in the output — it is the thing that gives the rest context.
        return [self._message_chunk(msg, names), *attachment_chunks]
