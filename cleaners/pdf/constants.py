"""PDF cleaning rules and protected-token patterns."""

from __future__ import annotations

import re

CLEANING_VERSION = "clean-v1"
_LIGATURES = str.maketrans(
    {
        "ﬁ": "fi",
        "ﬂ": "fl",
        "ﬀ": "ff",
        "ﬃ": "ffi",
        "ﬄ": "ffl",
    }
)
_PAGE_NUMBER_RE = re.compile(r"^\d{1,4}$")
_PROTECTED_TOKEN_RE = re.compile(
    r"\[\d+\]|(?<![\w])[-+]?\d+(?:[.,]\d+)*(?:%|[A-Za-zµμ]+)?"
)
_IMAGE_MARKERS = ("image", "figure", "diagram", "architecture", "图", "图片", "图示")
_KEEP_HYPHENATED_COMPOUNDS = {
    "knowledge-intensive",
    "open-domain",
    "non-parametric",
    "state-of-the-art",
    "fine-tuning",
    "pre-trained",
    "parametric-only",
    "task-specific",
    "retrieve-and-extract",
    "end-to-end",
    "closed-book",
    "top-k",
    "two-way",
    "word-overlap-based",
    "well-suited",
}
_KNOWN_PDF_JOIN_REPAIRS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\bdocumentszi\b", re.IGNORECASE), "documents zi"),
    (re.compile(r"\btreatz\b", re.IGNORECASE), "treat z"),
    (re.compile(r"\bknowledgeintensive\b", re.IGNORECASE), "knowledge-intensive"),
    (re.compile(r"\bstackaugmented\b", re.IGNORECASE), "stack-augmented"),
    (re.compile(r"\bnonparametric\b", re.IGNORECASE), "non-parametric"),
    (re.compile(r"\bTheSunAlso\s*R\s*ises\b", re.IGNORECASE), "The Sun Also Rises"),
    (re.compile(r"\bFarewellto\b", re.IGNORECASE), "Farewell to"),
    (re.compile(r"\bAnswer\s+GenerationRetriever\b", re.IGNORECASE), "Answer Generation Retriever"),
)
_KNOWN_TABLE_BODY_BOUNDARIES: dict[str, tuple[str, str]] = {
    "p07_t03": (
        r"\bTask\s+Input\s+Model\s+Generation\b",
        r"\bFor\s+2-way\s+classification\b",
    ),
}
