"""Shared QA narrative for KG full-capability E2E.

Keeps inject payloads, DOCX bodies, and unit-test artifact bundles aligned so the
agent sees consistent parties, dates, and terms across email thread + attachments.
"""

from __future__ import annotations

# Parties (stable across parent thread, child request, and DOCX bodies)
DISCLOSING_PARTY = "Acme Corp"
RECEIVING_PARTY = "Jane Doe"
RECEIVING_EMAIL = "jane@acme.com"
EFFECTIVE_DATE = "January 1, 2026"
PURPOSE = (
    "Evaluating a potential business partnership and joint product discussions "
    "between Acme Corp and Jane Doe"
)
CONFIDENTIALITY_TERM = "24 months from the Effective Date"
GOVERNING_LAW = "State of Delaware"
CONTRACT_TYPE = "mutual NDA"

PARENT_SUBJECT = f"Signed NDA - {DISCLOSING_PARTY} and {RECEIVING_PARTY}"
PARENT_BODY = (
    f"Please archive the executed mutual non-disclosure agreement between "
    f"{DISCLOSING_PARTY} (Delaware corporation) and {RECEIVING_PARTY} "
    f"({RECEIVING_EMAIL}).\n\n"
    f"The agreement was fully executed on {EFFECTIVE_DATE}. "
    f"Purpose: {PURPOSE}. "
    f"Confidentiality term: {CONFIDENTIALITY_TERM}. "
    f"Governing law: {GOVERNING_LAW}.\n\n"
    f"Attachment: executed_nda.docx (signed copy for records)."
)

CHILD_SUBJECT = f"Please draft NDA for {DISCLOSING_PARTY} and {RECEIVING_PARTY}"
CHILD_BODY = (
    f"We need a new {CONTRACT_TYPE} between {DISCLOSING_PARTY} and "
    f"{RECEIVING_PARTY} ({RECEIVING_EMAIL}).\n\n"
    f"Use the same terms as the executed agreement in this email thread "
    f"(see parent message from records@acme.test with executed_nda.docx):\n"
    f"- Effective date: {EFFECTIVE_DATE}\n"
    f"- Purpose: {PURPOSE}\n"
    f"- Confidentiality term: {CONFIDENTIALITY_TERM}\n"
    f"- Governing law: {GOVERNING_LAW}\n\n"
    f"Also review prior_nda.docx attached here — it is the working template "
    f"with the same party names and term length. Do not draft a unilateral NDA; "
    f"this must be mutual.\n\n"
    f"Jane Doe is the receiving party; Acme Corp is the disclosing party "
    f"(same as the prior deal)."
)

PRIOR_NDA_DOCX_SECTIONS: list[tuple[str, str]] = [
    ("MUTUAL NON-DISCLOSURE AGREEMENT (TEMPLATE)", "heading"),
    ("Parties", "heading"),
    (
        f"Disclosing Party: {DISCLOSING_PARTY} (Delaware corporation)\n"
        f"Receiving Party: {RECEIVING_PARTY} (individual consultant, {RECEIVING_EMAIL})",
        "body",
    ),
    ("Agreement terms", "heading"),
    (f"Effective Date: {EFFECTIVE_DATE}", "body"),
    (f"Purpose: {PURPOSE}", "body"),
    (f"Confidentiality Term: obligations survive for {CONFIDENTIALITY_TERM}.", "body"),
    (f"Governing Law: {GOVERNING_LAW}", "body"),
    ("Contract type: mutual NDA (not unilateral).", "body"),
]

EXECUTED_NDA_DOCX_SECTIONS: list[tuple[str, str]] = [
    ("EXECUTED — MUTUAL NON-DISCLOSURE AGREEMENT", "heading"),
    ("Status: FULLY EXECUTED AND IN EFFECT", "heading"),
    *PRIOR_NDA_DOCX_SECTIONS[1:],
    (
        f"Execution note: signed by authorized representatives of {DISCLOSING_PARTY} "
        f"and {RECEIVING_PARTY} on {EFFECTIVE_DATE}.",
        "body",
    ),
]

# Structured envelope mirroring what extract/analyze should surface from the DOCX text.
PRIOR_NDA_EXTRACTED_DATA: dict = {
    "source_file": "prior_nda.docx",
    "summary": (
        f"Mutual NDA template between {DISCLOSING_PARTY} and {RECEIVING_PARTY} "
        f"with {CONFIDENTIALITY_TERM} and {GOVERNING_LAW}."
    ),
    "entities": [
        {"name": DISCLOSING_PARTY, "type": "Organization"},
        {"name": RECEIVING_PARTY, "type": "Person", "email": RECEIVING_EMAIL},
    ],
    "key_findings": [
        f"Effective date: {EFFECTIVE_DATE}",
        f"Purpose: {PURPOSE}",
        f"Confidentiality term: {CONFIDENTIALITY_TERM}",
        f"Governing law: {GOVERNING_LAW}",
        f"Contract type: {CONTRACT_TYPE}",
    ],
}

EXECUTED_NDA_EXTRACTED_DATA: dict = {
    "source_file": "executed_nda.docx",
    "summary": (
        f"Executed mutual NDA between {DISCLOSING_PARTY} and {RECEIVING_PARTY}, "
        f"effective {EFFECTIVE_DATE}."
    ),
    "entities": PRIOR_NDA_EXTRACTED_DATA["entities"],
    "key_findings": [
        "Status: fully executed",
        *PRIOR_NDA_EXTRACTED_DATA["key_findings"],
    ],
}

# Minimal 1×1 PNG (valid image bytes for analyze_image tool routing).
ACME_LOGO_PNG_BYTES = bytes(
    [
        0x89,
        0x50,
        0x4e,
        0x47,
        0x0d,
        0x0a,
        0x1a,
        0x0a,
        0x00,
        0x00,
        0x00,
        0x0d,
        0x49,
        0x48,
        0x44,
        0x52,
        0x00,
        0x00,
        0x00,
        0x01,
        0x00,
        0x00,
        0x00,
        0x01,
        0x08,
        0x02,
        0x00,
        0x00,
        0x00,
        0x90,
        0x77,
        0x53,
        0xde,
        0x00,
        0x00,
        0x00,
        0x0c,
        0x49,
        0x44,
        0x41,
        0x54,
        0x08,
        0xd7,
        0x63,
        0xf8,
        0xcf,
        0xc0,
        0x00,
        0x00,
        0x03,
        0x01,
        0x01,
        0x00,
        0x18,
        0xdd,
        0x8d,
        0xb4,
        0x00,
        0x00,
        0x00,
        0x00,
        0x49,
        0x45,
        0x4e,
        0x44,
        0xae,
        0x42,
        0x60,
        0x82,
    ]
)
