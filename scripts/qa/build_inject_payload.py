"""Build JSON body for POST /api/admin/inject-fake (used by kg_full_capabilities.sh)."""

from __future__ import annotations

import base64
import json
import pathlib
import sys

MIME_BY_SUFFIX = {
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".png": "image/png",
    ".txt": "text/plain",
    ".pdf": "application/pdf",
}


def main() -> None:
    if len(sys.argv) < 8:
        print(
            "usage: build_inject_payload.py MAILBOX MSG_ID THREAD_ID SUBJECT BODY "
            "FROM_ADDR [IN_REPLY_TO] [ATTACH_NAMES_COMMA_SEP]",
            file=sys.stderr,
        )
        sys.exit(2)

    mailbox = sys.argv[1]
    msg_id = sys.argv[2]
    thread_id = sys.argv[3]
    subject = sys.argv[4]
    body = sys.argv[5]
    from_addr = sys.argv[6]
    in_reply_to = sys.argv[7] if sys.argv[7] else ""
    attach_names = sys.argv[8].split(",") if len(sys.argv) > 8 and sys.argv[8] else []

    fixture_dir = pathlib.Path(__file__).resolve().parent / "fixtures"
    attachments: list[dict] = []
    for name in attach_names:
        name = name.strip()
        if not name:
            continue
        path = fixture_dir / name
        if not path.is_file():
            print(f"missing fixture: {path}", file=sys.stderr)
            sys.exit(1)
        raw = path.read_bytes()
        suffix = path.suffix.lower()
        attachments.append(
            {
                "part_id": name,
                "name": name,
                "mime_type": MIME_BY_SUFFIX.get(suffix, "application/octet-stream"),
                "size": len(raw),
                "data_b64": base64.standard_b64encode(raw).decode("ascii"),
            }
        )

    msg: dict = {
        "provider_message_id": msg_id,
        "provider_thread_id": thread_id,
        "subject": subject,
        "body_text": body,
        "from_addr": from_addr,
        "to_addrs": ["qa@firm.test"],
        "cc_addrs": ["jane@acme.com"],
    }
    if in_reply_to:
        msg["in_reply_to"] = in_reply_to
        msg["references"] = [in_reply_to]
    if attachments:
        msg["attachments"] = attachments

    print(json.dumps({"mailbox_external_id": mailbox, "message": msg}))


if __name__ == "__main__":
    main()
