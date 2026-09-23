"""One-command local jury demo: environment, official data, validation and browser."""

import argparse
import importlib.metadata
import os
from pathlib import Path
import subprocess
import sys
import venv
import zipfile


ROOT = Path(__file__).resolve().parent


def dependencies_ok():
    try:
        for line in (ROOT / "requirements.txt").read_text().splitlines():
            if not line.strip() or line.startswith("#"):
                continue
            name, version = line.strip().split("==")
            if importlib.metadata.version(name) != version:
                return False
        return True
    except (importlib.metadata.PackageNotFoundError, ValueError):
        return False


def runtime(use_current=False):
    if sys.version_info < (3, 11):
        raise RuntimeError("Install Python 3.11 or newer, then run this command again.")
    if use_current:
        if not dependencies_ok():
            raise RuntimeError("Install requirements.txt in the selected environment.")
        return None
    directory = ROOT / ".venv"
    interpreter = directory / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    if Path(sys.prefix).resolve() == directory.resolve() and dependencies_ok():
        return None
    if not interpreter.is_file():
        print("Preparing .venv (Python 3.11+)...", flush=True)
        venv.EnvBuilder(with_pip=True).create(directory)
    probe = subprocess.run([str(interpreter), "-B", "-c",
                            "import run; raise SystemExit(not run.dependencies_ok())"], cwd=ROOT,
                           capture_output=True, timeout=30)
    if probe.returncode:
        print("Installing the two pinned dependencies (internet required on first launch)...", flush=True)
        subprocess.run([str(interpreter), "-m", "pip", "install", "--disable-pip-version-check",
                        "--no-input", "--timeout", "20", "--retries", "1",
                        "-r", str(ROOT / "requirements.txt")], cwd=ROOT, check=True, timeout=180)
        subprocess.run([str(interpreter), "-m", "pip", "check"], cwd=ROOT, check=True, timeout=30)
    return subprocess.call([str(interpreter), "-B", "-X", "utf8", str(ROOT / "run.py"), *sys.argv[1:]], cwd=ROOT)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package", type=Path, help="Official participant ZIP or directory; needed only once")
    parser.add_argument("--check", action="store_true", help="Validate 11 official runs and export CSV; no server")
    parser.add_argument("--port", type=int, default=8765, help="Loopback web port (default: 8765)")
    parser.add_argument("--no-browser", action="store_true", help="Print the URL without opening a browser")
    parser.add_argument("--llm", action="store_true", help="Enable optional OpenAI ranking; OPENAI_API_KEY required")
    parser.add_argument("--use-current-env", action="store_true", help="Use an already prepared Python instead of .venv")
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("port must be between 1 and 65535")
    if args.check and args.llm:
        parser.error("--check is an offline reproducibility check; use --llm for the interactive demo")
    os.chdir(ROOT)
    os.environ["PYTHONUTF8"] = "1"
    try:
        exit_code = runtime(args.use_current_env)
        if exit_code is not None:
            return exit_code
        from scripts.import_participant_package import import_package
        source = args.package
        if source is None and not (ROOT / "customer_profile.csv").exists() and sys.stdin.isatty():
            print("Official datasets are not included in Git. Drag the participant ZIP/folder here.")
            source = Path(input("Participant package path: ").strip().strip('"').strip("'"))
        import_package(source.resolve() if source else None)
        os.environ["CEREBRUM_LLM"] = "1" if args.llm else "0"
        if args.llm and not os.environ.get("OPENAI_API_KEY"):
            print("OPENAI_API_KEY is missing: continuing with local hypothesis ranking.", flush=True)
        if args.check:
            subprocess.run([sys.executable, "-B", "-X", "utf8", "jury_eval.py", "--check"],
                           cwd=ROOT, check=True, timeout=7860)
            return 0
        from preview_server import serve
        serve(args.port, open_browser=not args.no_browser)
        return 0
    except KeyboardInterrupt:
        print("\nCerebrum stopped.")
        return 0
    except (OSError, ValueError, RuntimeError, zipfile.BadZipFile, subprocess.SubprocessError) as exc:
        if isinstance(exc, OSError) and getattr(exc, "winerror", None) == 10048 or isinstance(exc, OSError) and exc.errno == 98:
            print("Port is busy. Use: python run.py --port 8766", file=sys.stderr)
        else:
            print(f"Start failed: {exc}\nFor data setup: python run.py --package <participant.zip-or-folder>", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
