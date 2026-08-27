"""Build the owned-runtime tunnel template using the pinned official Xray schema."""

from pathlib import PurePosixPath

import crossplane


def node(name, *args, block=None):
    result = {"directive": name, "args": list(args)}
    if block is not None:
        result["block"] = block
    return result


def managed_tunnel_bundle(payload, domain, cert_name, nginx, xray):
    site_value = payload.site_value or nginx["html_path"]
    cert_dir = PurePosixPath(nginx["certificate_dir"])
    http = [
        node(
            "map",
            "$http_upgrade",
            "$open_node_connection_upgrade",
            block=[
                node("default", "upgrade"),
                node("", "close"),
            ],
        ),
        node("include", "servers/*.conf"),
    ]
    location = [node("root", site_value), node("index", "index.html")]
    if payload.site_type == "proxy":
        location = [
            node("proxy_pass", site_value),
            node("proxy_http_version", "1.1"),
            node("proxy_set_header", "Host", "$host"),
            node("proxy_set_header", "Upgrade", "$http_upgrade"),
            node("proxy_set_header", "Connection", "$open_node_connection_upgrade"),
            node("proxy_set_header", "X-Real-IP", "$proxy_protocol_addr"),
            node("proxy_set_header", "X-Forwarded-For", "$proxy_protocol_addr"),
            node("proxy_set_header", "X-Forwarded-Proto", "https"),
            node("proxy_ssl_server_name", "on"),
            node("proxy_ssl_verify", "on"),
            node("proxy_ssl_trusted_certificate", "/etc/ssl/certs/ca-certificates.crt"),
            node("proxy_connect_timeout", "10s"),
            node("proxy_read_timeout", "60s"),
        ]
    server = node(
        "server",
        block=[
            node("listen", f"127.0.0.1:{payload.nginx_port}", "ssl", "proxy_protocol"),
            node("server_name", domain),
            node("set_real_ip_from", "127.0.0.1"),
            node("real_ip_header", "proxy_protocol"),
            node("ssl_certificate", str(cert_dir / (cert_name + ".pem"))),
            node("ssl_certificate_key", str(cert_dir / (cert_name + ".key"))),
            node("ssl_protocols", "TLSv1.2", "TLSv1.3"),
            node("location", "/", block=location),
        ],
    )
    main = [node("events", block=[node("worker_connections", "1024")]), node("http", block=http)]
    inbound = xray["inbounds"][0]
    inbound.update(listen=payload.listen_address, port=payload.listen_port)
    inbound["settings"]["port"] = payload.forward_port
    xray["inbounds"] = [inbound]
    xray["api"]["listen"] = f"127.0.0.1:{payload.api_port}"
    xray["metrics"]["listen"] = f"127.0.0.1:{payload.metrics_port}"
    xray["routing"]["rules"] = [
        {
            "type": "field",
            "inboundTag": ["tunnel-in"],
            "domain": ["full:" + domain],
            "outboundTag": "nginx",
        },
        {"type": "field", "inboundTag": ["tunnel-in"], "outboundTag": "direct"},
    ]
    for outbound in xray["outbounds"]:
        if outbound["tag"] == "nginx":
            outbound["settings"]["redirect"] = f"127.0.0.1:{payload.nginx_port}"
    return crossplane.build(main) + "\n", crossplane.build([server]) + "\n", http, xray
