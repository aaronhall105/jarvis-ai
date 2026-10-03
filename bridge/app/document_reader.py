"""Bounded, provider-neutral document extraction and registered read capability.

Attachment bytes never leave the provider connector.  Provider attachment
capabilities return this module's bounded structured extraction, and the local
``document.read`` connector composes those reads without granting write
authority.  Extracted content is untrusted evidence, never instructions.
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
import re
import sqlite3
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pypdf import PdfReader, filters as pypdf_filters

from app.connectors.base import (
    CapabilityAccess,
    CapabilityMetadata,
    CapabilityRequest,
    Connector,
    ConnectorResult,
    ExecutionStatus,
    ProviderStatus,
    VerificationMode,
    VerificationResult,
)

if TYPE_CHECKING:
    from app.connectors.registry import ConnectorRegistry


SUPPORTED_MIME_TYPES = frozenset(
    {
        "application/pdf",
        "text/plain",
        "text/csv",
        "text/tab-separated-values",
    }
)
_TEXT_MIME_TYPES = SUPPORTED_MIME_TYPES - {"application/pdf"}
_MAX_PDF_DECOMPRESSED_BYTES = 32 * 1024 * 1024
for _limit_name in (
    "ZLIB_MAX_OUTPUT_LENGTH",
    "LZW_MAX_OUTPUT_LENGTH",
    "RUN_LENGTH_MAX_OUTPUT_LENGTH",
    "JBIG2_MAX_OUTPUT_LENGTH",
    "MAX_ARRAY_BASED_STREAM_OUTPUT_LENGTH",
    "MAX_DECLARED_STREAM_LENGTH",
):
    if hasattr(pypdf_filters, _limit_name):
        setattr(
            pypdf_filters,
            _limit_name,
            min(int(getattr(pypdf_filters, _limit_name)), _MAX_PDF_DECOMPRESSED_BYTES),
        )


@dataclass(frozen=True, slots=True)
class DocumentLimits:
    max_file_bytes: int = 10 * 1024 * 1024
    max_pdf_pages: int = 40
    max_pdf_declared_pages: int = 200
    max_extracted_characters: int = 60_000
    max_chunks: int = 40
    parser_timeout_seconds: float = 10.0


@dataclass(frozen=True, slots=True)
class DocumentExtraction:
    document_type: str
    filename: str
    mime_type: str
    fingerprint_sha256: str
    size_bytes: int
    text_content: str
    text_chunks: tuple[Mapping[str, Any], ...]
    page_count: int | None
    evidence_status: str = "verified"
    truncated: bool = False
    warnings: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


class DocumentReadError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _normalise_mime(value: str) -> str:
    return str(value or "").split(";", 1)[0].strip().casefold()


def _bounded_text(value: str, maximum: int) -> tuple[str, bool]:
    cleaned = value.replace("\x00", "").strip()
    if len(cleaned) <= maximum:
        return cleaned, False
    return cleaned[:maximum].rstrip(), True


def _extract_text_document(
    data: bytes, *, filename: str, mime_type: str, limits: DocumentLimits
) -> DocumentExtraction:
    if b"\x00" in data:
        raise DocumentReadError(
            "unsupported_document_encoding",
            "The attachment does not appear to be ordinary UTF-8 or ASCII text.",
        )
    try:
        decoded = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        try:
            decoded = data.decode("ascii")
        except UnicodeDecodeError as exc:
            raise DocumentReadError(
                "unsupported_document_encoding",
                "The attachment is not valid UTF-8 or ASCII text.",
            ) from exc
    text, truncated = _bounded_text(decoded, limits.max_extracted_characters)
    if not text:
        raise DocumentReadError("empty_document", "The attachment contains no readable text.")
    chunks = tuple(
        {"chunk_index": index, "text": text[offset : offset + 2_000]}
        for index, offset in enumerate(range(0, len(text), 2_000), start=1)
        if index <= limits.max_chunks
    )
    return DocumentExtraction(
        document_type="text",
        filename=filename,
        mime_type=mime_type,
        fingerprint_sha256=hashlib.sha256(data).hexdigest(),
        size_bytes=len(data),
        text_content=text,
        text_chunks=chunks,
        page_count=None,
        truncated=truncated or len(chunks) * 2_000 < len(text),
    )


def _extract_pdf_document(
    data: bytes, *, filename: str, limits: DocumentLimits
) -> DocumentExtraction:
    try:
        reader = PdfReader(io.BytesIO(data), strict=True)
    except Exception as exc:
        raise DocumentReadError("malformed_pdf", "The PDF could not be read safely.") from exc
    if reader.is_encrypted:
        raise DocumentReadError(
            "encrypted_document",
            "The PDF is encrypted and cannot currently be read.",
        )
    page_count = len(reader.pages)
    if page_count > limits.max_pdf_declared_pages:
        raise DocumentReadError(
            "document_too_many_pages",
            f"The PDF exceeds the {limits.max_pdf_declared_pages}-page safety limit.",
        )
    page_limit = min(page_count, limits.max_pdf_pages)
    chunks: list[Mapping[str, Any]] = []
    collected: list[str] = []
    character_count = 0
    truncated = page_count > page_limit
    for page_number in range(page_limit):
        try:
            page_text = str(reader.pages[page_number].extract_text() or "").strip()
        except Exception as exc:
            raise DocumentReadError(
                "pdf_text_extraction_failed",
                f"Text extraction failed on PDF page {page_number + 1}.",
            ) from exc
        if not page_text:
            continue
        remaining = limits.max_extracted_characters - character_count
        if remaining <= 0:
            truncated = True
            break
        bounded, page_truncated = _bounded_text(page_text, remaining)
        collected.append(bounded)
        for offset in range(0, len(bounded), 2_000):
            if len(chunks) >= limits.max_chunks:
                truncated = True
                break
            chunks.append(
                {
                    "page": page_number + 1,
                    "chunk_index": len(chunks) + 1,
                    "text": bounded[offset : offset + 2_000],
                }
            )
        character_count += len(bounded)
        if page_truncated:
            truncated = True
            break
    text = "\n\n".join(collected).strip()
    if not text:
        raise DocumentReadError(
            "image_only_document",
            "The PDF appears to be image-based and no OCR capability is available.",
        )
    return DocumentExtraction(
        document_type="pdf",
        filename=filename,
        mime_type="application/pdf",
        fingerprint_sha256=hashlib.sha256(data).hexdigest(),
        size_bytes=len(data),
        text_content=text,
        text_chunks=tuple(chunks[: limits.max_chunks]),
        page_count=page_count,
        truncated=truncated or len(chunks) > limits.max_chunks,
        warnings=(("Only the bounded leading pages were read.",) if truncated else ()),
    )


async def extract_document(
    data: bytes,
    *,
    filename: str,
    mime_type: str,
    limits: DocumentLimits | None = None,
) -> DocumentExtraction:
    """Extract bounded text without executing, unpacking, or shelling out."""

    policy = limits or DocumentLimits()
    if not isinstance(data, bytes):
        raise DocumentReadError("malformed_document", "Attachment content is invalid.")
    if not data:
        raise DocumentReadError("empty_document", "The attachment is empty.")
    if len(data) > policy.max_file_bytes:
        raise DocumentReadError(
            "document_too_large",
            f"The attachment exceeds the {policy.max_file_bytes}-byte safety limit.",
        )
    safe_filename = " ".join(str(filename or "attachment").split())[:240]
    mime = _normalise_mime(mime_type)
    if mime not in SUPPORTED_MIME_TYPES:
        raise DocumentReadError(
            "unsupported_document_type",
            f"The attachment type {mime or 'unknown'} is not supported.",
        )
    if mime == "application/pdf" and not data.startswith(b"%PDF-"):
        raise DocumentReadError("mime_mismatch", "The attachment is not a valid PDF file.")
    if mime == "application/pdf":
        parser = lambda: _extract_pdf_document(data, filename=safe_filename, limits=policy)
    else:
        parser = lambda: _extract_text_document(
            data, filename=safe_filename, mime_type=mime, limits=policy
        )
    try:
        return await asyncio.wait_for(
            asyncio.to_thread(parser), timeout=policy.parser_timeout_seconds
        )
    except TimeoutError as exc:
        raise DocumentReadError(
            "document_parse_timeout", "The attachment exceeded the parser time limit."
        ) from exc


class DocumentExtractionCache:
    """Principal-scoped cache for immutable email attachments; never stores bytes."""

    def __init__(self, path: Path) -> None:
        self.path = path

    @staticmethod
    def source_key(provider: str, message_id: str, attachment_id: str) -> str:
        material = "\x00".join((provider, message_id, attachment_id)).encode()
        return hashlib.sha256(material).hexdigest()

    async def initialize(self) -> None:
        await asyncio.to_thread(self._initialize_sync)

    def _initialize_sync(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.path) as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS document_extractions (
                    principal_id TEXT NOT NULL,
                    source_key TEXT NOT NULL,
                    fingerprint_sha256 TEXT NOT NULL,
                    result_json TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (principal_id, source_key)
                )
                """
            )

    async def get(self, *, principal_id: str, source_key: str) -> dict[str, Any] | None:
        await self.initialize()

        def read() -> dict[str, Any] | None:
            with sqlite3.connect(self.path) as connection:
                row = connection.execute(
                    "SELECT result_json FROM document_extractions "
                    "WHERE principal_id=? AND source_key=?",
                    (principal_id, source_key),
                ).fetchone()
            return json.loads(str(row[0])) if row else None

        return await asyncio.to_thread(read)

    async def put(self, *, principal_id: str, source_key: str, result: Mapping[str, Any]) -> None:
        await self.initialize()
        payload = json.dumps(dict(result), separators=(",", ":"), ensure_ascii=False)
        fingerprint = str(result.get("fingerprint_sha256") or "")

        def write() -> None:
            with sqlite3.connect(self.path) as connection:
                connection.execute(
                    """
                    INSERT INTO document_extractions
                        (principal_id, source_key, fingerprint_sha256, result_json)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(principal_id, source_key) DO UPDATE SET
                        fingerprint_sha256=excluded.fingerprint_sha256,
                        result_json=excluded.result_json,
                        created_at=CURRENT_TIMESTAMP
                    """,
                    (principal_id, source_key, fingerprint, payload),
                )

        await asyncio.to_thread(write)


