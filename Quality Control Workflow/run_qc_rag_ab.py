# run_qc_rag_ab.py
# Two-pass QC batch: RAG on vs RAG off (same config), one CSV with rag_on column (gi_rag_ab template).

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

_QC = Path(__file__).resolve().parent
sys.path.insert(0, str(_QC))
from paths import JA_ROOT, QC_ROOT  # noqa: E402

RUN_VARIANTS = QC_ROOT / "run_variants.py"
DOTENV_PATH = JA_ROOT / ".env"

SUPABASE_KEYS = ("SUPABASE_URL", "SUPABASE_KEY", "SUPABASE_SERVICE_ROLE_KEY")


def _merge_dotenv(base: dict[str, str]) -> dict[str, str]:
    """Overlay JournalAnalyzer/.env onto base (string values only)."""
    try:
        from dotenv import dotenv_values
    except ImportError:
        return base
    if not DOTENV_PATH.is_file():
        return base
    merged = dict(base)
    for k, v in dotenv_values(DOTENV_PATH).items():
        if v is not None:
            merged[k] = str(v)
    return merged


def _str_dict(d: dict[str, str]) -> dict[str, str]:
    return {k: "" if v is None else str(v) for k, v in d.items()}


def build_env_rag_on() -> dict[str, str]:
    """Full environment for subprocess: current process env + .env overlay."""
    return _str_dict(_merge_dotenv(dict(os.environ)))


def build_env_rag_off(env_rag_on: dict[str, str]) -> dict[str, str]:
    """
    Force embedding_pipeline.rag_available() false: no Supabase client.
    If EMBEDDING_BACKEND is openai, also drop OPENAI_API_KEY so rag_available stays false.
    """
    out = dict(env_rag_on)
    for k in SUPABASE_KEYS:
        out.pop(k, None)
    eb = (out.get("EMBEDDING_BACKEND") or "").strip().lower()
    if eb in ("openai", "open_ai"):
        out.pop("OPENAI_API_KEY", None)
    return out


def _print_diff(env_on: dict[str, str], env_off: dict[str, str]) -> None:
    print("--- RAG off pass: removed or unset keys (vs RAG on) ---")
    for k in SUPABASE_KEYS:
        had = k in env_on and env_on.get(k)
        now = env_off.get(k)
        if had and not now:
            print(f"  {k}: (was set) -> removed")
    eb = (env_on.get("EMBEDDING_BACKEND") or "").strip().lower()
    if eb in ("openai", "open_ai"):
        if env_on.get("OPENAI_API_KEY") and not env_off.get("OPENAI_API_KEY"):
            print("  OPENAI_API_KEY: (was set) -> removed (openai embedding backend)")


def run_both(
    config: Path,
    csv_out: Path,
    *,
    dry_run: bool,
) -> int:
    config = config.resolve()
    csv_out = csv_out.resolve()
    if not config.is_file():
        print(f"Error: config not found: {config}", file=sys.stderr)
        return 1
    if not RUN_VARIANTS.is_file():
        print(f"Error: run_variants.py not found: {RUN_VARIANTS}", file=sys.stderr)
        return 1

    env_on = build_env_rag_on()
    env_off = build_env_rag_off(env_on)

    _print_diff(env_on, env_off)
    if dry_run:
        print("(dry-run: not executing run_variants.py)")
        return 0

    cmd_base = [
        sys.executable,
        str(RUN_VARIANTS),
        "--config",
        str(config),
        "--csv",
        str(csv_out),
        "--output-schema",
        "gi_rag_ab",
        "--quiet",
    ]

    print("=== Pass 1: RAG on ===")
    env_pass1 = dict(env_on)
    env_pass1["QC_RAG_ON"] = "true"
    r1 = subprocess.run(cmd_base + ["--rag-on", "true"], cwd=str(JA_ROOT), env=env_pass1)
    if r1.returncode != 0:
        print(f"Pass 1 failed with exit code {r1.returncode}", file=sys.stderr)
        return r1.returncode

    print("=== Pass 2: RAG off ===")
    env_pass2 = dict(env_off)
    env_pass2["QC_RAG_ON"] = "false"
    r2 = subprocess.run(cmd_base + ["--rag-on", "false"], cwd=str(JA_ROOT), env=env_pass2)
    if r2.returncode != 0:
        print(f"Pass 2 failed with exit code {r2.returncode}", file=sys.stderr)
        return r2.returncode

    print("Done. Single CSV:", csv_out)
    return 0


def main() -> None:
    p = argparse.ArgumentParser(
        description=(
            "Run QC twice (RAG available vs unavailable) with the same config; "
            "append both passes to one CSV (gi_rag_ab: Set B columns + rag_on)."
        )
    )
    p.add_argument(
        "--config",
        type=Path,
        default=QC_ROOT / "qc_config.yaml",
        help="Path to QC YAML (same file for both passes)",
    )
    p.add_argument(
        "--csv",
        type=Path,
        default=QC_ROOT / "qc_experiment_scores_rag_ab.csv",
        help="Single output CSV (gi_rag_ab header; both passes append here)",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Print env diff only; do not run run_variants.py",
    )
    args = p.parse_args()
    raise SystemExit(run_both(args.config, args.csv, dry_run=args.dry_run))


if __name__ == "__main__":
    main()
