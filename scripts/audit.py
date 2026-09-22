#!/usr/bin/env python3
"""Audit static frontend source or fetched HTML; never certify accessibility."""

import argparse
import json
import sys
import subprocess
import shutil
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

from report import build_report, write_reports


def parser():
    result = argparse.ArgumentParser(
        description="Accessibility audit: rendered URL with local Playwright/axe, or static frontend source.",
        epilog="Exit codes: 0 = no fail/warning findings, 1 = findings, 2 = operational error. "
               "Exit 0 never means WCAG or legal compliance.",
    )
    target = result.add_mutually_exclusive_group(required=True)
    target.add_argument("--path", help="Frontend file or project directory")
    target.add_argument("--url", help="HTTP(S) page rendered with the local browser and axe-core")
    result.add_argument("--format", choices=("markdown", "json"), default="markdown",
                        help="Console format; both report files are always written")
    result.add_argument("--output", default="accessibility-report", help="Report directory")
    result.add_argument("--static", action="store_true", help="Explicit partial HTML-only URL audit")
    result.add_argument("--prepare", action="store_true", help="Prepare/reuse isolated dependencies automatically")
    result.add_argument("--timeout", type=float, default=30, help="Rendered target budget in seconds (2-120)")
    result.add_argument("--baseline", help="Previous JSON report for conservative before/after comparison")
    return result


def now():
    return datetime.now(timezone.utc).isoformat()


def rendered_scan(url, timeout, selectors):
    node = shutil.which("node")
    if not node:
        raise RuntimeError("חסר Node.js. התקינו Node.js 20 ומעלה והפעילו שוב עם --prepare")
    request = {"url": url, "timeout_ms": int(timeout * 1000), "selectors": selectors}
    process = subprocess.run([node, str(Path(__file__).with_name("browser_scan.cjs"))],
                             input=json.dumps(request), text=True, capture_output=True,
                             timeout=timeout + 5)
    try:
        data = json.loads(process.stdout)
    except (ValueError, TypeError):
        raise RuntimeError("סריקת הדפדפן לא החזירה תוצאה תקינה. הפעילו עם --prepare. "
                           + process.stderr[-500:])
    if not isinstance(data, dict) or not isinstance(data.get("run"), dict) or not isinstance(data.get("errors"), list):
        raise ValueError("Invalid browser result contract")
    if not isinstance(data.get("ok"), bool):
        raise ValueError("Invalid browser completion status")
    if process.returncode and data["ok"]:
        raise ValueError("Browser process failed despite a success response")
    if data["ok"] and (not isinstance(data.get("axe"), dict) or not isinstance(data.get("html"), str)):
        raise ValueError("Browser result lacks scan evidence")
    return data


