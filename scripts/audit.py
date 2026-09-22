#!/usr/bin/env python3
"""Audit rendered URLs or static frontend source; never certify accessibility."""

import argparse
import json
import sys
import subprocess
import shutil
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

from report import VERSION, build_report, write_reports


def parser():
    result = argparse.ArgumentParser(
        description="Accessibility audit: public website, one rendered page, or static frontend source.",
        epilog="Exit codes: 0 = no fail/warning findings, 1 = findings, 2 = operational error. "
               "Exit 0 never means WCAG or legal compliance.",
    )
    target = result.add_mutually_exclusive_group(required=True)
    target.add_argument("--path", help="Frontend file or project directory")
    target.add_argument("--url", help="HTTP(S) page rendered with the local browser and axe-core")
    target.add_argument("--site", help="Discover and audit public same-origin pages in one site report")
    result.add_argument("--format", choices=("markdown", "json"), default="markdown",
                        help="Console format; all three report files are always written")
    result.add_argument("--output", default="accessibility-report", help="Report directory")
    result.add_argument("--static", action="store_true", help="Explicit partial HTML-only URL audit")
    result.add_argument("--prepare", action="store_true", help="Prepare/reuse isolated dependencies automatically")
    result.add_argument("--timeout", type=float, default=30, help="Rendered target budget in seconds (2-120)")
    result.add_argument("--baseline", help="Previous JSON report for conservative before/after comparison")
    result.add_argument("--max-pages", type=int, default=100, help="Site page budget (1-5000; default 100)")
    result.add_argument("--max-seconds", type=float, default=600, help="Site time budget (5-7200 seconds; default 600)")
    return result


def now():
    return datetime.now(timezone.utc).isoformat()


