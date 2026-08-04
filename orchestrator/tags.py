"""Fleet `cursor.dev` tags — publish after a lib migrates, pin in consumers.

After a shared library lands DONE, we tag its PR head:
    cursor.dev/<shortsha>

Consumers in later waves get those pins injected into their agent prompt and
must write git dependencies into pyproject.toml, e.g.:

    common-utils @ git+https://github.com/ecowan/common-utils.git@cursor.dev/a1b2c3d
"""
from __future__ import annotations

import abc
import hashlib
import re
from dataclasses import dataclass
from typing import Callable, Optional
from urllib.parse import urlparse

import httpx

# Git tag / ref name. Cute, greppable, and legal as a git ref.
TAG_PREFIX = "cursor.dev/"


@dataclass(frozen=True)
class DevTag:
    """One published fleet-dev tag for an upstream repo."""
    name: str           # package / repo name, e.g. common-utils
    url: str            # https://github.com/owner/repo
    tag: str            # cursor.dev/<shortsha>
    sha: str            # full commit sha tagged
    pep440: str         # 0.0.0+cursor.dev.<shortsha> (for version metadata)

    @property
    def git_dep(self) -> str:
        """PEP 508 direct-URL dependency line (without quotes)."""
        base = self.url.rstrip("/")
        if not base.endswith(".git"):
            base = base + ".git"
        return f"{self.name} @ git+{base}@{self.tag}"


def short_sha(sha: str, n: int = 7) -> str:
    return sha.lower()[:n]


def make_dev_tag(name: str, url: str, sha: str) -> DevTag:
    short = short_sha(sha)
    return DevTag(
        name=name,
        url=url.rstrip("/"),
        tag=f"{TAG_PREFIX}{short}",
        sha=sha,
        pep440=f"0.0.0+cursor.dev.{short}",
    )


def parse_github_repo(url: str) -> tuple[str, str]:
    """Return (owner, repo) from a github.com URL."""
    parsed = urlparse(url)
    parts = [p for p in parsed.path.split("/") if p]
    if len(parts) < 2:
        raise ValueError(f"not a github repo url: {url}")
    owner, repo = parts[0], parts[1].removesuffix(".git")
    return owner, repo


def parse_pr_url(pr_url: str) -> tuple[str, str, int]:
    """Return (owner, repo, number) from a pull request URL."""
    parsed = urlparse(pr_url)
    parts = [p for p in parsed.path.split("/") if p]
    # /owner/repo/pull/123
    if len(parts) < 4 or parts[2] not in {"pull", "pulls"}:
        raise ValueError(f"not a github PR url: {pr_url}")
    return parts[0], parts[1], int(parts[3])


def render_pins_prompt(tags: dict[str, DevTag]) -> str:
    """Markdown block appended to the migration playbook for consumers."""
    if not tags:
        return ""
    lines = [
        "",
        "## Fleet upstream pins (required)",
        "",
        "These in-fleet dependencies already migrated and were tagged with a",
        f"`{TAG_PREFIX}<sha>` **cursor.dev** tag on their PR head. You MUST pin",
        "them as git dependencies in `pyproject.toml` (and refresh `uv.lock`).",
        "Do **not** omit them, and do **not** keep sibling path hacks.",
        "",
    ]
    for tag in tags.values():
        lines.append(f"- `{tag.git_dep}`")
        lines.append(f"  - pep440 local version hint: `{tag.pep440}`")
    lines += [
        "",
        "Example `pyproject.toml` fragment:",
        "",
        "```toml",
        "dependencies = [",
    ]
    for tag in tags.values():
        lines.append(f'  "{tag.git_dep}",')
    lines += [
        "  # …plus third-party deps",
        "]",
        "```",
        "",
        "List every pin under an **Upstream pins** heading in the PR description,",
        "and mention each `cursor.dev/…` tag name so verification can see it.",
        "",
    ]
    return "\n".join(lines)


def extract_cursor_dev_tags(text: str) -> set[str]:
    return set(re.findall(r"cursor\.dev/[A-Za-z0-9._+-]+", text or ""))


class TagPublisher(abc.ABC):
    @abc.abstractmethod
    async def publish(self, *, name: str, url: str, pr_url: str) -> DevTag:
        """Tag the PR head commit; return the DevTag."""


class MockTagPublisher(TagPublisher):
    """Deterministic tags for dry-run — no GitHub calls."""

    def __init__(self) -> None:
        self.published: list[DevTag] = []

    async def publish(self, *, name: str, url: str, pr_url: str) -> DevTag:
        # Stable fake sha from name+pr so demos are repeatable.
        digest = hashlib.sha1(f"{name}:{pr_url}".encode()).hexdigest()
        tag = make_dev_tag(name, url, digest)
        self.published.append(tag)
        return tag


class GitHubTagPublisher(TagPublisher):
    """Create lightweight `cursor.dev/<sha>` tags on the PR head via GitHub API."""

    def __init__(self, token: str, *, on_step: Optional[Callable[[str], None]] = None):
        if not token:
            raise ValueError("GitHub token required to publish cursor.dev tags")
        self._on_step = on_step or (lambda _msg: None)
        self._http = httpx.AsyncClient(
            base_url="https://api.github.com",
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            timeout=httpx.Timeout(30.0),
        )

    async def publish(self, *, name: str, url: str, pr_url: str) -> DevTag:
        owner, repo, number = parse_pr_url(pr_url)
        self._on_step(f"resolving PR head for {owner}/{repo}#{number}")
        pr = await self._http.get(f"/repos/{owner}/{repo}/pulls/{number}")
        pr.raise_for_status()
        sha = pr.json()["head"]["sha"]
        tag = make_dev_tag(name, url, sha)
        ref = f"refs/tags/{tag.tag}"
        self._on_step(f"creating {ref} → {short_sha(sha)}")
        resp = await self._http.post(
            f"/repos/{owner}/{repo}/git/refs",
            json={"ref": ref, "sha": sha},
        )
        # 422 usually means the tag already exists — treat as success if it
        # already points at this sha (or just accept existence for demos).
        if resp.status_code == 422:
            existing = await self._http.get(
                f"/repos/{owner}/{repo}/git/ref/tags/{tag.tag}"
            )
            if existing.status_code == 200:
                self._on_step(f"tag {tag.tag} already exists — reusing")
                return tag
        resp.raise_for_status()
        return tag

    async def aclose(self) -> None:
        await self._http.aclose()
