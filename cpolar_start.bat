@echo off
REM cpolar 一键暴露本机 8000 服务（面试系统）
REM 用法：
REM   1) 首次使用，先去 https://www.cpolar.com 注册，在控制台「验证」页拿到 authtoken，
REM      在本文件同目录执行一次（把 xxxx 换成你的 token）：
REM        "d:\tools\cpolar\extracted\cpolar\cpolar.exe" authtoken xxxx
REM   2) 直接双击本脚本即可，终端会打印一个公网地址（如 https://xxxx.cpolar.top），
REM      把该地址发给国内朋友，无需翻墙即可访问。
REM   注意：免费版地址每 24 小时变化、带宽有限，仅适合给朋友试用。

"d:\tools\cpolar\extracted\cpolar\cpolar.exe" http 8000
pause
