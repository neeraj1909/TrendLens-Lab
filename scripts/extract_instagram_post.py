#!/usr/bin/env python3
"""Read one already-open, authorized Instagram post using the local CDP CLI."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from socialrum.instagram import collect_visible_posts  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Read the visible caption of one public Instagram post or reel "
            "already open in a headed CDP-controlled browser."
        ),
        epilog=(
            "Public visibility does not itself authorize automated collection. "
            "The script cannot verify permission or whether the browser is logged out."
        ),
    )
    parser.add_argument("url", help="Exact HTTPS Instagram post/reel URL, already open in the browser")
    args = parser.parse_args()
    try:
        parsed = urlsplit(args.url)
        if (
            parsed.scheme.lower() != "https"
            or (parsed.hostname or "").lower() not in {"instagram.com", "www.instagram.com"}
            or parsed.username is not None or parsed.password is not None
            or parsed.port not in (None, 443)
            or not re.fullmatch(r"/(?:p|reel)/[A-Za-z0-9_-]+/?", parsed.path)
        ):
            raise ValueError("Provide an HTTPS Instagram post or reel permalink")
        records = collect_visible_posts(args.url)
    except (ValueError, RuntimeError) as exc:
        print(f"Could not extract post: {exc}", file=sys.stderr)
        return 1
    print(json.dumps({"source": "user_opened_visible_page", "posts": records}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
