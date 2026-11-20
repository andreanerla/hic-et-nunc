"""C2PA Content Credentials: embed the process summary in the delivery.

Uses the official `c2pa-python` bindings. Development certificates from
`make_dev_certs` are NOT on the C2PA trust list, so validators will
report the signer as untrusted; that's expected until a production
certificate is obtained from a conformant CA.
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Any

PROCESS_LABEL = "io.github.andreanerla.hic-et-nunc.process.v1"
ATTESTATIONS_LABEL = "io.github.andreanerla.hic-et-nunc.attestations.v1"
DIGITAL_ART = "http://cv.iptc.org/newscodes/digitalsourcetype/digitalArt"


def _base(label: str) -> str:
    head, _, tail = label.rpartition(".")
    return (
        head if head and tail.startswith("v") and tail[1:].isdigit() else label
    )


def build_manifest(
    title: str,
    process: dict[str, Any],
    attestations: list[dict] | None = None,
    generator: str = "hic-et-nunc/0.1",
) -> dict:
    """Return a C2PA manifest definition carrying the process summary.

    `generator` is "name/version".
    """
    assertions: list[dict] = [
        {
            "label": "c2pa.actions",
            "data": {
                "actions": [
                    {
                        "action": "c2pa.created",
                        "digitalSourceType": DIGITAL_ART,
                    }
                ]
            },
        },
        {"label": PROCESS_LABEL, "data": process},
    ]
    if attestations:
        assertions.append(
            {
                "label": ATTESTATIONS_LABEL,
                "data": {"attestations": attestations},
            }
        )
    return {
        "claim_generator_info": [
            {
                "name": generator.split("/")[0],
                "version": generator.split("/")[-1],
            }
        ],
        "title": title,
        "assertions": assertions,
    }


def sign_file(
    src: str | Path,
    dest: str | Path,
    manifest: dict,
    cert_chain_pem: bytes,
    key_pem: bytes,
    tsa_url: str | None = None,
) -> None:
    """Sign `src` with `manifest` and write the result to `dest`.

    The key is ES256; `tsa_url` is an optional RFC 3161 timestamp
    authority.
    """
    import c2pa

    info = c2pa.C2paSignerInfo(
        alg=b"es256",
        sign_cert=cert_chain_pem,
        private_key=key_pem,
        ta_url=tsa_url.encode() if tsa_url else None,
    )
    signer = c2pa.Signer.from_info(info)
    try:
        with c2pa.Builder(json.dumps(manifest)) as builder:
            builder.sign_file(str(src), str(dest), signer)
    finally:
        signer.close()


def read_file(path: str | Path) -> dict:
    """Read the Content Credentials of a signed file.

    Returns a dict with the active manifest (`manifest`), the process
    summary (`process`), any `attestations`, and the SDK's
    `validation_status` and `validation_state`.
    """
    import c2pa

    with c2pa.Reader(str(path)) as reader:
        store = json.loads(reader.json())
    active = store["manifests"][store["active_manifest"]]
    # The SDK stores "x.v1" as label "x" with a version field; compare
    # base labels.
    by_label = {
        _base(a["label"]): a.get("data") for a in active.get("assertions", [])
    }
    return {
        "manifest": active,
        "process": by_label.get(_base(PROCESS_LABEL)),
        "attestations": (by_label.get(_base(ATTESTATIONS_LABEL)) or {}).get(
            "attestations"
        ),
        "validation_status": store.get("validation_status", []),
        "validation_state": store.get("validation_state"),
    }


def make_dev_certs(
    out_dir: str | Path, org: str = "Hic et nunc Dev"
) -> tuple[Path, Path]:
    """Create a throwaway ES256 root CA and a signing certificate.

    Returns (chain_pem_path, key_pem_path). The chain contains the leaf
    then the root, which is what c2pa expects for `sign_cert`.
    """
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    now = dt.datetime.now(dt.timezone.utc)

    def name(cn: str) -> x509.Name:
        return x509.Name(
            [
                x509.NameAttribute(NameOID.ORGANIZATION_NAME, org),
                x509.NameAttribute(NameOID.COMMON_NAME, cn),
            ]
        )

    ca_key = ec.generate_private_key(ec.SECP256R1())
    ca = (
        x509.CertificateBuilder()
        .subject_name(name(f"{org} Root CA"))
        .issuer_name(name(f"{org} Root CA"))
        .public_key(ca_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(minutes=5))
        .not_valid_after(now + dt.timedelta(days=3650))
        .add_extension(
            x509.BasicConstraints(ca=True, path_length=0), critical=True
        )
        .add_extension(
            x509.KeyUsage(
                digital_signature=False,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=True,
                crl_sign=True,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(
            x509.SubjectKeyIdentifier.from_public_key(ca_key.public_key()),
            critical=False,
        )
        .sign(ca_key, hashes.SHA256())
    )

    key = ec.generate_private_key(ec.SECP256R1())
    leaf = (
        x509.CertificateBuilder()
        .subject_name(name(f"{org} Signer"))
        .issuer_name(ca.subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(minutes=5))
        .not_valid_after(now + dt.timedelta(days=365))
        .add_extension(
            x509.BasicConstraints(ca=False, path_length=None), critical=True
        )
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=False,
                crl_sign=False,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(
            x509.ExtendedKeyUsage([ExtendedKeyUsageOID.EMAIL_PROTECTION]),
            critical=False,
        )
        .add_extension(
            x509.SubjectKeyIdentifier.from_public_key(key.public_key()),
            critical=False,
        )
        .add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_public_key(
                ca_key.public_key()
            ),
            critical=False,
        )
        .sign(ca_key, hashes.SHA256())
    )

    chain = out / "chain.pem"
    keyf = out / "signer.key"
    chain.write_bytes(
        leaf.public_bytes(serialization.Encoding.PEM)
        + ca.public_bytes(serialization.Encoding.PEM)
    )
    keyf.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    (out / "root.pem").write_bytes(ca.public_bytes(serialization.Encoding.PEM))
    return chain, keyf
