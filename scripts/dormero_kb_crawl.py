#!/usr/bin/env python3

from collections import deque
from html.parser import HTMLParser
import json
import os
from urllib.parse import urljoin, urlsplit, urlunsplit
from urllib.request import Request, urlopen

from dotenv import load_dotenv

load_dotenv()

start_domain = [
    "https://www.dormero.de/ueber-dormero/",
    # "https://www.dormero.de/hotel-coburg/",
    # "https://www.dormero.de/hotel-aalen/",
]
parent_folder_id = "fDODk9WdlxVjeoEZ0HIY"
xi_api_key = os.getenv("ELEVEN_LABS_API_KEY", "")
elevenlabs_url_ingest_endpoint = "https://api.elevenlabs.io/v1/convai/knowledge-base/url"
ignore_subdomains = [
    "impressum",
    "datenschutz",
    "agb",
    "kontakt",
    "gutschein",
    "newsletter",
    "jobs",
    "karriere",
    "buchung",
    "book",
    "en"
    #"ueber-dormero"
]
ignore_subdomains_lower = tuple(token.lower() for token in ignore_subdomains)
request_timeout_seconds = 10
max_pages_per_domain = 250
max_subpage_depth = 4


class LinkParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.links = set()

    def handle_starttag(self, tag, attrs) -> None:
        if tag != "a":
            return
        for key, value in attrs:
            if key == "href" and value:
                self.links.add(value.strip())


def normalize_url(url: str) -> str:
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"}:
        return ""
    clean = urlunsplit((parsed.scheme, parsed.netloc.lower(), parsed.path or "/", "", ""))
    if clean.endswith("/") and clean.count("/") > 2:
        clean = clean.rstrip("/")
    return clean


def is_ignored(url: str) -> bool:
    lowered = url.lower()
    return any(token in lowered for token in ignore_subdomains_lower)


def is_page_like(url: str) -> bool:
    path = urlsplit(url).path.lower()
    blocked_ext = (
        ".jpg",
        ".jpeg",
        ".png",
        ".gif",
        ".svg",
        ".webp",
        ".pdf",
        ".zip",
        ".mp3",
        ".mp4",
        ".css",
        ".js",
        ".json",
        ".xml",
    )
    return not path.endswith(blocked_ext)


def fetch_links(page_url: str) -> tuple[bool, set[str]]:
    req = Request(page_url, headers={"User-Agent": "Mozilla/5.0 (Dormero-KB-Crawler)"})
    try:
        with urlopen(req, timeout=request_timeout_seconds) as response:
            content_type = response.headers.get("Content-Type", "")
            if "text/html" not in content_type:
                return False, set()
            html = response.read().decode("utf-8", errors="ignore")
    except Exception as exc:  # noqa: BLE001
        print(f"  [warn] skipped {page_url} ({exc})")
        return False, set()

    parser = LinkParser()
    parser.feed(html)

    links = set()
    current_path = urlsplit(normalize_url(page_url)).path.rstrip("/")
    current_slug = current_path.split("/")[-1].lower() if current_path else ""

    for href in parser.links:
        href = href.strip()
        if not href:
            continue
        lowered = href.lower()
        if lowered.startswith(("#", "mailto:", "tel:", "javascript:")):
            continue
        if lowered in {".", "./", "..", "../"}:
            continue
        if "/" not in lowered.strip("/") and lowered.strip("/") == current_slug:
            # Avoid self-referential relative links like "ueber-dormero" on /ueber-dormero/.
            continue

        if href.startswith(("http://", "https://")):
            absolute = normalize_url(href)
        else:
            absolute = normalize_url(urljoin(page_url, href))
        if absolute:
            links.add(absolute)
    return True, links


def is_in_hotel_scope(start_url: str, candidate_url: str) -> bool:
    root = urlsplit(normalize_url(start_url))
    candidate = urlsplit(normalize_url(candidate_url))

    if not root.netloc or not candidate.netloc:
        return False
    if candidate.netloc != root.netloc:
        return False

    root_segments = [segment for segment in root.path.split("/") if segment]
    candidate_segments = [segment for segment in candidate.path.split("/") if segment]

    if not root_segments:
        return False
    if len(candidate_segments) < len(root_segments):
        return False
    if candidate_segments[: len(root_segments)] != root_segments:
        return False

    trailing_segments = candidate_segments[len(root_segments) :]
    if trailing_segments and trailing_segments[0] == root_segments[-1]:
        return False
    if len(trailing_segments) > max_subpage_depth:
        return False
    if len(trailing_segments) != len(set(trailing_segments)):
        return False
    if any("hotel-" in segment for segment in trailing_segments):
        return False

    return True


def crawl_hotel(start_url: str) -> list[str]:
    root = normalize_url(start_url)
    if not root:
        return []

    seen = {root}
    valid = set()
    queue = deque([root])

    while queue and len(seen) < max_pages_per_domain:
        current = queue.popleft()
        ok, candidates = fetch_links(current)
        if not ok:
            continue

        valid.add(current)

        for candidate in candidates:
            if not is_in_hotel_scope(start_url, candidate):
                continue
            if is_ignored(candidate) or not is_page_like(candidate):
                continue
            if candidate in seen:
                continue
            seen.add(candidate)
            queue.append(candidate)

    return sorted(valid)


def build_document_name(start_url: str, url: str) -> str:
    root = normalize_url(start_url)
    current = normalize_url(url)

    root_path = urlsplit(root).path.rstrip("/")
    current_path = urlsplit(current).path.rstrip("/")

    hotel_slug = root_path.strip("/") or urlsplit(root).netloc

    if current_path == root_path:
        page_slug = "overview"
    else:
        relative = current_path[len(root_path) :].strip("/")
        page_slug = relative or "overview"

    return f"{hotel_slug} | {page_slug}"


def ingest_url_to_elevenlabs(start_url: str, url: str) -> bool:
    if not xi_api_key.strip():
        print("  [warn] xi_api_key is empty, skipping ingestion.")
        return False

    doc_name = build_document_name(start_url, url)
    payload = {"url": url, "name": doc_name, "parent_folder_id": parent_folder_id}
    body = json.dumps(payload).encode("utf-8")
    req = Request(
        elevenlabs_url_ingest_endpoint,
        data=body,
        method="POST",
        headers={
            "xi-api-key": xi_api_key.strip(),
            "Content-Type": "application/json",
        },
    )

    try:
        with urlopen(req, timeout=request_timeout_seconds) as response:
            raw = response.read().decode("utf-8", errors="ignore")
            print(f"  [ok] {doc_name} -> {response.status}")
            print(f"       url: {url}")
            if raw:
                print(f"       response: {raw[:250]}")
            return True
    except Exception as exc:  # noqa: BLE001
        print(f"  [error] failed to ingest {doc_name} ({exc})")
        print(f"       url: {url}")
        return False


def main() -> None:
    print(f"Parent folder id: {parent_folder_id}\n")

    for root in start_domain:
        print(f"Crawling start domain: {root}")
        subpages = crawl_hotel(root)

        print(f"Total pages found: {len(subpages)}")
        for url in subpages:
            print(f"  - {url}")

        print("Ingesting discovered URLs to ElevenLabs KB:")
        ok_count = 0
        #break
        for url in subpages:
            if ingest_url_to_elevenlabs(root, url):
                ok_count += 1
        print(f"Ingestion complete: {ok_count}/{len(subpages)} succeeded.")
        print()


if __name__ == "__main__":
    main()
