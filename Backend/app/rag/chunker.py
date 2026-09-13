"""
Chunking utilities for the application-source RAG collection.

We use lightweight, dependency-tolerant chunking:
 * Python files are split by top-level `def`/`class` boundaries using the
   built-in `ast` module (a practical, dependency-free stand-in for a full
   tree-sitter grammar).
 * JS/JSX/TS/TSX files are split with a regex-based heuristic that looks for
   top-level function/class/component boundaries, since tree-sitter grammars
   are an optional heavy dependency we should not hard-require.
 * Anything else (or any file that fails structured chunking) falls back to
   fixed-size line-window chunking so nothing is ever silently skipped.

Every chunk carries metadata (source file, language, chunk type, symbol,
line range) sufficient to reconstruct where it came from and to be shown to
the user, not just concatenated raw text.
"""
from __future__ import annotations

import ast
import re
from dataclasses import dataclass

SUPPORTED_EXTENSIONS = {".py", ".js", ".jsx", ".ts", ".tsx"}

_LANGUAGE_BY_EXTENSION = {
    ".py": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
}

_IGNORED_DIR_NAMES = {
    ".git",
    "node_modules",
    "venv",
    ".venv",
    "env",
    "__pycache__",
    "dist",
    "build",
    ".next",
    "target",
    ".mypy_cache",
    ".pytest_cache",
    "coverage",
    ".terraform",
}

_IGNORED_FILE_SUFFIXES = {
    ".pyc",
    ".pyo",
    ".so",
    ".dll",
    ".exe",
    ".dylib",
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".ico",
    ".webp",
    ".pdf",
    ".zip",
    ".tar",
    ".gz",
    ".7z",
    ".lock",
    ".woff",
    ".woff2",
    ".ttf",
    ".eot",
    ".mp4",
    ".mov",
    ".sqlite",
    ".sqlite3",
    ".db",
}

# Filenames/patterns that are (or commonly hold) secrets/credentials and
# must never be ingested into RAG, regardless of extension - this is a
# second line of defense beyond SUPPORTED_EXTENSIONS (which already
# excludes most non-code formats like .json/.pem/.key).
_IGNORED_FILENAME_PATTERNS = (
    re.compile(r"^\.env(\..+)?$", re.IGNORECASE),
    re.compile(r".*credentials.*", re.IGNORECASE),
    re.compile(r".*service[-_]?account.*", re.IGNORECASE),
    re.compile(r".*secrets?[-_.].*", re.IGNORECASE),
    re.compile(r".*\.pem$", re.IGNORECASE),
    re.compile(r".*\.key$", re.IGNORECASE),
    re.compile(r"^id_rsa.*$", re.IGNORECASE),
)

_SECRET_HINTS = re.compile(r"(?i)(api[_-]?key|secret|password|token|private[_-]?key)\s*[=:]")

_MAX_LINES_PER_FALLBACK_CHUNK = 60


@dataclass
class CodeChunk:
    content: str
    source_file: str
    start_line: int
    end_line: int
    symbol: str | None = None
    language: str = ""
    chunk_type: str = "line_window"  # "function" | "class" | "module" | "line_window"


def is_ignored_path(path_parts: tuple[str, ...], filename: str) -> bool:
    if any(part in _IGNORED_DIR_NAMES for part in path_parts):
        return True
    if any(filename.endswith(suffix) for suffix in _IGNORED_FILE_SUFFIXES):
        return True
    if any(pattern.match(filename) for pattern in _IGNORED_FILENAME_PATTERNS):
        return True
    return False


def redact_secrets(text: str) -> str:
    """Best-effort redaction so obvious secret-looking lines never reach the LLM."""
    redacted_lines = []
    for line in text.splitlines():
        if _SECRET_HINTS.search(line):
            redacted_lines.append("<REDACTED: possible secret omitted>")
        else:
            redacted_lines.append(line)
    return "\n".join(redacted_lines)


def _language_for(relative_path: str) -> str:
    for ext, lang in _LANGUAGE_BY_EXTENSION.items():
        if relative_path.endswith(ext):
            return lang
    return "unknown"


