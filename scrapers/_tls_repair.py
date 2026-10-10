"""
Repair for HTTPS servers that omit their intermediate CA certificate.

Why this exists (2026-10-09): taxsales.lgbs.com started sending only its own
leaf certificate, without the DigiCert intermediate that signed it. Browsers
and curl on Windows quietly fetch the missing link (the leaf's own
"Authority Information Access -> CA Issuers" URL); Python's `ssl` does not,
so every request failed with CERTIFICATE_VERIFY_FAILED ("unable to get local
issuer certificate") and the whole Dallas daily run died at that step.

What this does, safely -- verification stays ON:
  1. Read the leaf certificate the server presents (an unverified handshake,
     no application data sent) and its CA-Issuers URL(s).
  2. Download the issuer certificate(s) from that URL.
  3. CRYPTOGRAPHICALLY VERIFY each link: leaf signed by the fetched
     intermediate, intermediate signed by ... up to a root that is already in
     the `certifi` trust bundle. This step is what makes fetching over plain
     HTTP acceptable, and it is mandatory: on Python 3.13+ a certificate in
     the CA bundle is a trust anchor in its own right, so an unvalidated
     intermediate would otherwise be trusted outright.
  4. Write a bundle (certifi roots + the verified intermediates) and return
     its path for `requests`' `verify=`.

If any step cannot be proven, this raises -- it never falls back to
disabling verification.
"""
from __future__ import annotations

import socket
import ssl
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import certifi
from cryptography import x509
from cryptography.hazmat.primitives import serialization
from cryptography.x509.oid import AuthorityInformationAccessOID

MAX_CHAIN_DEPTH = 4
MAX_CERT_BYTES = 64 * 1024


class TlsRepairError(RuntimeError):
    pass


def _load_roots() -> list:
    import warnings

    with warnings.catch_warnings():
        # certifi ships one very old root with a non-positive serial number,
        # which `cryptography` warns about on every load; not actionable here.
        warnings.simplefilter("ignore")
        return x509.load_pem_x509_certificates(Path(certifi.where()).read_bytes())


def ca_issuer_urls(cert: x509.Certificate) -> list:
    try:
        aia = cert.extensions.get_extension_for_class(x509.AuthorityInformationAccess).value
    except x509.ExtensionNotFound:
        return []
    return [
        d.access_location.value
        for d in aia
        if d.access_method == AuthorityInformationAccessOID.CA_ISSUERS
        and isinstance(d.access_location, x509.UniformResourceIdentifier)
        and d.access_location.value.lower().startswith(("http://", "https://"))
    ]


def _issued_by(cert: x509.Certificate, issuer: x509.Certificate) -> bool:
    try:
        cert.verify_directly_issued_by(issuer)
        return True
    except Exception:  # noqa: BLE001 -- any failure means "not proven"
        return False


def _currently_valid(cert: x509.Certificate, now: datetime) -> bool:
    return cert.not_valid_before_utc <= now <= cert.not_valid_after_utc


def build_verified_chain(leaf, fetch_issuer, roots, now=None, max_depth=MAX_CHAIN_DEPTH) -> list:
    """Return the intermediate certificates (leaf-issuer first) that connect
    `leaf` to a certificate in `roots`, each link signature-verified and
    within its validity period. `fetch_issuer(cert)` returns candidate issuer
    certificates for `cert` (normally by following its CA-Issuers URL).
    Raises TlsRepairError if no proven path exists. Pure of any network or
    filesystem access, so it is unit-testable with synthetic certificates."""
    now = now or datetime.now(timezone.utc)
    intermediates: list = []
    current = leaf
    for _ in range(max_depth):
        # Done as soon as `current` is directly issued by a trusted root.
        for root in roots:
            if root.subject == current.issuer and _issued_by(current, root):
                if not _currently_valid(root, now):
                    raise TlsRepairError(f"trusted root {root.subject.rfc4514_string()} is not currently valid")
                return intermediates
        issuer = next(
            (c for c in fetch_issuer(current)
             if c.subject == current.issuer and _issued_by(current, c)),
            None,
        )
        if issuer is None:
            raise TlsRepairError(
                f"could not find a certificate that signed {current.subject.rfc4514_string()}"
            )
        if not _currently_valid(issuer, now):
            raise TlsRepairError(f"issuer {issuer.subject.rfc4514_string()} is expired or not yet valid")
        intermediates.append(issuer)
        current = issuer
    raise TlsRepairError("certificate chain too deep / did not reach a trusted root")


def _fetch_leaf(host: str, port: int, timeout: float) -> x509.Certificate:
    # Unverified on purpose, solely to read the certificate the server shows;
    # nothing is sent and nothing from it is trusted until chain-verified.
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    with socket.create_connection((host, port), timeout=timeout) as sock:
        with ctx.wrap_socket(sock, server_hostname=host) as tls:
            der = tls.getpeercert(binary_form=True)
    return x509.load_der_x509_certificate(der)


def _download_certs(url: str, timeout: float) -> list:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 -- chain-verified below
        data = resp.read(MAX_CERT_BYTES + 1)
    if len(data) > MAX_CERT_BYTES:
        raise TlsRepairError(f"issuer certificate at {url} is implausibly large")
    if b"-----BEGIN CERTIFICATE-----" in data:
        return x509.load_pem_x509_certificates(data)
    return [x509.load_der_x509_certificate(data)]


def verified_bundle_for(host: str, cache_dir: Path, port: int = 443, timeout: float = 20.0) -> str:
    """Path to a CA bundle that lets `requests` verify `host` despite its
    missing intermediate. Cached per host; the cache is only a convenience --
    it is rebuilt (and re-verified) if absent."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    out = cache_dir / f"{host}-ca-bundle.pem"

    leaf = _fetch_leaf(host, port, timeout)

    def fetch_issuer(cert):
        found: list = []
        for url in ca_issuer_urls(cert):
            try:
                found.extend(_download_certs(url, timeout))
            except Exception:  # noqa: BLE001 -- try the next URL
                continue
        return found

    intermediates = build_verified_chain(leaf, fetch_issuer, _load_roots())
    if not intermediates:
        raise TlsRepairError(f"{host} already chains to a trusted root; nothing to repair")

    pem = Path(certifi.where()).read_bytes()
    if not pem.endswith(b"\n"):
        pem += b"\n"
    for c in intermediates:
        pem += c.public_bytes(serialization.Encoding.PEM)
    tmp = out.with_suffix(".pem.tmp")
    tmp.write_bytes(pem)
    tmp.replace(out)
    return str(out)
