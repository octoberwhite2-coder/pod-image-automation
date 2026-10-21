@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo ============================================
echo   POD 批量出图 (读飞书提示词库)
echo   跑之前确认 ComfyUI 已经打开
echo ============================================
echo.
python feishu_batch.py --queue-all
echo.
echo 出图位置: ComfyUI\output\  (png=透明图 white=白T black=黑T raw=原图)
echo 飞书表里会自动回写: 状态/seed/生成时间/出图文件名/预览图
echo.
pause
