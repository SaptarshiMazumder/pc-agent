"""Public-internet-only proxy for sandbox tasks. Resolve, validate, then pin the address.

The task security group allows only this proxy and S3; application checks are never
the network isolation boundary. No credentials, tenant volumes or request logging.
"""
from __future__ import annotations

import asyncio
import ipaddress
import socket
from urllib.parse import urlsplit

MAX_CONNECTIONS = 128


def public_address(value: str) -> bool:
    address = ipaddress.ip_address(value)
    return address.is_global and not address.is_multicast and not address.is_reserved


async def resolve_public(host: str, port: int) -> str:
    if port not in (80, 443):
        raise PermissionError("port denied")
    addresses = await asyncio.get_running_loop().getaddrinfo(
        host, port, type=socket.SOCK_STREAM
    )
    ips = [result[4][0] for result in addresses]
    if not ips or not all(public_address(ip) for ip in ips):
        raise PermissionError("non-public destination denied")
    return ips[0]


async def relay(reader, writer):
    while data := await reader.read(65536):
        writer.write(data)
        await writer.drain()


async def connection(reader, writer, slots):
    upstream = None
    try:
        async with slots, asyncio.timeout(900):
            header = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 15)
            if len(header) > 32768:
                raise ValueError("headers too large")
            lines = header.decode("latin1").split("\r\n")
            method, target, version = lines[0].split(" ")
            if method == "CONNECT":
                parsed = urlsplit("//" + target)
                host, port = parsed.hostname, parsed.port or 443
            else:
                parsed = urlsplit(target)
                if parsed.scheme != "http" or parsed.username or parsed.password:
                    raise PermissionError("invalid HTTP destination")
                host, port = parsed.hostname, parsed.port or 80
            if not host:
                raise ValueError("missing host")
            ip = await resolve_public(host, port)
            remote_reader, upstream = await asyncio.wait_for(asyncio.open_connection(ip, port), 15)
            if method == "CONNECT":
                writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
                await writer.drain()
            else:
                path = parsed.path or "/"
                if parsed.query:
                    path += "?" + parsed.query
                filtered = [line for line in lines[1:] if line and not line.lower().startswith(
                    ("proxy-", "connection:", "host:")
                )]
                authority = host if port == 80 else f"{host}:{port}"
                request = f"{method} {path} {version}\r\nHost: {authority}\r\n"
                request += "\r\n".join(filtered) + "\r\nConnection: close\r\n\r\n"
                upstream.write(request.encode("latin1"))
                await upstream.drain()
            flows = [asyncio.create_task(relay(reader, upstream)),
                     asyncio.create_task(relay(remote_reader, writer))]
            try:
                await asyncio.wait(flows, return_when=asyncio.FIRST_COMPLETED)
            finally:
                for flow in flows:
                    flow.cancel()
                await asyncio.gather(*flows, return_exceptions=True)
    except (Exception, asyncio.CancelledError):
        try:
            writer.write(b"HTTP/1.1 403 Forbidden\r\nConnection: close\r\nContent-Length: 0\r\n\r\n")
            await writer.drain()
        except Exception:
            pass
    finally:
        if upstream:
            upstream.close()
        writer.close()


async def main():
    slots = asyncio.Semaphore(MAX_CONNECTIONS)
    server = await asyncio.start_server(lambda r, w: connection(r, w, slots), "0.0.0.0", 8080)
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    asyncio.run(main())
