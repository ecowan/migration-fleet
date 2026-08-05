"""Fleet dev versions — publish after a lib migrates, pin in consumers.

After a shared library lands DONE, we tag its PR head with a PEP 440 dev
version (default ``0.0.1.dev0``). Consumers in later waves pin that version:

    common-utils==0.0.1.dev0

and resolve it via ``[tool.uv.sources]`` pointing at the git tag (no public
index required). Colloquially: ``common-utils@0.0.1.dev0``.
"""
from __future__ import annotations

import abc
import hashlib
import re
from dataclasses import dataclass
from typing import Callable, Optional
from urllib.parse import urlparse

import httpx

# Fixed fleet-internal pre-release. Same version string is fine across packages
# (each package name is distinct). Re-running a lib moves the git tag.
FLEET_DEV_VERSION = "0.0.1.dev0"


@dataclass(frozen=True)
class DevTag:
    """One published fleet-dev version for an upstream repo."""
    name: str           # package / repo name, e.g. common-utils
    url: str            # https://github.com/owner/repo
    version: str        # PEP 440, e.g. 0.0.1.dev0
    sha: str            # full commit sha tagged
    tag: str            # git tag name (== version)

    @property
    def pep440(self) -> str:
        return self.version

    @property
    def pin(self) -> str:
        """Colloquial package@version form."""
        return f"{self.name}@{self.version}"

    @property
    def pep508(self) -> str:
        """PEP 508 version pin for dependencies = [...]."""
        return f"{self.name}=={self.version}"

    @property
    def git_url(self) -> str:
        base = self.url.rstrip("/")
        if not base.endswith(".git"):
            base = base + ".git"
        return base

    @property
    def uv_source(self) -> str:
        """One [tool.uv.sources] entry so uv can resolve the version from git."""
        return (
            f'{self.name} = {{ git = "{self.git_url}", tag = "{self.tag}" }}'
        )


def short_sha(sha: str, n: int = 7) -> str:
    return sha.lower()[:n]


def make_dev_tag(
    name: str,
    url: str,
    sha: str,
    *,
    version: str = FLEET_DEV_VERSION,
) -> DevTag:
    return DevTag(
        name=name,
        url=url.rstrip("/"),
        version=version,
        sha=sha,
        tag=version,
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
        f"PEP 440 pre-release (`{FLEET_DEV_VERSION}`) on their PR head.",
        "Pin them by **version** (not git URLs) in `pyproject.toml`, and tell",
        "`uv` where to find each version via `[tool.uv.sources]`.",
        "Do **not** omit them, and do **not** keep sibling path hacks.",
        "",
    ]
    for tag in tags.values():
        lines.append(f"- `{tag.pin}`  (PEP 508: `{tag.pep508}`)")
    lines += [
        "",
        "Example `pyproject.toml` fragment:",
        "",
        "```toml",
        "dependencies = [",
    ]
    for tag in tags.values():
        lines.append(f'  "{tag.pep508}",')
    lines += [
        "  # …plus third-party deps",
        "]",
        "",
        "[tool.uv.sources]",
    ]
    for tag in tags.values():
        lines.append(tag.uv_source)
    lines += [
        "```",
        "",
        "List every pin under an **Upstream pins** heading in the PR description,",
        f"using the `name@version` form (e.g. `common-utils@{FLEET_DEV_VERSION}`).",
        "",
    ]
    return "\n".join(lines)


def extract_fleet_pins(text: str) -> set[str]:
    """Find ``name@0.0.1.dev0`` / ``name==0.0.1.dev0`` mentions in agent output."""
    found: set[str] = set()
    for m in re.finditer(
        r"\b([A-Za-z0-9][A-Za-z0-9._-]*)\s*(?:@|==)\s*([0-9]+(?:\.[0-9]+)*(?:\.dev[0-9]+)?)\b",
        text or "",
    ):
        found.add(f"{m.group(1)}@{m.group(2)}")
    return found


class TagPublisher(abc.ABC):
    @abc.abstractmethod
    async def publish(self, *, name: str, url: str, pr_url: str) -> DevTag:
        """Tag the PR head commit; return the DevTag."""


class MockTagPublisher(TagPublisher):
    """Deterministic tags for dry-run — no GitHub calls."""

    def __init__(self, *, on_step: Optional[Callable[[str], None]] = None) -> None:
        self.published: list[DevTag] = []
        self._on_step = on_step or (lambda _msg: None)

    async def publish(self, *, name: str, url: str, pr_url: str) -> DevTag:
        digest = hashlib.sha1(f"{name}:{pr_url}".encode()).hexdigest()
        tag = make_dev_tag(name, url, digest)
        self._on_step(
            f"[mock] create refs/tags/{tag.tag} → {short_sha(digest)} "
            f"(version {tag.version}, from {pr_url})"
        )
        self.published.append(tag)
        return tag


class GitHubTagPublisher(TagPublisher):
    """Create/move a lightweight version tag on the PR head via GitHub API."""

    def __init__(self, token: str, *, on_step: Optional[Callable[[str], None]] = None):
        if not token:
            raise ValueError("GitHub token required to publish fleet version tags")
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
        self._on_step(f"creating {ref} → {short_sha(sha)} (pin {tag.pin})")
        resp = await self._http.post(
            f"/repos/{owner}/{repo}/git/refs",
            json={"ref": ref, "sha": sha},
        )
        if resp.status_code == 422:
            # Tag exists — move it to this PR head so the version tracks the
            # latest successful fleet migration of this package.
            existing = await self._http.get(
                f"/repos/{owner}/{repo}/git/ref/tags/{tag.tag}"
            )
            if existing.status_code == 200:
                old = existing.json().get("object", {}).get("sha", "")
                if old == sha:
                    self._on_step(f"tag {tag.tag} already at {short_sha(sha)} — reusing")
                    return tag
                self._on_step(
                    f"tag {tag.tag} exists at {short_sha(old)} — moving to {short_sha(sha)}"
                )
                moved = await self._http.patch(
                    f"/repos/{owner}/{repo}/git/refs/tags/{tag.tag}",
                    json={"sha": sha, "force": True},
                )
                moved.raise_for_status()
                return tag
        resp.raise_for_status()
        return tag

    async def aclose(self) -> None:
        await self._http.aclose()
