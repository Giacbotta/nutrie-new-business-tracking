@echo off
REM Playtomic sampling from a home connection: Playtomic answers 403 to every datacenter
REM (GitHub and Google checked on 23-24/09/2026), so the readings can only be taken from the PC.
REM This wrapper reads, rebuilds the summary and pushes, so the Google Sheet keeps updating by itself.
REM Called by the Windows tasks "Nutrie Padel Playtomic rileva" (read) and "anticipo" (lead).
setlocal
cd /d "%~dp0"
set GIT="C:\Program Files\Git\cmd\git.exe"
python playtomic_occupancy.py %1
python playtomic_occupancy.py summary_csv
%GIT% add data
%GIT% diff --cached --quiet && goto :done
%GIT% commit -m "data: playtomic %1 from the PC %date% %time:~0,5%"
%GIT% pull --rebase --autostash
%GIT% push
:done
endlocal