class DocumentConnector(Connector):
    """Registered provider-neutral reader composed over provider attachment reads."""

    provider_id = "jarvis_documents"

    def __init__(self, *, registry: ConnectorRegistry, cache: DocumentExtractionCache) -> None:
        super().__init__(
            provider_id=self.provider_id,
            name="Jarvis document reader",
            capabilities=(
                CapabilityMetadata(
                    capability_id="document.metadata",
                    provider_id=self.provider_id,
                    name="Read attachment metadata",
                    access=CapabilityAccess.READ,
                    verification=VerificationMode.REQUIRED,
                    timeout_seconds=30,
                ),
                CapabilityMetadata(
                    capability_id="document.read",
                    provider_id=self.provider_id,
                    name="Read bounded document content",
                    access=CapabilityAccess.READ,
                    verification=VerificationMode.REQUIRED,
                    timeout_seconds=45,
                ),
            ),
        )
        self.registry = registry
        self.cache = cache

    async def status(self) -> ProviderStatus:
        return ProviderStatus(
            provider_id=self.provider_id,
            name=self.name,
            configured=True,
            authenticated=True,
            healthy=True,
            executable_capabilities=tuple(item.capability_id for item in self.capabilities),
        )

    @staticmethod
    def _provider_capabilities(provider: str) -> tuple[str, str]:
        if provider in {"google", "google_gmail"}:
            return "gmail.read", "gmail.attachment.read"
        if provider in {"microsoft", "microsoft_outlook"}:
            return "outlook.attachments", "outlook.attachment.read"
        raise DocumentReadError(
            "unsupported_document_provider", "That attachment provider is not supported."
        )

    async def _run(
        self, capability_id: str, payload: Mapping[str, Any], request: CapabilityRequest
    ) -> Mapping[str, Any]:
        execution = await self.registry.execute(
            capability_id,
            payload,
            request_id=f"{request.request_id}:{capability_id}:{uuid.uuid4()}",
            conversation_id=request.conversation_id,
            principal_id=request.principal_id,
            operation="document_attachment_read",
        )
        if execution.status is not ExecutionStatus.SUCCEEDED:
            provider_error = (
                execution.error or "The attachment provider could not complete the read."
            )
            provider_code = provider_error.split(":", 1)[0]
            known_codes = {
                "document_too_large",
                "document_too_many_pages",
                "document_parse_timeout",
                "empty_document",
                "encrypted_document",
                "image_only_document",
                "malformed_document",
                "malformed_pdf",
                "mime_mismatch",
                "pdf_text_extraction_failed",
                "unsupported_document_encoding",
                "unsupported_document_type",
            }
            raise DocumentReadError(
                provider_code if provider_code in known_codes else "provider_document_read_failed",
                provider_error,
            )
        return execution.data

    @staticmethod
    def _attachments(provider: str, result: Mapping[str, Any]) -> list[dict[str, Any]]:
        if provider in {"google", "google_gmail"}:
            message = result.get("message")
            rows = (
                message.get("attachments")
                if isinstance(message, Mapping)
                else result.get("attachments")
            ) or ()
        else:
            rows = result.get("attachments") or ()
        return [dict(item) for item in rows if isinstance(item, Mapping)][:100]

    async def execute(
        self, capability: CapabilityMetadata, request: CapabilityRequest
    ) -> ConnectorResult:
        principal = str(request.principal_id or "").strip()
        provider = str(request.payload.get("provider") or "").strip()
        message_id = str(request.payload.get("message_id") or "").strip()
        if not principal or not provider or not message_id:
            return ConnectorResult.failed(
                "A principal, provider, and grounded message identity are required."
            )
        try:
            metadata_capability, read_capability = self._provider_capabilities(provider)
            if capability.capability_id == "document.metadata":
                result = await self._run(metadata_capability, {"message_id": message_id}, request)
                attachments = self._attachments(provider, result)
                return ConnectorResult.succeeded(
                    {"attachments": attachments, "count": len(attachments)}
                )

            attachment_id = str(request.payload.get("attachment_id") or "").strip()
            metadata = await self._run(metadata_capability, {"message_id": message_id}, request)
            attachments = self._attachments(provider, metadata)
            if attachment_id:
                matches = [
                    item
                    for item in attachments
                    if str(item.get("attachment_id") or "") == attachment_id
                ]
            else:
                readable = [
                    item
                    for item in attachments
                    if _normalise_mime(str(item.get("mime_type") or "")) in SUPPORTED_MIME_TYPES
                    and item.get("is_inline") is not True
                ]
                matches = readable if len(readable) == 1 else []
                if len(readable) > 1:
                    return ConnectorResult.succeeded(
                        {
                            "selection_required": True,
                            "attachments": readable,
                            "count": len(readable),
                        }
                    )
                if attachments and not readable:
                    raise DocumentReadError(
                        "unsupported_document_type",
                        "That email has no attachment type the document reader supports.",
                    )
            if len(matches) != 1:
                return ConnectorResult.failed("The requested attachment could not be grounded.")
            selected = matches[0]
            attachment_id = str(selected.get("attachment_id") or "")
            if not attachment_id:
                return ConnectorResult.failed("The provider did not return an attachment identity.")
            if int(selected.get("size") or 0) > DocumentLimits().max_file_bytes:
                raise DocumentReadError(
                    "document_too_large", "The attachment exceeds the document size limit."
                )
            source_key = self.cache.source_key(provider, message_id, attachment_id)
            cached = await self.cache.get(principal_id=principal, source_key=source_key)
            if cached is not None:
                return ConnectorResult.succeeded({**cached, "cache_hit": True})
            result = await self._run(
                read_capability,
                {"message_id": message_id, "attachment_id": attachment_id},
                request,
            )
            document = result.get("document")
            if not isinstance(document, Mapping):
                raise DocumentReadError(
                    "malformed_document_result", "The provider returned invalid document evidence."
                )
            safe_result = {
                "document": dict(document),
                "attachment": selected,
                "provider": provider,
                "fingerprint_sha256": document.get("fingerprint_sha256"),
                "cache_hit": False,
            }
            await self.cache.put(principal_id=principal, source_key=source_key, result=safe_result)
            return ConnectorResult.succeeded(safe_result)
        except DocumentReadError as exc:
            return ConnectorResult.failed(f"{exc.code}: {exc}")

    async def verify(
        self,
        capability: CapabilityMetadata,
        request: CapabilityRequest,
        result: ConnectorResult,
    ) -> VerificationResult:
        del capability, request
        if result.status.value == "succeeded":
            return VerificationResult.verified({"read_only": True})
        return VerificationResult.unverified(result.error)


