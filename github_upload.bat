@echo off
chcp 65001 >nul
echo ========================================
echo  SmartVoyage 上传 GitHub 脚本
echo ========================================
echo.

cd /d D:\claude_projects\SmartVoyage

REM ── 第1步：清除锁文件 ──
echo [1/6] 清除 git 锁文件...
if exist .git\index.lock (
    del /f .git\index.lock
    echo       锁文件已删除
) else (
    echo       无锁文件，跳过
)

REM ── 第2步：让 config.py 不再被 git 追踪（文件本地保留）──
echo.
echo [2/6] 从 git 追踪中移除 config.py（本地文件保留不删除）...
git rm --cached config.py 2>nul
if %errorlevel% equ 0 (
    echo       config.py 已从追踪中移除
) else (
    echo       config.py 未被追踪，跳过
)

REM ── 第3步：暂存所有变更 ──
echo.
echo [3/6] 暂存所有变更...
git add -A
echo       暂存完成

REM ── 第4步：提交 ──
echo.
echo [4/6] 提交变更...
git commit -m "feat: 添加 .gitignore 和配置模板，整理项目结构，移除敏感配置"
echo       提交完成

REM ── 第5步：配置远程地址 ──
echo.
echo [5/6] 配置远程仓库地址...
git remote rename origin gitee 2>nul
echo       原 Gitee 远程已重命名为 gitee

REM ⚠️ 请把下面这行的 YOUR_USERNAME 替换为你的 GitHub 用户名
set GITHUB_USER=bq07140
git remote add origin https://github.com/%GITHUB_USER%/SmartVoyage.git
echo       GitHub 远程已添加：https://github.com/%GITHUB_USER%/SmartVoyage.git

REM ── 第6步：推送 ──
echo.
echo [6/6] 推送到 GitHub...
echo       (如弹出登录窗口，请用 GitHub 账号登录)
git push -u origin master

echo.
echo ========================================
echo  全部完成！
echo  请前往 https://github.com/%GITHUB_USER%/SmartVoyage 查看
echo ========================================
pause