def rendered_scan(url, timeout, selectors, allowed_origin=None):
    node = shutil.which("node")
    if not node:
        raise RuntimeError("חסר Node.js. התקינו Node.js 20 ומעלה והפעילו שוב עם --prepare")
    request = {"url": url, "timeout_ms": int(timeout * 1000), "selectors": selectors}
    if allowed_origin is not None:
        request["allowed_origin"] = allowed_origin
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
    for collection, collection_status in (("violations", "fail"), ("incomplete", "human-review-required"), ("passes", "pass")):
        rules = axe.get(collection)
        if not isinstance(rules, list):
            raise ValueError("Invalid axe rule collection: " + collection)
        for rule in rules:
            if not isinstance(rule, dict) or not isinstance(rule.get("id"), str) or not isinstance(rule.get("nodes"), list):
                raise ValueError("Invalid axe rule")
            rule_id = rule["id"]
            tags = rule.get("tags", [])
            if not isinstance(tags, list) or not all(isinstance(tag, str) for tag in tags):
                raise ValueError("Invalid axe rule tags")
            wcag_tags = [tag for tag in tags if tag.startswith("wcag")]
            best_practice = "best-practice" in tags and not wcag_tags
            status = "warning" if best_practice and collection_status == "fail" else collection_status
            category = ARIA
            for words, candidate in ((('target-size','skip-link'), KEYBOARD), (('color','contrast'), CONTRAST), (('image','alt','video','audio'), IMAGES),
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
                wcag = '; '.join(wcag_tags)
                item = finding('axe-' + rule_id, category, severity, status,
                               'human-verification-required' if status == 'human-review-required' else
                               'heuristic' if best_practice else 'automatically-verified',
                               wcag or None, location(node), node.get('html') or rule.get('help') or rule_id,
                               (node.get('failureSummary') or rule.get('description') or rule_id) +
                               ' This result covers only the recorded rendered page and state.',
                               rule.get('help') or 'Review the affected element and rerun axe.')
                item.update(engine='axe', rule_id='axe-' + rule_id,
                            locations=locations if status == 'pass' else [location(node)],
                            help_url=rule.get('helpUrl', ''), title=rule.get('help') or rule_id,
                            axe_tags=tags, standards_basis='best-practice' if best_practice else
                            'WCAG' if wcag_tags else 'other')
                results.append(item)
    return results



def rendered_supplement(scanned, axe_results, url):
    """Add only evidence not already represented by the rendered axe run."""
    from html_scanner import scan_html
    from report import HEBREW, STATEMENT, KEYBOARD, finding

    language_heuristics = {'lang-hebrew-mismatch', 'lang-hebrew-declared-latin-content'}
    supplement = scan_html(scanned['html'], {'url': url})
    results = [item for item in supplement
               if item['category'] in (HEBREW, STATEMENT) or item['id'] in language_heuristics]
    skip_links = scanned['run'].get('skip_links', [])
    if not isinstance(skip_links, list):
        raise ValueError('Invalid rendered skip-link evidence')
    covered = {loc.get('selector') for item in axe_results
               if item.get('rule_id') == 'axe-skip-link' and item['status'] in ('fail', 'warning')
               for loc in item.get('locations', [item['location']])}
    for link in skip_links:
        if (not isinstance(link, dict) or not isinstance(link.get('selector'), str)
                or not isinstance(link.get('fragment'), str)
                or not isinstance(link.get('href'), str)
                or not isinstance(link.get('target_exists'), bool)
                or not isinstance(link.get('html'), str)):
            raise ValueError('Invalid rendered skip-link entry')
        if link['selector'] in covered:
            continue
        exists = link['target_exists']
        item = finding('skip-link-target-missing', KEYBOARD, 'info' if exists else 'serious',
                       'pass' if exists else 'fail',
                       'automatically-verified', '2.4.1 Bypass Blocks',
                       {'url': url, 'selector': link['selector']}, link['html'],
                       'The rendered skip link points to #' + link['fragment'] +
                       (' and a matching fragment target exists in the recorded document. ' if exists else
                        ' but no matching fragment target exists in the recorded document. ') +
                       'Keyboard activation and other bypass mechanisms were not tested.',
                       'Give the main content the referenced id, or correct the skip-link href; '
                       'then verify keyboard focus transfer.')
        item.update(engine='rendered-dom', rule_id='skip-link-target-missing',
                    standards_basis='WCAG', locations=[item['location']])
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
                      headers={"User-Agent": "israeli-accessibility-auditor/" + VERSION}) as response:
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
    mode = "source" if args.path else "site" if args.site else "url"
    target = args.path or args.site or args.url
    run = {"status": "not-performed", "started_at": now(), "original_url": args.site or args.url,
           "final_url": None, "rendered": False, "engines": {}, "pages": [], "states": [],
           "untested": ["Other pages, authenticated states, keyboard and screen-reader interactions"],
           "observed_selectors": {}, "attempts": 0}
    metadata = {"run": run, "rendered": False,
                "network_scope": "discovered public same-origin pages and their resources" if args.site else
                "one supplied URL and its page resources" if args.url else "none"}
    baseline = None
    try:
        if not 2 <= args.timeout <= 120:
            raise ValueError("--timeout must be between 2 and 120 seconds")
        if not 1 <= args.max_pages <= 5000:
            raise ValueError("--max-pages must be between 1 and 5000")
        if not 5 <= args.max_seconds <= 7200:
            raise ValueError("--max-seconds must be between 5 and 7200 seconds")
        if args.static and args.site:
            raise ValueError("--static cannot be combined with --site; use --url for one-page HTML auditing")
        if args.prepare:
            from bootstrap import prepare
            managed = prepare(needs_browser=bool((args.url or args.site) and not args.static))
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
            run.update(status='partial', pages=list(files), states=['source'], engines={'static': VERSION})
        elif args.static:
            html, final_url = fetch_html(args.url)
            metadata["final_url"] = final_url
            findings.extend(scan_html(html, {"url": final_url}))
            files_scanned = 1
            run.update(status='partial', final_url=final_url, pages=[final_url], states=['static-html'],
                       engines={'static': VERSION}, attempts=1)
        elif args.site:
            from site_scan import scan_site
            selectors = []
            if baseline:
                for item in baseline['findings']:
                    if not isinstance(item, dict):
                        raise ValueError("Invalid baseline finding")
                    for loc in item.get('locations') or [item.get('location', {})]:
                        if isinstance(loc, dict) and isinstance(loc.get('selector'), str):
                            selectors.append(loc['selector'])
            def scan_page(url, budget, allowed_origin):
                print("בודק עמוד: " + url, file=sys.stderr, flush=True)
                return rendered_scan(url, budget, list(set(selectors)), allowed_origin)
            scanned_site = scan_site(args.site, scan_page, timeout=args.timeout,
                                     max_pages=args.max_pages, max_seconds=args.max_seconds)
            run.update(scanned_site['run'])
            errors.extend(str(error) for error in scanned_site['errors'])
            if (run.get('status') != 'completed'
                    or not isinstance(run.get('site'), dict)
                    or run['site'].get('complete') is not True) and not errors:
                errors.append('סריקת האתר לא הושלמה. בדקו בדוח אילו עמודים נבדקו ומה נותר לבדיקה.')
            run['observed_selectors_by_page'] = {}
            for scanned in scanned_site['results']:
                page_run = scanned['run']
                page_url = page_run.get('final_url') or page_run.get('original_url')
                page_findings = axe_findings(scanned['axe'], page_url)
                page_findings.extend(rendered_supplement(scanned, page_findings, page_url))
                findings.extend(page_findings)
                files_scanned += 1
                run['observed_selectors_by_page'][page_url] = page_run.get('observed_selectors', {})
            metadata['rendered'] = bool(files_scanned)
            metadata['final_url'] = run.get('final_url')
        else:
            selectors = []
            if baseline:
                for item in baseline['findings']:
                    if not isinstance(item, dict):
                        raise ValueError("Invalid baseline finding")
                    if item.get('engine') not in ('axe', 'rendered-dom') or item.get('status') not in ('fail', 'warning'):
                        continue
                    for loc in item.get('locations') or [item.get('location', {})]:
                        if isinstance(loc, dict) and isinstance(loc.get('selector'), str):
                            selectors.append(loc['selector'])
            scanned = rendered_scan(args.url, args.timeout, list(set(selectors)))
            run.update(scanned['run'])
            errors.extend(str(e) for e in scanned['errors'])
            if scanned['ok']:
                findings.extend(axe_findings(scanned['axe'], run.get('final_url') or args.url))
                findings.extend(rendered_supplement(scanned, findings, run.get('final_url') or args.url))
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
            comparison_status = 'partial' if files_scanned else 'not-performed'
            result['metadata']['run']['status'] = comparison_status
            result['summary']['run_status'] = comparison_status
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
