import argparse
import asyncio
import json
from dataclasses import asdict
from pathlib import Path
from downloader import download_video
from analyzer import (
    VideoProposal,
    analyze_and_create_proposal,
    refine_proposal,
    regenerate_storyboard_panel,
)
from generator import generate_final_video
from config import PROPOSALS_DIR


def _load_proposal_payload(job_id: str) -> tuple[Path, dict, VideoProposal]:
    proposal_file = PROPOSALS_DIR / f"{job_id}.json"
    if not proposal_file.exists():
        raise FileNotFoundError(f"No existe la propuesta para job_id={job_id}")
    payload = json.loads(proposal_file.read_text(encoding="utf-8"))
    p_data = payload["proposal"]
    # Compatibilidad con propuestas guardadas previamente
    p_data.setdefault("subjects_and_roles", "Sujetos principales en escena")
    p_data.setdefault("impact_moments", [])
    proposal = VideoProposal(**p_data)
    return proposal_file, payload, proposal


def cmd_analyze(args: argparse.Namespace) -> None:
    video = download_video(args.url, custom_job_id=args.job_id or "")
    proposal = analyze_and_create_proposal(video, args.style or "")
    payload = {
        "video_path": str(video.file_path),
        "proposal": asdict(proposal),
    }
    proposal_file = PROPOSALS_DIR / f"{video.job_id}.json"
    proposal_file.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def cmd_refine(args: argparse.Namespace) -> None:
    proposal_file, payload, proposal = _load_proposal_payload(args.job_id)
    video_path = Path(payload["video_path"])
    updated = refine_proposal(proposal, args.feedback, video_path=video_path)
    payload["proposal"] = asdict(updated)
    proposal_file.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def cmd_regenerate(args: argparse.Namespace) -> None:
    proposal_file, payload, proposal = _load_proposal_payload(args.job_id)
    video_path = Path(payload["video_path"])
    panel_idx = int(args.panel) if args.panel and int(args.panel) > 0 else None
    ts_val = float(args.timestamp) if args.timestamp is not None and args.timestamp >= 0 else None
    updated = regenerate_storyboard_panel(
        proposal,
        video_path=video_path,
        panel_index=panel_idx,
        custom_prompt=args.prompt or "",
        custom_timestamp=ts_val,
    )
    payload["proposal"] = asdict(updated)
    proposal_file.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def cmd_generate(args: argparse.Namespace) -> None:
    proposal_file, payload, proposal = _load_proposal_payload(args.job_id)
    if args.script:
        proposal.voiceover_script = args.script
    if args.prompt:
        proposal.visual_prompt = args.prompt

    final_path, engine = asyncio.run(
        generate_final_video(proposal, Path(payload["video_path"]))
    )
    print(
        json.dumps(
            {
                "job_id": args.job_id,
                "final_video_path": str(final_path),
                "engine_used": engine,
                "social_caption": proposal.social_caption,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="CLI de AI Manga / Aura Video Editor: Ingesta, Análisis Multimodal, Arte Manga/Baki y Ensamblado FFmpeg."
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_analyze = sub.add_parser(
        "analyze",
        help="Paso 1-3: Descarga video, extrae timestamps/sujetos y genera storyboards Manga/Baki",
    )
    p_analyze.add_argument("--url", required=True, help="URL de YouTube, Instagram, Facebook o TikTok")
    p_analyze.add_argument("--style", default="", help="Estilo o temática objetivo")
    p_analyze.add_argument("--job-id", default="", help="ID opcional del job")
    p_analyze.set_defaults(func=cmd_analyze)

    p_refine = sub.add_parser(
        "refine",
        help="Refina prompts/timestamps con instrucciones del usuario y regenera storyboards",
    )
    p_refine.add_argument("--job-id", required=True, help="ID del trabajo")
    p_refine.add_argument("--feedback", required=True, help="Instrucciones de cambio")
    p_refine.set_defaults(func=cmd_refine)

    p_regen = sub.add_parser(
        "regenerate",
        help="Regenera una imagen específica del storyboard (o todas) con nueva semilla",
    )
    p_regen.add_argument("--job-id", required=True, help="ID del trabajo")
    p_regen.add_argument("--panel", type=int, default=0, help="Número de panel (1..N, o 0 para todos)")
    p_regen.add_argument("--prompt", default="", help="Prompt manga personalizado opcional")
    p_regen.add_argument("--timestamp", type=float, default=-1.0, help="Nuevo timestamp opcional en segundos")
    p_regen.set_defaults(func=cmd_regenerate)

    p_gen = sub.add_parser(
        "generate",
        help="Paso 5: Ensambla el video final 9:16 con cortes, zoom/shake y Phonk + SFX",
    )
    p_gen.add_argument("--job-id", required=True, help="ID del trabajo devuelto por analyze")
    p_gen.add_argument("--script", default="", help="Notas opcionales")
    p_gen.add_argument("--prompt", default="", help="Prompt editado opcional")
    p_gen.set_defaults(func=cmd_generate)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