def _document_evidence_text(document: Mapping[str, Any]) -> str:
    chunks = [
        str(item.get("text") or "")
        for item in document.get("text_chunks") or ()
        if isinstance(item, Mapping) and str(item.get("text") or "").strip()
    ]
    chunk_text = "\n".join(chunks)
    direct = str(document.get("text_content") or "")
    return chunk_text if len(chunk_text) > len(direct) else direct


def exact_evidence_answer(question: str, document: Mapping[str, Any]) -> dict[str, Any] | None:
    """Conservative fallback: answer only explicit label/value questions.

    This parser is domain-neutral.  It matches user words to document labels and
    never invents a value.  Semantic cases are handled by the model selector and
    then subjected to the same exact-evidence validation.
    """

    text = _document_evidence_text(document)
    words = {
        word
        for word in re.findall(r"[a-z0-9]+", question.casefold())
        if len(word) > 2 and word not in {"what", "which", "much", "does", "document"}
    }
    candidates: list[tuple[int, str, str, str]] = []
    pattern = re.compile(r"^\s*([^:\n]{2,80}?)(?:\s*[:=-]\s*|\s{2,})([^\n]{1,120})\s*$")
    for line in text.splitlines():
        match = pattern.match(line)
        if not match:
            continue
        label = " ".join(match.group(1).split())
        value = " ".join(match.group(2).split())
        label_words = set(re.findall(r"[a-z0-9]+", label.casefold()))
        score = len(words & label_words)
        if score:
            candidates.append((score, label, value, line.strip()))
    if not candidates:
        return None
    candidates.sort(key=lambda item: item[0], reverse=True)
    if len(candidates) > 1 and candidates[0][0] == candidates[1][0]:
        return None
    _score, label, value, quote = candidates[0]
    return {"label": label, "value": value, "evidence_quote": quote}


