#!/usr/bin/env python3
"""Audit static frontend source or fetched HTML; never certify accessibility."""

import argparse
import json
import sys
from pathlib import Path
from urllib.parse import urlsplit

from report import build_report, write_reports


def parser():
    result = argparse.ArgumentParser(
        description="Static accessibility audit with Hebrew/RTL checks. No JavaScript rendering.",
        epilog="Exit codes: 0 = no fail/warning findings, 1 = findings, 2 = operational error. "
               "Exit 0 never means WCAG or legal compliance.",
    )
    target = result.add_mutually_exclusive_group(required=True)
    target.add_argument("--path", help="Frontend file or project directory")
    target.add_argument("--url", help="HTTP(S) page; only its returned HTML is examined")
    result.add_argument("--format", choices=("markdown", "json"), default="markdown",
                        help="Console format; both report files are always written")
    result.add_argument("--output", default="accessibility-report", help="Report directory")
    return result


def fetch_html(url):
    import requests

    parsed = urlsplit(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise ValueError("--url must be an absolute HTTP(S) URL")
    if parsed.username or parsed.password:
        raise ValueError("Do not include credentials in audit URLs")
    with requests.get(url, timeout=(5, 15), stream=True,
                      headers={"User-Agent": "israeli-accessibility-auditor/0.1.0"}) as response:
        response.raise_for_status()
        content_type = response.headers.get("Content-Type", "").lower()
        mime_type = content_type.split(";", 1)[0].strip()
        if mime_type not in ("text/html", "application/xhtml+xml"):
            raise ValueError("Target did not return an HTML Content-Type")
        chunks = []
        size = 0
        for chunk in response.iter_content(65536):
            size += len(chunk)
            if size > 5 * 1024 * 1024:
                raise ValueError("HTML exceeds the 5 MiB audit limit")
            chunks.append(chunk)
        raw = b"".join(chunks)
        # Let the HTML parser inspect charset declarations when no HTTP charset exists.
        charset = response.encoding if "charset=" in content_type else None
        if not raw.strip():
            raise ValueError("Target returned an empty HTML response; nothing was tested")
        html = raw.decode(charset, errors="replace") if charset else raw
        return html, response.url


def main(argv=None):
    args = parser().parse_args(argv)
    findings, errors = [], []
    files_scanned = 0
    mode = "source" if args.path else "url"
    target = args.path or args.url
    metadata = {"rendered": False, "network_scope": "one supplied URL and HTTP redirects" if args.url else "none"}
    try:
        import bs4  # Check the declared dependency before importing scanners.
        import requests
        from html_scanner import scan_html
        from source_scanner import collect_files, scan_file

        metadata["dependencies"] = {"beautifulsoup4": bs4.__version__, "requests": requests.__version__}
        if args.path:
            path = Path(args.path).expanduser()
            if not path.exists():
                raise ValueError("Input path does not exist")
            files = collect_files(str(path))
            if not files:
                raise ValueError("No supported frontend files found; nothing was tested")
            for filename in files:
                try:
                    findings.extend(scan_file(filename))
                    files_scanned += 1
                except (OSError, UnicodeError, ValueError) as exc:
                    errors.append("Could not scan {}: {}".format(filename, exc))
        else:
            html, final_url = fetch_html(args.url)
            metadata["final_url"] = final_url
            findings.extend(scan_html(html, {"url": final_url}))
            files_scanned = 1
    except ImportError as exc:
        errors.append("Missing dependency {}. Run: python -m pip install -r requirements.txt".format(exc.name))
    except Exception as exc:
        # A failed fetch or parser is incomplete coverage, never a clean audit.
        errors.append("{}: {}".format(type(exc).__name__, exc))

    result = build_report(target=target, mode=mode, findings=findings,
                          files_scanned=files_scanned, errors=errors, metadata=metadata)
    try:
        write_reports(result, args.output)
    except OSError as exc:
        print("Cannot write reports: {}".format(exc), file=sys.stderr)
        return 2
    if args.format == "json":
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print((Path(args.output) / "accessibility-report.md").read_text(encoding="utf-8"))
    for error in errors:
        print(error, file=sys.stderr)
    return 2 if errors else int(any(f["status"] in ("fail", "warning") for f in result["findings"]))


if __name__ == "__main__":
    sys.exit(main())
