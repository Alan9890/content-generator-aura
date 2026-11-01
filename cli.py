import argparse
import asyncio
import json
from dataclasses import asdict
from pathlib import Path
from downloader import download_video
from analyzer import VideoProposal, analyze_and_create_proposal, refine_proposal
from generator import generate_final_video
from config import WORK_DIR

PROPOSALS_DIR = WORK_DIR / "proposals"
PROPOSALS_DIR.mkdir(parents=True, exist_ok=True)


def cmd_analyze(args: argparse.Namespace) -> None:
    video = download_video(args.url, custom_job_id=args.job_id or "")
    proposal = analyze_and_create_proposal(video, args.style or "")
    payload = {
        "video_path": str(video.file_path),
        "proposal": asdict(proposal),
    }
    proposal_file = PROPOSALS_DIR / f"{video.job_id}.json"
    proposal_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def cmd_refine(args: argparse.Namespace) -> None:
    proposal_file = PROPOSALS_DIR / f"{args.job_id}.json"
    if not proposal_file.exists():
        raise FileNotFoundError(f"No existe la propuesta para job_id={args.job_id}")
    payload = json.loads(proposal_file.read_text(encoding="utf-8"))
    proposal = VideoProposal(**payload["proposal"])
    updated = refine_proposal(proposal, args.feedback)
    payload["proposal"] = asdict(updated)
    proposal_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def cmd_generate(args: argparse.Namespace) -> None:
    proposal_file = PROPOSALS_DIR / f"{args.job_id}.json"
    if not proposal_file.exists():
        raise FileNotFoundError(f"No existe la propuesta para job_id={args.job_id}")

    payload = json.loads(proposal_file.read_text(encoding="utf-8"))
    p_data = payload["proposal"]
    if args.script:
        p_data["voiceover_script"] = args.script
    if args.prompt:
        p_data["visual_prompt"] = args.prompt

    proposal = VideoProposal(**p_data)
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
        description="CLI para Rust / OpenClaw / Hermes: Descarga, analiza y recrea videos con IA."
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_analyze = sub.add_parser("analyze", help="Descarga un video y genera el guion + prompt IA")
    p_analyze.add_argument("--url", required=True, help="URL de YouTube, Instagram, Facebook o TikTok")
    p_analyze.add_argument("--style", default="", help="Estilo o temática objetivo")
    p_analyze.add_argument("--job-id", default="", help="ID opcional del job")
    p_analyze.set_defaults(func=cmd_analyze)

    p_refine = sub.add_parser("refine", help="Refina el guion y prompt con feedback del usuario")
    p_refine.add_argument("--job-id", required=True, help="ID del trabajo")
    p_refine.add_argument("--feedback", required=True, help="Instrucciones de cambio")
    p_refine.set_defaults(func=cmd_refine)

    p_gen = sub.add_parser("generate", help="Genera el video final tras la aprobación del usuario")
    p_gen.add_argument("--job-id", required=True, help="ID del trabajo devuelto por analyze")
    p_gen.add_argument("--script", default="", help="Guion editado opcional")
    p_gen.add_argument("--prompt", default="", help="Prompt visual editado opcional")
    p_gen.set_defaults(func=cmd_generate)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
