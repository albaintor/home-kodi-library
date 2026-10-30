"""Persistent self-signed TLS and an HTTPS TCP relay to the existing HTTP app."""
import asyncio
import hashlib
import os
import ssl
from datetime import UTC, datetime, timedelta
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID


def ensure_certificate(directory: Path) -> tuple[Path, Path]:
    root = Path(directory) / "tls"
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    cert, private = root / "server.crt", root / "server.key"
    if cert.exists() != private.exists():
        raise RuntimeError("Incomplete TLS identity: restore certificate and key from backup.")
    if not cert.exists():
        key = rsa.generate_private_key(public_exponent=65537, key_size=3072)
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Home Media Catalog")])
        now = datetime.now(UTC)
        certificate = (x509.CertificateBuilder().subject_name(name).issuer_name(name)
                       .public_key(key.public_key()).serial_number(x509.random_serial_number())
                       .not_valid_before(now-timedelta(minutes=5))
                       .not_valid_after(now+timedelta(days=3650))
                       .add_extension(x509.BasicConstraints(ca=False, path_length=None), True)
                       .sign(key, hashes.SHA256()))
        items = (
            (private, key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                        serialization.NoEncryption())),
            (cert, certificate.public_bytes(serialization.Encoding.PEM)),
        )
        for path, payload in items:
            fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as output:
                output.write(payload)
            os.chmod(path, 0o600)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(str(cert), str(private))
    return cert, private


def fingerprint(directory: Path) -> str:
    cert, _ = ensure_certificate(directory)
    value = x509.load_pem_x509_certificate(cert.read_bytes())
    return hashlib.sha256(value.public_bytes(serialization.Encoding.DER)).hexdigest()


async def pipe(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        while data := await reader.read(65536):
            writer.write(data)
            await writer.drain()
    except (ConnectionError, OSError):
        pass


async def proxy(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        # 127.0.0.2 marks TLS-relayed requests: FastAPI must never grant
        # them implicit LAN administrator privileges. Ordinary HTTP keeps
        # its original client IP because uvicorn serves that port directly.
        upstream_reader, upstream_writer = await asyncio.open_connection(
            "127.0.0.1", int(os.getenv("LIBRARY_HTTP_PORT", "8000")),
            local_addr=("127.0.0.2", 0),
        )
    except OSError:
        writer.close()
        return
    jobs = [asyncio.create_task(pipe(reader, upstream_writer)),
            asyncio.create_task(pipe(upstream_reader, writer))]
    try:
        _, pending = await asyncio.wait(jobs, return_when=asyncio.FIRST_COMPLETED)
        for job in pending:
            job.cancel()
        await asyncio.gather(*jobs, return_exceptions=True)
    finally:
        upstream_writer.close()
        writer.close()
        await asyncio.gather(upstream_writer.wait_closed(), writer.wait_closed(),
                             return_exceptions=True)


async def main() -> None:
    import uvicorn
    # Prevent loopback relay peers from acquiring LOCAL_AUTH_NETWORKS privileges.
    os.environ["HMC_TLS_RELAY_ACTIVE"] = "1"
    cert, key = ensure_certificate(Path(os.getenv("DATA_DIR", "/data")))
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(str(cert), str(key))
    host = os.getenv("LIBRARY_BIND_ADDRESS", "0.0.0.0")
    tls_port = int(os.getenv("TLS_BIND_PORT", "8443"))
    https = await asyncio.start_server(proxy, host=host, port=tls_port, ssl=context)
    http_port = int(os.getenv("LIBRARY_HTTP_PORT", "8000"))
    if http_port == tls_port:
        raise ValueError("HTTP and HTTPS listeners must use distinct ports.")
    server = uvicorn.Server(uvicorn.Config(
        "home_media_catalog.app:create_app", factory=True,
        host=host, port=http_port, workers=1, proxy_headers=False,
    ))
    try:
        await server.serve()
    finally:
        https.close()
        await https.wait_closed()


if __name__ == "__main__":
    asyncio.run(main())
