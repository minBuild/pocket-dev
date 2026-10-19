"""파일 첨부 — 아이디어 문서·기획서·이미지 등을 올려서 토론·명세서·구현에 참고하게 한다.

- 업로드: data/uploads/<id>/<파일>  → 작업에 붙이면 data/runs/<run>/attachments/ 로 옮김
- 글자 문서(md·txt·docx 등)는 내용을 프롬프트에 직접 넣고, PDF·이미지는 AI 가 Read 도구로 열어본다
"""
import re
import shutil
import uuid
import zipfile
from pathlib import Path

from fastapi import UploadFile

from .config import DATA_DIR

UPLOADS = DATA_DIR / "uploads"
TEXT_EXT = {".md", ".txt", ".csv", ".json", ".html", ".htm", ".yaml", ".yml", ".xml"}
ALLOWED_EXT = TEXT_EXT | {".pdf", ".docx", ".png", ".jpg", ".jpeg", ".webp", ".gif"}
MAX_BYTES = 20 * 1024 * 1024
INLINE_BUDGET = 40_000  # 프롬프트에 직접 넣는 글자 수 (첨부 전체 합)


def _safe_name(name: str) -> str:
    name = Path(name or "file").name
    name = re.sub(r"[^\w.\- ()가-힣]", "_", name).strip(" .") or "file"
    return name[:120]


def _docx_text(path: Path) -> str:
    """docx → 글자 (문단마다 줄바꿈)."""
    with zipfile.ZipFile(path) as z:
        xml = z.read("word/document.xml").decode("utf-8", "replace")
    xml = re.sub(r"</w:p>", "\n", xml)
    xml = re.sub(r"<w:tab/>", "\t", xml)
    text = re.sub(r"<[^>]+>", "", xml)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


async def save_upload(file: UploadFile) -> dict:
    name = _safe_name(file.filename)
    ext = Path(name).suffix.lower()
    if ext not in ALLOWED_EXT:
        raise ValueError(f"{name}: 지원하지 않는 형식이에요 ({', '.join(sorted(ALLOWED_EXT))})")
    upload_id = uuid.uuid4().hex[:12]
    folder = UPLOADS / upload_id
    folder.mkdir(parents=True)
    path, size = folder / name, 0
    with path.open("wb") as out:
        while chunk := await file.read(1024 * 1024):
            size += len(chunk)
            if size > MAX_BYTES:
                out.close()
                shutil.rmtree(folder, ignore_errors=True)
                raise ValueError(f"{name}: 파일이 너무 커요 (최대 20MB)")
            out.write(chunk)
    if ext == ".docx":
        try:
            (folder / (name + ".txt")).write_text(_docx_text(path), encoding="utf-8")
        except (zipfile.BadZipFile, KeyError):
            pass
    return {"id": upload_id, "name": name, "size": size}


def attach(run_dir: Path, upload_ids: list[str]) -> list[dict]:
    """업로드한 파일들을 작업 폴더로 옮기고 [{name, path, text_path?}] 를 돌려준다."""
    dest_root = run_dir / "attachments"
    dest_root.mkdir(parents=True, exist_ok=True)
    out = []
    for upload_id in upload_ids:
        if not re.fullmatch(r"[0-9a-f]{12}", upload_id or ""):
            continue
        folder = UPLOADS / upload_id
        if not folder.is_dir():
            continue
        for f in folder.iterdir():
            if f.suffix == ".txt" and f.name.endswith(".docx.txt"):
                continue
            target = dest_root / f.name
            if target.exists():
                target = dest_root / f"{f.stem}-{upload_id[:4]}{f.suffix}"
            shutil.move(str(f), target)
            item = {"name": target.name, "path": str(target)}
            text_src = folder / (f.name + ".txt")
            if text_src.exists():
                text_dst = target.with_name(target.name + ".txt")
                shutil.move(str(text_src), text_dst)
                item["text_path"] = str(text_dst)
            out.append(item)
        shutil.rmtree(folder, ignore_errors=True)
    return out


def prompt_block(items: list[dict]) -> str:
    """프롬프트용 [첨부 자료] — 글자 문서는 내용, PDF·이미지는 경로 (Read 도구로 열기)."""
    if not items:
        return ""
    lines, budget = ["[첨부 자료 — 사용자가 올린 파일, 반드시 참고]"], INLINE_BUDGET
    for it in items:
        path = Path(it.get("text_path") or it["path"])
        if path.suffix.lower() in TEXT_EXT and path.exists() and budget > 0:
            text = path.read_text(encoding="utf-8", errors="replace")
            lines.append(f"\n--- {it['name']} ---\n{text[:budget]}")
            if len(text) > budget:
                lines.append(f"(… 일부 생략 — 전체는 Read 도구로 {it['path']} 를 여세요)")
            budget -= len(text)
        else:
            lines.append(f"\n--- {it['name']} --- (PDF·이미지 등: Read 도구로 열어서 확인)\n경로: {it['path']}")
    return "\n".join(lines)
