"""HTTP -> HTTPS 跳转服务(监听 80 端口)。

所有请求 308 永久跳转到 https://mockin.cn,保留路径与查询参数。
配合 /etc/systemd/system/interview-http.service 运行。
"""
from starlette.applications import Starlette
from starlette.responses import RedirectResponse
from starlette.routing import Route

HTTPS_HOST = "mockin.cn"


async def redirect_to_https(request):
    url = request.url
    target = f"https://{HTTPS_HOST}{url.path}"
    if url.query:
        target += f"?{url.query}"
    return RedirectResponse(target, status_code=308)


app = Starlette(routes=[Route("/{path:path}", redirect_to_https)])
