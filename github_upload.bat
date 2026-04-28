@echo off
chcp 65001 >nul
echo ========================================
echo  SmartVoyage 推送到 GitHub
echo ========================================
echo.
echo  准备工作已全部完成：
echo  - commit 已提交（76 个文件）
echo  - .gitignore 和 config.example.py 已就绪
echo  - origin 已指向 GitHub
echo.

cd /d D:\claude_projects\SmartVoyage

REM ── 清除残留锁文件 ──
if exist .git\index.lock  del /f .git\index.lock
if exist .git\config.lock del /f .git\config.lock
if exist .git\HEAD.lock   del /f .git\HEAD.lock

REM ── 确认远程地址 ──
echo [检查] 当前远程仓库：
git remote -v
echo.

REM ── 推送到 GitHub ──
echo [推送] 正在推送到 GitHub...
echo       如弹出登录窗口，请用 GitHub 账号登录
echo       或使用 Personal Access Token 作为密码
echo.
git push -u origin master

if %errorlevel% equ 0 (
    echo.
    echo ========================================
    echo  推送成功！
    echo  请前往 https://github.com/bq07140/SmartVoyage 查看
    echo ========================================
) else (
    echo.
    echo ========================================
    echo  推送失败，请检查：
    echo  1. GitHub 用户名/密码（用 Token 作为密码）
    echo  2. 仓库 https://github.com/bq07140/SmartVoyage 已创建
    echo  3. 网络是否可以访问 GitHub
    echo ========================================
)
pause
