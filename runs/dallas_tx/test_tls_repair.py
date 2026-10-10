#!/usr/bin/env python3
"""Tests for scrapers/_tls_repair.py's chain building, with synthetic
certificates only (no network). The security property under test: an
intermediate fetched over plain HTTP is accepted ONLY if it is provably
signed into a trusted root -- a forged, expired, or unrelated certificate
must be rejected, because on Python 3.13+ anything placed in a CA bundle is
trusted outright.

Run: python3 runs/dallas_tx/test_tls_repair.py
Exit 0 = pass, non-zero = fail.
"""
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from cryptography import x509
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import AuthorityInformationAccessOID, NameOID

from scrapers import _tls_repair as tr

FAILS = []
NOW = datetime.now(timezone.utc)


def case(name, ok):
    print(f"  {'PASS' if ok else 'FAIL'}  {name}")
    if not ok:
        FAILS.append(name)


def make_cert(cn, issuer_cert=None, issuer_key=None, key=None, ca=True,
              days_before=10, days_after=300, aia_url=None):
    key = key or ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn)])
    issuer_name = issuer_cert.subject if issuer_cert is not None else name
    signer = issuer_key or key
    b = (x509.CertificateBuilder()
         .subject_name(name).issuer_name(issuer_name)
         .public_key(key.public_key())
         .serial_number(x509.random_serial_number())
         .not_valid_before(NOW - timedelta(days=days_before))
         .not_valid_after(NOW + timedelta(days=days_after))
         .add_extension(x509.BasicConstraints(ca=ca, path_length=None), critical=True))
    if aia_url:
        b = b.add_extension(x509.AuthorityInformationAccess([
            x509.AccessDescription(AuthorityInformationAccessOID.CA_ISSUERS,
                                   x509.UniformResourceIdentifier(aia_url))]), critical=False)
    return b.sign(signer, hashes.SHA256()), key


def chain():
    root, root_k = make_cert("TEST Root CA")
    inter, inter_k = make_cert("TEST Intermediate CA", root, root_k)
    leaf, _ = make_cert("test.example", inter, inter_k, ca=False,
                        aia_url="http://pki.example.test/int.crt")
    return root, root_k, inter, inter_k, leaf


root, root_k, inter, inter_k, leaf = chain()

print("accepts a genuine chain")
got = tr.build_verified_chain(leaf, lambda c: [inter], [root])
case("returns the one intermediate that links leaf to the trusted root", got == [inter])
direct_leaf, _ = make_cert("direct.example", root, root_k, ca=False)
case("a leaf issued directly by a trusted root needs no intermediates",
     tr.build_verified_chain(direct_leaf, lambda c: [], [root]) == [])

print("rejects what it cannot prove")


def rejects(fetch, roots, **kw):
    try:
        tr.build_verified_chain(leaf, fetch, roots, **kw)
        return False
    except tr.TlsRepairError:
        return True


# forged intermediate: same subject name, attacker's own key -> does NOT verify the leaf
forged, _ = make_cert("TEST Intermediate CA", root, root_k)
case("an impostor intermediate (same name, different key) is rejected",
     rejects(lambda c: [forged], [root]))

# attacker builds their own full chain, but the root isn't in our trust list
evil_root, evil_root_k = make_cert("EVIL Root")
evil_inter, evil_inter_k = make_cert("EVIL Intermediate", evil_root, evil_root_k)
evil_leaf, _ = make_cert("test.example", evil_inter, evil_inter_k, ca=False)
try:
    tr.build_verified_chain(evil_leaf, lambda c: [evil_inter, evil_root], [root])
    evil_ok = False
except tr.TlsRepairError:
    evil_ok = True
case("a self-consistent chain to an untrusted root is rejected", evil_ok)

expired_inter, expired_k = make_cert("TEST Intermediate CA", root, root_k, days_before=400, days_after=-5)
expired_leaf, _ = make_cert("test.example", expired_inter, expired_k, ca=False)
try:
    tr.build_verified_chain(expired_leaf, lambda c: [expired_inter], [root])
    exp_ok = False
except tr.TlsRepairError:
    exp_ok = True
case("an expired intermediate is rejected", exp_ok)
case("no candidate issuer found -> rejected", rejects(lambda c: [], [root]))
case("an unrelated certificate offered as the issuer is rejected",
     rejects(lambda c: [make_cert("Unrelated")[0]], [root]))

# depth limit
i1, k1 = make_cert("I1", root, root_k)
i2, k2 = make_cert("I2", i1, k1)
i3, k3 = make_cert("I3", i2, k2)
deep_leaf, _ = make_cert("deep.example", i3, k3, ca=False)
pool = {c.subject: c for c in (i1, i2, i3)}
fetch_pool = lambda c: [pool[c.issuer]] if c.issuer in pool else []
case("a long-enough depth limit accepts the 3-deep chain",
     len(tr.build_verified_chain(deep_leaf, fetch_pool, [root], max_depth=5)) == 3)
try:
    tr.build_verified_chain(deep_leaf, fetch_pool, [root], max_depth=2)
    deep_ok = False
except tr.TlsRepairError:
    deep_ok = True
case("the depth limit stops an over-long chain", deep_ok)

print("reading the issuer URL from the certificate")
case("CA-Issuers URL is read from the leaf",
     tr.ca_issuer_urls(leaf) == ["http://pki.example.test/int.crt"])
case("a certificate with no AIA yields no URLs", tr.ca_issuer_urls(direct_leaf) == [])
ftp_leaf, _ = make_cert("ftp.example", inter, inter_k, ca=False, aia_url="ldap://x/y")
case("non-http(s) issuer URLs are ignored", tr.ca_issuer_urls(ftp_leaf) == [])

if FAILS:
    print(f"\n{len(FAILS)} FAILED")
    sys.exit(1)
print("\nall passed")
