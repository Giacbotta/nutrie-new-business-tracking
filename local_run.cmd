@echo off
REM Playtomic sampling from a home connection: Playtomic answers 403 to every datacenter
REM (GitHub and Google checked on 23-24/09/2026), so the readings can only be taken from the PC.
REM Read, rebuild the summary, then push, so the Google Sheet keeps updating by itself.
REM Called by the Windows tasks "Nutrie Padel Playtomic rileva" (read) and "anticipo" (lead).
REM The log stays out of the repository: git cannot replace a file that is open for writing.
setlocal
cd /d "%~dp0"
set GIT="C:\Program Files\Git\cmd\git.exe"
python playtomic_occupancy.py %1
python playtomic_occupancy.py summary_csv
%GIT% add data
%GIT% diff --cached --quiet && goto :done
%GIT% commit -m "data: playtomic %1 from the PC"
%GIT% pull --rebase
%GIT% push
:done
endlocal