def chunk_source_file(relative_path: str, content: str) -> list[CodeChunk]:
    content = redact_secrets(content)
    language = _language_for(relative_path)

    if relative_path.endswith(".py"):
        chunks = _chunk_python(relative_path, content, language)
    elif relative_path.endswith((".js", ".jsx", ".ts", ".tsx")):
        chunks = _chunk_js_like(relative_path, content, language)
    else:
        chunks = []

    if not chunks:
        chunks = _chunk_by_lines(relative_path, content, language)
    return chunks


def _chunk_python(relative_path: str, content: str, language: str) -> list[CodeChunk]:
    try:
        tree = ast.parse(content)
    except SyntaxError:
        return []

    lines = content.splitlines()
    chunks: list[CodeChunk] = []
    top_level_nodes = [
        node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
    ]
    other_nodes = [n for n in tree.body if n not in top_level_nodes]

    if not top_level_nodes and not other_nodes:
        return []

    for node in top_level_nodes:
        start = node.lineno - 1
        end = getattr(node, "end_lineno", node.lineno)
        snippet = "\n".join(lines[start:end])
        chunk_type = "class" if isinstance(node, ast.ClassDef) else "function"
        chunks.append(
            CodeChunk(
                content=snippet,
                source_file=relative_path,
                start_line=node.lineno,
                end_line=end,
                symbol=node.name,
                language=language,
                chunk_type=chunk_type,
            )
        )

    # Module-level statements (imports, config, top-level assignments like
    # `app = FastAPI()`) carry framework/config signal even though they are
    # not inside a function/class - keep them as one additional chunk.
    if other_nodes:
        module_lines = []
        for node in other_nodes:
            start = node.lineno - 1
            end = getattr(node, "end_lineno", node.lineno)
            module_lines.append("\n".join(lines[start:end]))
        module_snippet = "\n".join(module_lines).strip()
        if module_snippet:
            chunks.append(
                CodeChunk(
                    content=module_snippet,
                    source_file=relative_path,
                    start_line=other_nodes[0].lineno,
                    end_line=getattr(other_nodes[-1], "end_lineno", other_nodes[-1].lineno),
                    symbol="<module>",
                    language=language,
                    chunk_type="module",
                )
            )
    return chunks


_JS_BOUNDARY_RE = re.compile(
    r"^(export\s+)?(default\s+)?(async\s+)?(function\s+\w+|class\s+\w+|const\s+\w+\s*=\s*\(?.*=>)",
    re.MULTILINE,
)


def _chunk_js_like(relative_path: str, content: str, language: str) -> list[CodeChunk]:
    lines = content.splitlines()
    matches = list(_JS_BOUNDARY_RE.finditer(content))
    if not matches:
        return []

    chunks: list[CodeChunk] = []
    boundaries = [content.count("\n", 0, m.start()) for m in matches]
    boundaries.append(len(lines))

    for i, start_line in enumerate(boundaries[:-1]):
        end_line = boundaries[i + 1]
        snippet = "\n".join(lines[start_line:end_line]).strip()
        if not snippet:
            continue
        symbol_match = re.search(r"(function|class|const)\s+(\w+)", matches[i].group(0))
        symbol = symbol_match.group(2) if symbol_match else None
        chunk_type = "class" if "class" in matches[i].group(0) else "function"
        chunks.append(
            CodeChunk(
                content=snippet,
                source_file=relative_path,
                start_line=start_line + 1,
                end_line=end_line,
                symbol=symbol,
                language=language,
                chunk_type=chunk_type,
            )
        )
    return chunks


def _chunk_by_lines(relative_path: str, content: str, language: str = "") -> list[CodeChunk]:
    lines = content.splitlines()
    if not lines:
        return []
    chunks = []
    for start in range(0, len(lines), _MAX_LINES_PER_FALLBACK_CHUNK):
        end = min(start + _MAX_LINES_PER_FALLBACK_CHUNK, len(lines))
        snippet = "\n".join(lines[start:end])
        if snippet.strip():
            chunks.append(
                CodeChunk(
                    content=snippet,
                    source_file=relative_path,
                    start_line=start + 1,
                    end_line=end,
                    language=language or _language_for(relative_path),
                    chunk_type="line_window",
                )
            )
    return chunks
