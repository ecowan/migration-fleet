"""Build the in-fleet dependency matrix by reading each repo's contents.

Until now `depends_on` was hand-authored in repos.yaml (attributed to a
"dependency-matrix skill"). This module is that step: for every fleet repo it

  1. Resolves a local checkout under `targets/<name>/` (demo seed layout)
  2. Discovers the distribution name (`setup.py` / `pyproject.toml`) and the
     top-level import packages under `src/`
  3. Collects declared requirements (`requirements*.txt`, install_requires)
  4. Scans Python imports under `src/` and `tests/`
  5. Keeps only edges whose target is another fleet member

The scheduler then topo-sorts the resulting graph into migration waves.
"""
from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Optional

from .models import RepoTarget

# PEP 508 requirement: name, optional extras, then version/URL/marker junk.
_REQ_NAME = re.compile(
    r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)(?:\[[^\]]*\])?\s*(?:[<=>!~@]|$)"
)
_SETUP_NAME = re.compile(
    r"""name\s*=\s*["']([A-Za-z0-9][A-Za-z0-9._-]*)["']"""
)
_SETUP_INSTALL = re.compile(
    r"install_requires\s*=\s*\[(.*?)\]", re.DOTALL
)
_STR_LIT = re.compile(r"""["']([^"']+)["']""")
_PYPROJECT_NAME = re.compile(
    r"""(?m)^name\s*=\s*["']([A-Za-z0-9][A-Za-z0-9._-]*)["']"""
)


def normalize_name(name: str) -> str:
    """PEP 503-ish: lowercase, `_`/`.` → `-`."""
    return re.sub(r"[-_.]+", "-", name).lower()


@dataclass
class RepoIdentity:
    """What we learned by reading one checkout."""
    repo: str                          # fleet name (repos.yaml)
    path: Path
    dist_name: str                     # setuptools/pyproject name
    import_packages: set[str] = field(default_factory=set)  # top-level pkgs
    declared: set[str] = field(default_factory=set)         # normalized req names
    imported: set[str] = field(default_factory=set)         # top-level modules


@dataclass
class DepMatrix:
    """depends_on lists keyed by fleet repo name, plus scan diagnostics."""
    depends_on: dict[str, list[str]]
    identities: dict[str, RepoIdentity]
    # Audit trail of how we learned about each dependency edge.
    edges: list[tuple[str, str, str]]  # (consumer, upstream, reason)

    def apply(self, targets: list[RepoTarget]) -> list[RepoTarget]:
        """Return new RepoTargets with depends_on replaced by the matrix."""
        out = []
        for t in targets:
            deps = list(self.depends_on.get(t.name, []))
            out.append(replace(t, depends_on=deps))  # preserves root, publish_tag, …
        return out


def format_matrix(matrix: DepMatrix) -> str:
    """Human-readable adjacency list for the console."""
    lines = ["Dependency matrix (from repo contents):"]
    for name in sorted(matrix.depends_on):
        deps = matrix.depends_on[name]
        ident = matrix.identities.get(name)
        pkg = f"  [{ident.dist_name}]" if ident and ident.dist_name != name else ""
        if deps:
            lines.append(f"  {name}{pkg} → {', '.join(deps)}")
        else:
            lines.append(f"  {name}{pkg} → (none — leaf / shared lib)")
    if matrix.edges:
        lines.append("  evidence:")
        for consumer, upstream, reason in matrix.edges:
            lines.append(f"    {consumer} depends on {upstream}  ({reason})")
    return "\n".join(lines)


def build_dep_matrix(
    targets: list[RepoTarget],
    *,
    roots: Path,
) -> DepMatrix:
    """Scan checkouts under `roots/<repo.name>/` and build in-fleet depends_on."""
    identities: dict[str, RepoIdentity] = {}
    missing: list[str] = []
    for t in targets:
        path = roots / t.name
        if not path.is_dir():
            missing.append(t.name)
            continue
        identities[t.name] = _inspect_repo(t.name, path)

    if missing:
        raise FileNotFoundError(
            "dep matrix needs a local checkout for every fleet repo under "
            f"{roots}/; missing: {', '.join(missing)}. "
            "Seed targets/ from the demo repos (or clone each url there) before running."
        )

    # Map distribution name → fleet repo, and import package → fleet repo.
    by_dist: dict[str, str] = {}
    by_import: dict[str, str] = {}
    for name, ident in identities.items():
        by_dist[normalize_name(ident.dist_name)] = name
        by_dist[normalize_name(name)] = name
        for pkg in ident.import_packages:
            by_import[pkg] = name

    depends: dict[str, set[str]] = {t.name: set() for t in targets}
    edges: list[tuple[str, str, str]] = []

    for name, ident in identities.items():
        # Declared requirements → fleet members.
        for req in ident.declared:
            upstream = by_dist.get(req)
            if upstream and upstream != name:
                depends[name].add(upstream)
                edges.append((name, upstream, f"declared requirement '{req}'"))
        # Imports of another fleet member's top-level package.
        for mod in ident.imported:
            upstream = by_import.get(mod)
            if upstream and upstream != name:
                depends[name].add(upstream)
                edges.append((name, upstream, f"imports '{mod}'"))

    # Stable order for depends_on lists.
    order = {t.name: i for i, t in enumerate(targets)}
    depends_on = {
        name: sorted(deps, key=lambda d: order.get(d, 999))
        for name, deps in depends.items()
    }
    # Deduplicate evidence lines while preserving order.
    seen: set[tuple[str, str, str]] = set()
    uniq_edges = []
    for e in edges:
        if e not in seen:
            seen.add(e)
            uniq_edges.append(e)

    return DepMatrix(
        depends_on=depends_on,
        identities=identities,
        edges=uniq_edges,
    )


