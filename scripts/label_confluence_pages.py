"""Add or remove a Confluence label on every page under one or more root pages.

An ops script, not part of the rag_discover package: nothing imports it and it is
the only code in this repo that writes to Confluence. It exists because a
Confluence source in sources.yaml is a (space, label) pair, and labelling a page
tree by hand through the web UI does not scale.

Removing a label takes those pages out of the corpus on the next run — the
space+label listing is a complete snapshot, so the pipeline reads the absence as a
deletion (see discover/CLAUDE.md).

Runs on discover's venv (httpx is already a dependency):

    export CONFLUENCE_EMAIL=you@example.com CONFLUENCE_API_TOKEN=...
    .venv/bin/python scripts/label_confluence_pages.py \
        --base-url https://acme.atlassian.net --root-page-id 123456 --label RAG

That prints what it would do and writes nothing; add --apply to write.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from dataclasses import dataclass
from dotenv import load_dotenv

import httpx

# Load variables from .env into the process environment.
load_dotenv()

# Confluence Cloud caps page size at 250 for the v2 endpoints.
PAGE_LIMIT = 250
# 429 is the documented backpressure signal; anything past this many retries on
# one request is a real problem, not throttling.
MAX_RETRIES = 5
DEFAULT_RETRY_AFTER = 5.0


class ConfluenceError(RuntimeError):
    """An API call failed in a way that should stop the run."""


@dataclass(frozen=True)
class Page:
    id: str
    title: str
    depth: int


class ConfluenceClient:
    """Thin httpx wrapper: basic auth, cursor pagination, 429 backoff.

    Deliberately not shared with the ConfluenceReader in rag_discover — that one
    only reads and only lists, this one writes; keeping them apart means an edit
    here can never change what the pipeline discovers.
    """

    def __init__(self, base_url: str, email: str, api_token: str, timeout: float = 30.0):
        self.base_url = base_url.rstrip("/")
        self.client = httpx.Client(
            auth=(email, api_token),
            timeout=timeout,
            headers={"Accept": "application/json"},
            follow_redirects=True,
        )

    def __enter__(self) -> ConfluenceClient:
        return self

    def __exit__(self, *exc_info) -> None:
        self.client.close()

    def request(self, method: str, path: str, **kwargs) -> httpx.Response:
        """One API call, retrying while Confluence answers 429.

        `path` is either a path relative to the site (as _links.next gives it) or
        an absolute URL.
        """
        url = path if path.startswith("http") else f"{self.base_url}{path}"
        for attempt in range(MAX_RETRIES):
            response = self.client.request(method, url, **kwargs)
            if response.status_code != 429:
                break
            wait = _retry_after(response)
            print(f"  rate limited, waiting {wait:.0f}s", file=sys.stderr)
            time.sleep(wait)
        if response.status_code >= 400:
            raise ConfluenceError(
                f"{method} {url} → {response.status_code}: {response.text[:300]}"
            )
        return response

    def paginate(self, path: str, params: dict | None = None) -> list[dict]:
        """Every `results` item across the cursor-paginated pages of one endpoint.

        The next link comes back site-relative and already carries the cursor, so
        subsequent calls must not re-send `params`.
        """
        items: list[dict] = []
        response = self.request("GET", path, params=params)
        while True:
            body = response.json()
            items.extend(body.get("results", []))
            next_link = (body.get("_links") or {}).get("next")
            if not next_link:
                return items
            response = self.request("GET", next_link)


def _retry_after(response: httpx.Response) -> float:
    try:
        return float(response.headers.get("Retry-After", DEFAULT_RETRY_AFTER))
    except ValueError:
        return DEFAULT_RETRY_AFTER


def collect_pages(
    client: ConfluenceClient, root_page_id: str, include_root: bool
) -> list[Page]:
    """The root page and all its descendants, in the order Confluence returns them.

    /descendants walks the whole subtree in one paginated call, so this makes no
    recursive per-page requests.
    """
    pages: list[Page] = []
    if include_root:
        root = client.request("GET", f"/wiki/api/v2/pages/{root_page_id}").json()
        pages.append(Page(id=str(root["id"]), title=root.get("title", ""), depth=0))

    for item in client.paginate(
        f"/wiki/api/v2/pages/{root_page_id}/descendants", {"limit": PAGE_LIMIT}
    ):
        # /descendants returns other content types too (whiteboards, databases);
        # only pages carry a body the pipeline can parse.
        if item.get("type") not in (None, "page"):
            continue
        pages.append(
            Page(
                id=str(item["id"]),
                title=item.get("title", ""),
                depth=int(item.get("depth", 1)),
            )
        )
    return pages


def page_labels(client: ConfluenceClient, page_id: str) -> set[str]:
    return {
        label["name"]
        for label in client.paginate(
            f"/wiki/api/v2/pages/{page_id}/labels", {"limit": PAGE_LIMIT}
        )
    }


def add_label(client: ConfluenceClient, page_id: str, label: str) -> None:
    # v2 has no add-label endpoint; the v1 content API is the supported route on
    # Cloud. Adding a label the page already has is a no-op there.
    client.request(
        "POST",
        f"/wiki/rest/api/content/{page_id}/label",
        json=[{"prefix": "global", "name": label}],
    )


def remove_label(client: ConfluenceClient, page_id: str, label: str) -> None:
    client.request(
        "DELETE",
        f"/wiki/rest/api/content/{page_id}/label",
        params={"name": label},
    )


def run(
    client: ConfluenceClient,
    root_page_ids: list[str],
    label: str,
    include_root: bool,
    remove: bool,
    apply: bool,
) -> int:
    """Label (or unlabel) every page under each root. Returns how many changed.

    Prints one line per page either way; without `apply` nothing is written.
    """
    verb = "remove" if remove else "add"
    seen: set[str] = set()
    changed = 0
    skipped = 0

    for root_page_id in root_page_ids:
        print(f"\n{root_page_id}: collecting pages…")
        pages = collect_pages(client, root_page_id, include_root)
        print(f"{root_page_id}: {len(pages)} page(s)\n")

        for page in pages:
            # a page can sit under two of the given roots; only touch it once
            if page.id in seen:
                continue
            seen.add(page.id)

            has_label = label in page_labels(client, page.id)
            needs_change = has_label if remove else not has_label
            indent = "  " * page.depth
            if not needs_change:
                skipped += 1
                state = "not labelled" if remove else "already labelled"
                print(f"  {page.id:>12}  {indent}{page.title}  [{state}]")
                continue

            changed += 1
            print(f"  {page.id:>12}  {indent}{page.title}  [{verb}]")
            if apply:
                if remove:
                    remove_label(client, page.id, label)
                else:
                    add_label(client, page.id, label)

    action = "changed" if apply else "would change"
    print(f"\n{len(seen)} page(s): {changed} {action}, {skipped} already as wanted")
    if changed and not apply:
        print("dry run — nothing written. Re-run with --apply.")
    return changed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Add or remove a Confluence label across a page tree, so a "
            "(space, label) source in sources.yaml picks the pages up."
        )
    )
    parser.add_argument(
        "--base-url",
        required=True,
        help="Confluence Cloud site, e.g. https://acme.atlassian.net",
    )
    parser.add_argument(
        "--root-page-id",
        required=True,
        action="append",
        dest="root_page_ids",
        help="page whose subtree gets the label; repeatable",
    )
    parser.add_argument("--label", required=True, help="label name, e.g. RAG")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="actually write; without this the script only prints what it would do",
    )
    parser.add_argument(
        "--skip-root",
        action="store_true",
        help="label only the descendants, not the root page itself",
    )
    parser.add_argument(
        "--remove",
        action="store_true",
        help="remove the label instead of adding it "
        "(takes those pages out of the corpus on the next run)",
    )
    parser.add_argument(
        "--timeout", type=float, default=30.0, help="per-request timeout in seconds"
    )
    args = parser.parse_args(argv)

    email = os.environ.get("RAG_CONFLUENCE_EMAIL")
    api_token = os.environ.get("RAG_CONFLUENCE_API_TOKEN")
    if not email or not api_token:
        parser.error("CONFLUENCE_EMAIL and CONFLUENCE_API_TOKEN must be set")

    try:
        with ConfluenceClient(args.base_url, email, api_token, args.timeout) as client:
            run(
                client,
                args.root_page_ids,
                args.label,
                include_root=not args.skip_root,
                remove=args.remove,
                apply=args.apply,
            )
    except ConfluenceError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
