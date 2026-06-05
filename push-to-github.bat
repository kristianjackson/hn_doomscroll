@echo off
REM ---------------------------------------------------------------------------
REM Publishes the hn-doomscroll subdirectory to its standalone GitHub repo:
REM   https://github.com/kristianjackson/hn_doomscroll  (branch: main)
REM
REM It commits any pending hn-doomscroll changes, extracts just this folder's
REM history with `git subtree split`, and pushes it so the repo contains only
REM hn-doomscroll files at the root - none of the other projects in the parent.
REM
REM Run this from anywhere; it operates on the parent monorepo automatically.
REM ---------------------------------------------------------------------------
setlocal
set REMOTE_NAME=hn-origin
set REMOTE_URL=https://github.com/kristianjackson/hn_doomscroll.git
set PREFIX=hn-doomscroll
set SPLIT_BRANCH=hn-doomscroll-only

REM Move to the parent repo root (this script lives in <root>\hn-doomscroll).
cd /d "%~dp0\.."

echo Checking for uncommitted changes in %PREFIX% ...
git diff --quiet -- %PREFIX% && git diff --cached --quiet -- %PREFIX%
if errorlevel 1 (
    echo Uncommitted changes found. Committing them...
    git add %PREFIX%
    git commit -m "Update hn-doomscroll"
) else (
    echo Working tree clean for %PREFIX%.
)

echo Ensuring remote "%REMOTE_NAME%" exists...
git remote get-url %REMOTE_NAME% >nul 2>&1 || git remote add %REMOTE_NAME% %REMOTE_URL%

echo Splitting %PREFIX% into branch "%SPLIT_BRANCH%"...
git branch -D %SPLIT_BRANCH% >nul 2>&1
git subtree split --prefix=%PREFIX% -b %SPLIT_BRANCH%
if errorlevel 1 (
    echo Subtree split failed. Aborting.
    exit /b 1
)

echo Pushing to %REMOTE_NAME%/main ...
git push %REMOTE_NAME% %SPLIT_BRANCH%:main
if errorlevel 1 (
    echo Push failed. If history diverged, you may need: git push %REMOTE_NAME% %SPLIT_BRANCH%:main --force
    exit /b 1
)

echo.
echo Done. https://github.com/kristianjackson/hn_doomscroll
endlocal
