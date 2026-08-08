"""Execute and archive the frozen GitHub defect-retrieval manifest."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any, Callable, Mapping, Optional, Sequence
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


API_ROOT = "https://api.github.com"


class GitHubRetrievalError(RuntimeError):
    """A GitHub response could not be archived and interpreted safely."""


def _headers() -> dict[str, str]:
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "NanoHarness-real-defect-retrieval",
    }
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def parse_next_link(value: Optional[str]) -> Optional[str]:
    """Return the URL carrying the RFC 5988 `next` relation."""

    if not value:
        return None
    for part in value.split(","):
        fields = [field.strip() for field in part.split(";")]
        if len(fields) >= 2 and 'rel="next"' in fields[1:]:
            return fields[0].strip()[1:-1]
    return None


def _request_bytes(
    url: str,
    *,
    transport: Callable[[Request], Any],
) -> tuple[bytes, Mapping[str, str]]:
    try:
        with transport(Request(url, headers=_headers())) as response:
            return response.read(), response.headers
    except HTTPError as exc:
        remaining = exc.headers.get("X-RateLimit-Remaining", "unknown")
        reset = exc.headers.get("X-RateLimit-Reset", "unknown")
        raise GitHubRetrievalError(
            f"GitHub HTTP {exc.code}; rate remaining={remaining}; reset={reset}"
        ) from exc
    except URLError as exc:
        raise GitHubRetrievalError(f"GitHub request failed: {exc.reason}") from exc


def _write_exact(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)


def _fetch_json(
    url: str,
    output: Path,
    *,
    transport: Callable[[Request], Any],
) -> Any:
    payload, _ = _request_bytes(url, transport=transport)
    _write_exact(output, payload)
    try:
        return json.loads(payload)
    except json.JSONDecodeError as exc:
        raise GitHubRetrievalError(f"GitHub returned invalid JSON for {url}") from exc


def fetch_pages(
    url: str,
    output_dir: Path,
    *,
    transport: Callable[[Request], Any] = urlopen,
) -> list[dict[str, Any]]:
    """Follow a GitHub search query and retain every exact response body."""

    output_dir.mkdir(parents=True, exist_ok=True)
    items: list[dict[str, Any]] = []
    page = 1
    while url:
        payload, headers = _request_bytes(url, transport=transport)
        _write_exact(output_dir / f"page-{page:04d}.json", payload)
        try:
            decoded = json.loads(payload)
        except json.JSONDecodeError as exc:
            raise GitHubRetrievalError(
                f"GitHub returned invalid JSON for page {page}"
            ) from exc
        if not isinstance(decoded, dict) or not isinstance(
            decoded.get("items"), list
        ):
            raise GitHubRetrievalError(
                "GitHub search response must be an object with an items list"
            )
        items.extend(decoded["items"])
        url = parse_next_link(headers.get("Link"))
        page += 1
    return items


def run_retrieval(
    manifest: Mapping[str, Any],
    output_dir: Path,
    *,
    transport: Callable[[Request], Any] = urlopen,
) -> dict[str, Any]:
    """Execute all frozen queries and return a credential-free audit report."""

    output_dir.mkdir(parents=True, exist_ok=True)
    report: dict[str, Any] = {
        "manifest_id": manifest["manifest_id"],
        "complete": True,
        "repositories": [],
        "errors": [],
    }
    window = manifest["window"]
    for repository in manifest["repositories"]:
        repository_dir = output_dir / repository.replace("/", "__")
        try:
            metadata = _fetch_json(
                f"{API_ROOT}/repos/{repository}",
                repository_dir / "repository.json",
                transport=transport,
            )
            default_branch = metadata["default_branch"]
            commit_query = urlencode({
                "sha": default_branch,
                "until": f"{window['end']}T23:59:59Z",
                "per_page": 1,
            })
            commits = _fetch_json(
                f"{API_ROOT}/repos/{repository}/commits?{commit_query}",
                repository_dir / "pin.json",
                transport=transport,
            )
            if not isinstance(commits, list) or not commits:
                raise GitHubRetrievalError(
                    f"No default-branch commit found for {repository}"
                )
            repository_report = {
                "repository": repository,
                "repository_id": metadata["id"],
                "default_branch": default_branch,
                "pinned_commit": commits[0]["sha"],
                "queries": [],
            }
            report["repositories"].append(repository_report)
            for query_spec in manifest["queries"]:
                query = query_spec["query"].format(
                    repository=repository,
                    start=window["start"],
                    end=window["end"],
                )
                url = f"{API_ROOT}/{query_spec['endpoint']}?{urlencode({'q': query, 'per_page': 100})}"
                try:
                    items = fetch_pages(
                        url,
                        repository_dir / "queries" / query_spec["id"],
                        transport=transport,
                    )
                    repository_report["queries"].append({
                        "query_id": query_spec["id"],
                        "query": query,
                        "item_count": len(items),
                    })
                except GitHubRetrievalError as exc:
                    report["complete"] = False
                    report["errors"].append({
                        "repository": repository,
                        "query_id": query_spec["id"],
                        "error": str(exc),
                    })
        except (GitHubRetrievalError, KeyError, TypeError) as exc:
            report["complete"] = False
            report["errors"].append({
                "repository": repository,
                "query_id": None,
                "error": str(exc),
            })
    return report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    report = run_retrieval(manifest, args.output)
    report_path = args.output.parent / "retrieval_report.json"
    report_path.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["complete"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