def _inspect_repo(repo: str, path: Path) -> RepoIdentity:
    dist = _read_dist_name(path) or repo
    packages = _top_level_packages(path)
    declared = _read_declared_deps(path)
    imported = _read_imports(path)
    return RepoIdentity(
        repo=repo,
        path=path,
        dist_name=dist,
        import_packages=packages,
        declared=declared,
        imported=imported,
    )


def _read_dist_name(path: Path) -> Optional[str]:
    pyproject = path / "pyproject.toml"
    if pyproject.is_file():
        m = _PYPROJECT_NAME.search(pyproject.read_text(encoding="utf-8", errors="replace"))
        if m:
            return m.group(1)
    setup = path / "setup.py"
    if setup.is_file():
        m = _SETUP_NAME.search(setup.read_text(encoding="utf-8", errors="replace"))
        if m:
            return m.group(1)
    return None


def _top_level_packages(path: Path) -> set[str]:
    """Directories under src/ (or repo root) that look like importable packages."""
    found: set[str] = set()
    for root in (path / "src", path):
        if not root.is_dir():
            continue
        for child in root.iterdir():
            if child.is_dir() and (child / "__init__.py").is_file():
                if child.name.startswith(".") or child.name in {"tests", "test", "docs"}:
                    continue
                found.add(child.name)
        if root.name == "src" or found:
            break
    return found


def _read_declared_deps(path: Path) -> set[str]:
    names: set[str] = set()
    for req_file in sorted(path.glob("requirements*.txt")):
        names |= _parse_requirements_text(
            req_file.read_text(encoding="utf-8", errors="replace")
        )
    setup = path / "setup.py"
    if setup.is_file():
        text = setup.read_text(encoding="utf-8", errors="replace")
        block = _SETUP_INSTALL.search(text)
        if block:
            for lit in _STR_LIT.findall(block.group(1)):
                names |= _parse_requirements_text(lit)
    pyproject = path / "pyproject.toml"
    if pyproject.is_file():
        names |= _parse_pyproject_deps(
            pyproject.read_text(encoding="utf-8", errors="replace")
        )
    return {normalize_name(n) for n in names}


def _parse_requirements_text(text: str) -> set[str]:
    names: set[str] = set()
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or line.startswith("-"):
            continue
        m = _REQ_NAME.match(line)
        if m:
            names.add(m.group(1))
    return names


def _parse_pyproject_deps(text: str) -> set[str]:
    """Minimal TOML-free scrape of dependency string lists."""
    names: set[str] = set()
    # Catch project.dependencies = [ ... ] and similar list blocks.
    for block in re.finditer(
        r"(?is)(?:dependencies|requires)\s*=\s*\[(.*?)\]", text
    ):
        for lit in _STR_LIT.findall(block.group(1)):
            names |= _parse_requirements_text(lit)
    return names


def _read_imports(path: Path) -> set[str]:
    """Top-level module names imported from src/ and tests/."""
    mods: set[str] = set()
    roots: list[Path] = []
    for candidate in (path / "src", path / "tests", path):
        if candidate.is_dir():
            roots.append(candidate)
    seen_files: set[Path] = set()
    for root in roots:
        for py in root.rglob("*.py"):
            if py in seen_files:
                continue
            seen_files.add(py)
            # Skip the repo's own setup/conftest noise for "from setuptools".
            if py.name in {"setup.py"}:
                continue
            try:
                tree = ast.parse(
                    py.read_text(encoding="utf-8", errors="replace"),
                    filename=str(py),
                )
            except SyntaxError:
                continue
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        mods.add(alias.name.split(".")[0])
                elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                    mods.add(node.module.split(".")[0])
    # Drop ubiquitous stdlib / tooling noise — only fleet packages will match
    # via by_import anyway, but this keeps evidence quieter.
    return mods


def resolve_targets_root(explicit: Optional[Path], here: Path) -> Path:
    """Default to ./targets next to run.py."""
    return explicit if explicit is not None else here / "targets"