def validate_model_evidence(
    selection: Mapping[str, Any], document: Mapping[str, Any]
) -> dict[str, Any] | None:
    """Accept model interpretation only when its exact quote/value exists."""

    text = _document_evidence_text(document)
    quote = " ".join(str(selection.get("evidence_quote") or "").split())
    value = " ".join(str(selection.get("value") or "").split())
    label = " ".join(str(selection.get("label") or "").split())
    normalised_text = " ".join(text.split())
    if (
        not quote
        or not value
        or quote not in normalised_text
        or value not in quote
        or (label and label not in quote)
    ):
        return None
    validated: dict[str, Any] = {
        "label": label[:120],
        "value": value[:240],
        "evidence_quote": quote[:500],
    }
    semantic_field = str(selection.get("semantic_field") or "").strip().casefold()
    if semantic_field in {
        "net_pay",
        "gross_pay",
        "income_tax",
        "monetary_amount",
        "measurement",
        "other",
    }:
        validated["semantic_field"] = semantic_field
    value_kind = str(selection.get("value_kind") or "").strip().casefold()
    if value_kind in {"money", "number", "text"}:
        validated["value_kind"] = value_kind
    currency = str(selection.get("currency") or "").strip().upper()
    currency_markers = {
        "GBP": ("£", r"\bGBP\b"),
        "USD": ("$", r"\bUSD\b"),
        "EUR": ("€", r"\bEUR\b"),
    }
    marker = currency_markers.get(currency)
    if marker and (marker[0] in quote or re.search(marker[1], quote, flags=re.IGNORECASE)):
        validated["currency"] = currency
    return validated
