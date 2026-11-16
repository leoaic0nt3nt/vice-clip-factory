"""Use a verified CA bundle when a Python installation has no certificate store."""
import os
import ssl


def configure_certificates():
    if os.environ.get("SSL_CERT_FILE") or ssl.get_default_verify_paths().cafile:
        return
    try:
        import certifi
    except ImportError:
        return
    os.environ["SSL_CERT_FILE"] = certifi.where()
