# -*- coding: utf-8 -*-
"""Общий SSL-контекст для запросов через urllib.

На части macOS у собранного PyInstaller-приложения нет доступа к системному
хранилищу сертификатов (Keychain), и стандартный urlopen падает с
CERTIFICATE_VERIFY_FAILED: unable to get local issuer certificate. certifi
даёт свой набор корневых сертификатов, который PyInstaller упаковывает
вместе с приложением, — используем его вместо системных путей по умолчанию.
"""
import ssl

import certifi

_CONTEXT = None


def ssl_context():
    global _CONTEXT
    if _CONTEXT is None:
        _CONTEXT = ssl.create_default_context(cafile=certifi.where())
    return _CONTEXT
