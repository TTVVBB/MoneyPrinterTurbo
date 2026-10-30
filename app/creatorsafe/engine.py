"""Adapter to MoneyPrinterTurbo's existing task pipeline; no renderer duplication."""
from pathlib import Path

from app.creatorsafe.workflow import digest


def render(job, script, plan, folder):
    # Lazy imports let policy tests and review/export run without media dependencies.
    from app.models.schema import MaterialInfo, VideoParams
    from app.services import material_upload, task, upload_post

    if upload_post.upload_post_service.auto_upload:
        raise ValueError("Automatic publishing must be disabled")
    materials = []
    narration = None
    for asset in plan["assets"]:
        source = folder / asset["path"]
        if digest(source) != asset["sha256"]:
            raise ValueError("Snapshot checksum mismatch")
        if asset["kind"] == "footage":
            if source.suffix.lower() != ".mp4":
                raise ValueError("MVP footage must be MP4")
            with source.open("rb") as stream:
                stored = material_upload.save_material_upload(source.name, stream)
            materials.append(MaterialInfo(provider="local", url=stored))
        else:
            if source.suffix.lower() not in {".mp3", ".wav"}:
                raise ValueError("Narration must be MP3 or WAV")
            narration = str(source.resolve())
    params = VideoParams(
        video_subject=plan["title"], video_script=script, video_language="en",
        video_source="local", video_materials=materials, video_aspect="9:16",
        video_count=1, video_concat_mode="sequential", video_clip_duration=5,
        custom_audio_file=narration, bgm_type="", bgm_volume=0,
        subtitle_enabled=True, font_name="MicrosoftYaHeiBold.ttc",
    )
    result = task.start(f"creatorsafe-{job}", params, stop_at="video", allow_server_file_input=True)
    if not result or not result.get("videos") or result.get("warnings"):
        raise ValueError("Engine failed or reported warnings; inspect the task and create a new job")
    duration = result.get("audio_duration", 0)
    if not 0 < duration <= 60:
        raise ValueError("MVP narration must be 1–60 seconds")
    return Path(result["videos"][0])
