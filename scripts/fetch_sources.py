#!/usr/bin/env python3
"""Reproduce the five reviewed supplemental HTML captures without crawling.

This downloads local research copies, not permission to republish the articles.
Publisher-restricted sources and URLs outside the reviewed allowlist are refused.
TLS certificate verification remains enabled and HTTP errors are not bypassed.
"""

import argparse
import csv
from datetime import datetime, timezone
import hashlib
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import ssl
import sys
import urllib.error
import urllib.parse
import urllib.request


ROOT = Path(__file__).resolve().parents[1]
REVIEWED_URLS = {
    "D035": "https://hudsoncountyview.com/jersey-city-council-approves-realpage-ban-and-increasing-benefits-for-laborers/",
    "D037": "https://www.morganlewis.com/pubs/2026/08/algorithmic-rent-pricing-litigation-expands-under-new-state-and-local-laws",
    "D059": "https://www.wbur.org/news/2026/06/23/massachusetts-high-court-rent-control-ballot-question-struck",
    "D086": "https://www.ocbj.com/real-estate/santa-ana-bans-landlords-from-using-ai-apartment-rent-pricing-software/",
    "D087": "https://www.publicceo.com/2026/02/santa-ana-city-council-continues-to-strengthen-tenant-protections-by-banning-anticompetitive-rent-setting-software/",
}
RESTRICTED_HOSTS = {
    "ecode360.com": "https://ecode360.com/docs/TOS.html restricts copying for public/non-profit use and derivative works.",
    "gocodebook.com": "https://gocodebook.com/terms section 2.2(d) prohibits automated access outside documented APIs.",
}
MAX_BYTES = 6_000_000


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def hostname(url):
    host = (urllib.parse.urlsplit(url).hostname or "").lower()
    return host[4:] if host.startswith("www.") else host


class TextExtractor(HTMLParser):
    """Keep page text and copyright footers; omit executable/style/head content."""

    IGNORED = {"script", "style", "head", "svg", "noscript"}
    BLOCKS = {
        "p", "div", "br", "h1", "h2", "h3", "h4", "h5", "h6", "li", "tr",
        "td", "th", "section", "article", "header", "footer", "blockquote",
    }

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.skip_depth = 0

    def handle_starttag(self, tag, attrs):
        if tag in self.IGNORED:
            self.skip_depth += 1
        if not self.skip_depth and tag in self.BLOCKS:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in self.IGNORED and self.skip_depth:
            self.skip_depth -= 1
        if not self.skip_depth and tag in self.BLOCKS:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self.skip_depth:
            self.parts.append(data)

    def text(self):
        return "\n".join(
            re.sub(r"\s+", " ", line).strip()
            for line in "".join(self.parts).splitlines()
            if line.strip()
        )


class ReviewedRedirects(urllib.request.HTTPRedirectHandler):
    def __init__(self, original_url):
        self.allowed_host = hostname(original_url)

    def redirect_request(self, request, response, code, message, headers, new_url):
        if urllib.parse.urlsplit(new_url).scheme != "https" or hostname(new_url) != self.allowed_host:
            raise ValueError("Redirect leaves the reviewed HTTPS source host: " + new_url)
        return super().redirect_request(request, response, code, message, headers, new_url)


