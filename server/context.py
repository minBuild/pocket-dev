"""프로젝트 코드베이스 → 프롬프트용 텍스트 (트리 + 핵심 파일 내용)."""
from pathlib import Path

EXCLUDE_DIRS = {".git", "node_modules", "build", "dist", ".dart_tool", "Pods", ".venv", "venv",
                "__pycache__", ".next", ".gradle", ".idea", ".vscode", "DerivedData", "coverage",
                ".pocketdev", "target", ".expo"}
PRIORITY_FILES = {"README.md", "CLAUDE.md", "package.json", "pubspec.yaml", "pyproject.toml",
                  "requirements.txt", "Podfile", "Info.plist", "build.gradle", "app.json",
                  "tsconfig.json", "go.mod", "Cargo.toml"}
SOURCE_EXT = {".py", ".ts", ".tsx", ".js", ".jsx", ".dart", ".swift", ".kt", ".java", ".go",
              ".rs", ".vue", ".svelte", ".css", ".scss", ".html", ".md", ".yaml", ".yml",
              ".json", ".gradle", ".plist", ".sql", ".sh"}
MAX_FILE_CHARS = 12_000


def _walk(root: Path):
    for p in sorted(root.iterdir(), key=lambda x: (x.is_file(), x.name.lower())):
        if p.name in EXCLUDE_DIRS or (p.name.startswith(".") and p.is_dir()):
            continue
        yield p
        if p.is_dir():
            yield from _walk(p)


def build_context(project: Path, budget: int) -> str:
    """파일 목록(예산의 절반까지) + 핵심 파일 내용. 나머지는 AI 가 Read/Grep/Glob 으로 직접 연다."""
    files = [p for p in _walk(project) if p.is_file()]
    lines, used = [], 0
    for p in files:
        rel = p.relative_to(project).as_posix()
        if used + len(rel) > budget // 2:
            lines.append(f"... (외 {len(files) - len(lines)}개 파일 — Glob 으로 확인)")
            break
        lines.append(rel)
        used += len(rel) + 1
    tree = "\n".join(lines)

    def priority(p: Path):
        rel = p.relative_to(project)
        return (p.name not in PRIORITY_FILES, len(rel.parts), str(rel))

    chunks, used = [], len(tree)
    for p in sorted((f for f in files if f.suffix.lower() in SOURCE_EXT or f.name in PRIORITY_FILES),
                    key=priority):
        if p.name.endswith((".lock", "-lock.json")) or p.stat().st_size > MAX_FILE_CHARS * 2:
            continue
        try:
            text = p.read_text(encoding="utf-8")[:MAX_FILE_CHARS]
        except (UnicodeDecodeError, OSError):
            continue
        block = f"\n===== {p.relative_to(project).as_posix()} =====\n{text}\n"
        if used + len(block) > budget:
            continue
        chunks.append(block)
        used += len(block)

    return f"[파일 목록]\n{tree}\n\n[주요 파일 내용]\n{''.join(chunks)}"