def axe_findings(axe, url):
    from report import (finding, ARIA, CONTRAST, FORMS, HEADINGS, IMAGES, KEYBOARD,
                        LANGUAGE, LINKS, STRUCTURE, TABLES)
    results = []
    for collection, status in (("violations", "fail"), ("incomplete", "human-review-required"), ("passes", "pass")):
        rules = axe.get(collection)
        if not isinstance(rules, list):
            raise ValueError("Invalid axe rule collection: " + collection)
        for rule in rules:
            if not isinstance(rule, dict) or not isinstance(rule.get("id"), str) or not isinstance(rule.get("nodes"), list):
                raise ValueError("Invalid axe rule")
            rule_id = rule["id"]
            category = ARIA
            for words, candidate in ((('color','contrast'), CONTRAST), (('image','alt','video','audio'), IMAGES),
                                     (('label','input','select','form'), FORMS), (('heading',), HEADINGS),
                                     (('link','button'), LINKS), (('table','th-','td-'), TABLES),
                                     (('lang',), LANGUAGE), (('tabindex','focus','keyboard'), KEYBOARD),
                                     (('landmark','region','document-title','bypass'), STRUCTURE)):
                if any(word in rule_id for word in words):
                    category = candidate
                    break
            nodes = rule['nodes']
            if not all(isinstance(n, dict) and isinstance(n.get('target'), list) for n in nodes):
                raise ValueError("Invalid axe node")
            def location(node):
                target = node.get('target', [])
                selector = target[0] if len(target) == 1 and isinstance(target[0], str) else json.dumps(target)
                return {'url': url, 'selector': selector}
            locations = [location(n) for n in nodes]
            batches = [nodes] if status == 'pass' else [[n] for n in nodes]
            for batch in batches:
                if not batch:
                    continue
                node = batch[0]
                severity = rule.get('impact') or 'moderate'
                if severity not in ('critical','serious','moderate','minor'):
                    severity = 'moderate'
                if status == 'pass':
                    severity = 'info'
                wcag = '; '.join(t for t in rule.get('tags', []) if isinstance(t, str) and t.startswith('wcag'))
                item = finding('axe-' + rule_id, category, severity, status,
                               'human-verification-required' if status == 'human-review-required' else 'automatically-verified',
                               wcag or None, location(node), node.get('html') or rule.get('help') or rule_id,
                               (node.get('failureSummary') or rule.get('description') or rule_id) +
                               ' This result covers only the recorded rendered page and state.',
                               rule.get('help') or 'Review the affected element and rerun axe.')
                item.update(engine='axe', rule_id='axe-' + rule_id,
                            locations=locations if status == 'pass' else [location(node)],
                            help_url=rule.get('helpUrl', ''), title=rule.get('help') or rule_id)
                results.append(item)
    return results


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
    arguments = list(sys.argv[1:] if argv is None else argv)
    args = parser().parse_args(arguments)
    findings, errors = [], []
    files_scanned = 0
    mode = "source" if args.path else "url"
    target = args.path or args.url
    run = {"status": "not-performed", "started_at": now(), "original_url": args.url,
           "final_url": None, "rendered": False, "engines": {}, "pages": [], "states": [],
           "untested": ["Other pages, authenticated states, keyboard and screen-reader interactions"],
           "observed_selectors": {}, "attempts": 0}
    metadata = {"run": run, "rendered": False,
                "network_scope": "one supplied URL and its page resources" if args.url else "none"}
    baseline = None
    try:
        if not 2 <= args.timeout <= 120:
            raise ValueError("--timeout must be between 2 and 120 seconds")
        if args.prepare:
            from bootstrap import prepare
            managed = prepare(needs_browser=bool(args.url and not args.static))
            return subprocess.call([managed, str(Path(__file__).resolve()),
                                    *[arg for arg in arguments if arg != "--prepare"]])
        if args.baseline:
            baseline = json.loads(Path(args.baseline).read_text(encoding="utf-8"))
            if not isinstance(baseline, dict) or not isinstance(baseline.get('findings'), list):
                raise ValueError("Baseline must be an audit JSON report")
        import bs4
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
            run.update(status='partial', pages=list(files), states=['source'], engines={'static':'0.2.0'})
        elif args.static:
            html, final_url = fetch_html(args.url)
            metadata["final_url"] = final_url
            findings.extend(scan_html(html, {"url": final_url}))
            files_scanned = 1
            run.update(status='partial', final_url=final_url, pages=[final_url], states=['static-html'],
                       engines={'static':'0.2.0'}, attempts=1)
        else:
            selectors = []
            if baseline:
                for item in baseline['findings']:
                    if not isinstance(item, dict):
                        raise ValueError("Invalid baseline finding")
                    if item.get('engine') != 'axe' or item.get('status') not in ('fail', 'warning'):
                        continue
                    for loc in item.get('locations') or [item.get('location', {})]:
                        if isinstance(loc, dict) and isinstance(loc.get('selector'), str):
                            selectors.append(loc['selector'])
            scanned = rendered_scan(args.url, args.timeout, list(set(selectors)))
            run.update(scanned['run'])
            errors.extend(str(e) for e in scanned['errors'])
            if scanned['ok']:
                findings.extend(axe_findings(scanned['axe'], run.get('final_url') or args.url))
                from report import HEBREW, LANGUAGE, STATEMENT
                supplement = scan_html(scanned['html'], {'url': run.get('final_url') or args.url})
                findings.extend(f for f in supplement if f['category'] in (HEBREW, LANGUAGE, STATEMENT))
                files_scanned = 1
                metadata['rendered'] = True
                metadata['final_url'] = run.get('final_url')
            elif not errors:
                errors.append('הבדיקה לא בוצעה: הדפדפן לא השלים סריקה.')
    except ImportError as exc:
        errors.append("Missing dependency {}. Run with --prepare, or: python -m pip install -r requirements.txt".format(exc.name))
    except Exception as exc:
        errors.append("{}: {}".format(type(exc).__name__, exc))
    run['finished_at'] = now()
    if errors:
        run['status'] = 'partial' if files_scanned else 'not-performed'
    result = build_report(target=target, mode=mode, findings=findings,
                          files_scanned=files_scanned, errors=errors, metadata=metadata)
    if baseline is not None:
        try:
            from compare import compare_reports
            result['comparison'] = compare_reports(baseline, result)
        except (ValueError, TypeError) as exc:
            errors.append('Baseline comparison failed: ' + str(exc))
            result['errors'] = list(errors)
            result['summary']['operational_errors'] = len(errors)
            result['metadata']['run']['status'] = 'partial'
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
