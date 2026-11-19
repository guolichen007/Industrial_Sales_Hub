"""生产 WSGI 入口：Waitress（Windows/Linux 通用）。

    python serve.py              # 读取 ISH_CONFIG_FILE 或环境变量
环境变量：
  ISH_LISTEN      监听地址，默认 0.0.0.0:8000（LAN）。DEMO 模式强制 127.0.0.1。
  ISH_THREADS     工作线程数，默认 6（任务书建议 4–8）。
  ISH_TRUSTED_PROXY  若前面有反向代理（IIS/内网 CA），填写代理 IP，Waitress 仅信任该地址转发的头。
"""
import logging
import os
import sys

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")


def main():
    import django
    from django.conf import settings

    django.setup()
    from waitress import serve

    from config.wsgi import application

    listen = os.environ.get("ISH_LISTEN", "0.0.0.0:8000")
    if settings.MODE == "DEMO":
        listen = "127.0.0.1:" + listen.rsplit(":", 1)[-1]
    threads = int(os.environ.get("ISH_THREADS", "6"))
    kw = {}
    proxy = os.environ.get("ISH_TRUSTED_PROXY", "").strip()
    if proxy:
        kw.update(trusted_proxy=proxy, trusted_proxy_headers={"x-forwarded-for", "x-forwarded-proto", "x-forwarded-host"}, clear_untrusted_proxy_headers=True)
    else:
        kw.update(clear_untrusted_proxy_headers=True)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s", stream=sys.stdout)
    logging.getLogger("waitress").info("Industrial Sales Hub %s mode=%s listen=%s threads=%s https=%s", settings.APP_VERSION, settings.MODE, listen, threads, settings.HTTPS_ENABLED)
    serve(application, listen=listen, threads=threads, ident="IndustrialSalesHub", channel_timeout=120, connection_limit=200,
          max_request_body_size=30 * 1024 * 1024, url_scheme="http", **kw)  # TLS 由前置代理终止；Waitress 本身不提供 HTTPS


if __name__ == "__main__":
    main()
