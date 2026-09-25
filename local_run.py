"""Playtomic sampling from a home connection, then push so the Google Sheet updates by itself.

Playtomic answers HTTP 403 to every datacenter (GitHub and Google Apps Script both checked,
23-24/09/2026), so the readings can only be taken from Giacomo's PC. The Windows tasks
"Nutrie Padel Playtomic rileva" and "... anticipo" call this with "read" or "lead".

Python, not a .cmd: a batch file started by the Windows scheduler has no console and dies
on the spot with a Ctrl+C (checked 24/09/2026). Merge, not rebase: an interrupted rebase
leaves the repository stuck, and "-X ours" keeps the PC's rows, which are the only real
readings (GitHub can only ever rewrite the summary).
"""
import datetime as dt, os, subprocess, sys

HERE = os.path.dirname(os.path.abspath(__file__))
GIT = r"C:\Program Files\Git\cmd\git.exe"
LOG = os.path.join(HERE, "local_run.log")


def run(args):
    # CREATE_NO_WINDOW: started by the Windows scheduler there is no console, and a child that
    # opens its own gets a Ctrl+C when that console closes, killing the run (24/09/2026).
    r = subprocess.run(args, cwd=HERE, capture_output=True, text=True, timeout=900,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    with open(LOG, "a", encoding="utf8") as f:
        f.write(f"{dt.datetime.now():%Y-%m-%d %H:%M} {' '.join(args[:3])} -> {r.returncode}\n")
        for line in (r.stdout + r.stderr).splitlines():
            f.write("    " + line + "\n")
    return r


def main():
    command = sys.argv[1] if len(sys.argv) > 1 else "read"
    with open(LOG, "a", encoding="utf8") as f:
        f.write(f"{dt.datetime.now():%Y-%m-%d %H:%M} start {command}\n")
    run([sys.executable, "playtomic_occupancy.py", command])
    run([sys.executable, "playtomic_occupancy.py", "summary_csv"])
    run([sys.executable, "playtomic_occupancy.py", "monthly_csv"])
    run([GIT, "add", "data"])
    if run([GIT, "diff", "--cached", "--quiet"]).returncode == 0:
        return  # nothing new to send
    run([GIT, "commit", "-m", f"data: playtomic {command} from the PC"])
    run([GIT, "pull", "--no-rebase", "--no-edit", "-X", "ours"])
    run([GIT, "push"])


if __name__ == "__main__":
    try:
        main()
    except Exception:
        import traceback
        with open(LOG, "a", encoding="utf8") as f:
            f.write(f"{dt.datetime.now():%Y-%m-%d %H:%M} crash\n" + traceback.format_exc())
        raise
