"""CLI entrypoint wrapper. Delegates to uvicorn when invoked as a script."""

from __future__ import annotations


def main() -> None:
    import uvicorn

    uvicorn.run("legalbot.api.main:app", host="0.0.0.0", port=8000)


if __name__ == "__main__":
    main()