def write_json(path, value):
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def capture(row, output_dir, timeout):
    """Return an attempt record; write a document only after a complete response."""
    url = row["url"]
    result = {
        "doc_id": row["doc_id"], "originalurl": url, "finalurl": None,
        "timestamp": utc_now(), "state": "fetch_error", "reason": "",
    }
    try:
        opener = urllib.request.build_opener(
            ReviewedRedirects(url),
            urllib.request.HTTPSHandler(context=ssl.create_default_context()),
        )
        request = urllib.request.Request(url, headers={"User-Agent": "RentAgent-Hackathon-SourceReview/1.0"})
        with opener.open(request, timeout=timeout) as response:
            result.update(finalurl=response.url, http_status=response.status,
                          content_type=response.headers.get("Content-Type", ""))
            if response.headers.get_content_type() not in {"text/html", "application/xhtml+xml"}:
                raise ValueError("Expected public HTML; received " + result["content_type"])
            body = response.read(MAX_BYTES + 1)
            if len(body) > MAX_BYTES:
                raise ValueError("Response exceeds size limit; refusing a truncated capture.")
            charset = response.headers.get_content_charset() or "utf-8"
        parser = TextExtractor()
        parser.feed(body.decode(charset, errors="replace"))
        parser.close()
        text = parser.text()
        if len(text) < 500 or re.match(r"^(just a moment|access denied|attention required)", text, re.I):
            result.update(state="insufficient_content", reason="Response lacks readable article text or is an access-control page.")
            return result
        result["timestamp"] = utc_now()
        captured = "SOURCE: " + url + "\nRETRIEVED: " + result["timestamp"] + "\n\n" + text + "\n"
        target = output_dir / (row["doc_id"] + ".txt")
        temporary = target.with_suffix(".txt.tmp")
        temporary.write_text(captured, encoding="utf-8")
        temporary.replace(target)
        result.update(
            state="captured",
            reason="One public HTML request; mechanical html.parser extraction; no crawling, login, or access-control workaround.",
            text_file=str(target), body_characters=len(text),
            sha256=hashlib.sha256(captured.encode("utf-8")).hexdigest(),
            raw_sha256=hashlib.sha256(body).hexdigest(),
            parser_version="visible-html-v1",
            rights_note="Local research capture does not confer permission to republish the article; copyright footer retained.",
        )
        if row["doc_id"] == "D037":
            result["terms_url"] = "https://www.morganlewis.com/terms-of-use"
            result["terms_note"] = "Personal noncommercial downloads only, with copyright notices retained; no redistribution permission inferred."
    except urllib.error.HTTPError as error:
        result.update(state="http_error", http_status=error.code, finalurl=error.url,
                      reason="HTTP {} returned; no workaround attempted.".format(error.code))
    except (urllib.error.URLError, ValueError, LookupError, OSError) as error:
        result.update(state="fetch_error", reason=type(error).__name__ + ": " + str(error))
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=ROOT / "participant-final-no-hour16_v5/corpus/corpus_manifest.csv")
    parser.add_argument("--documents", nargs="+", default=list(REVIEWED_URLS),
                        help="Reviewed document IDs, separated by spaces or commas (default: the five reviewed sources).")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "data/supplemental")
    parser.add_argument("--timeout", type=float, default=20.0)
    parser.add_argument("--dry-run", action="store_true", help="Validate the manifest and print the request plan without network or file writes.")
    args = parser.parse_args(argv)
    if args.timeout <= 0:
        parser.error("--timeout must be positive")
    requested = list(dict.fromkeys(part.strip() for item in args.documents for part in item.split(",") if part.strip()))
    if not requested:
        parser.error("at least one document ID is required")
    with args.manifest.open(encoding="utf-8-sig", newline="") as handle:
        manifest = {row["doc_id"]: row for row in csv.DictReader(handle)}
    selected = []
    for doc_id in requested:
        if doc_id not in manifest:
            parser.error("Unknown document ID: " + doc_id)
        row = manifest[doc_id]
        host = hostname(row["url"])
        for blocked_host, reason in RESTRICTED_HOSTS.items():
            if host == blocked_host or host.endswith("." + blocked_host):
                parser.error("Refusing {}: {}".format(doc_id, reason))
        if doc_id not in REVIEWED_URLS or row["url"] != REVIEWED_URLS[doc_id]:
            parser.error("Refusing {}: URL is outside the five reviewed sources.".format(doc_id))
        selected.append(row)
    if args.dry_run:
        print(json.dumps([{"doc_id": row["doc_id"], "url": row["url"]} for row in selected], indent=2))
        return 0
    args.output_dir.mkdir(parents=True, exist_ok=True)
    log_path = args.output_dir / "fetch_log.json"
    log = json.loads(log_path.read_text(encoding="utf-8")) if log_path.exists() else {"entries": []}
    entries = {entry["doc_id"]: entry for entry in log["entries"]}
    failed = False
    for row in selected:
        attempt = capture(row, args.output_dir, args.timeout)
        previous = entries.get(row["doc_id"])
        if previous and previous.get("state") == "captured" and attempt["state"] != "captured":
            attempt["previous_capture"] = previous
            attempt["reason"] += " Previously captured local text was retained unchanged."
        entries[row["doc_id"]] = attempt
        log.update(generated_at=utc_now(), as_of=log.get("as_of", "2026-10-01"),
                   entries=sorted(entries.values(), key=lambda entry: entry["doc_id"]))
        write_json(log_path, log)
        print(row["doc_id"], attempt["state"], attempt["reason"], flush=True)
        failed |= attempt["state"] != "captured"
    return int(failed)


if __name__ == "__main__":
    sys.exit(main())
