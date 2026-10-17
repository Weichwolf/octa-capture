# SPDX-License-Identifier: GPL-2.0-only
"""Restrict the desktop web view to the panel and its own settings downloads."""

from urllib.parse import urlsplit


def local_document(uri, origin):
    if type(uri) is not str or any(ord(char) < 32 or ord(char) == 127 for char in uri):
        return False
    try:
        parsed, expected = urlsplit(uri), urlsplit(origin)
        return (parsed.scheme == expected.scheme == 'http' and parsed.netloc == expected.netloc and
                parsed.path in ('/', '/index.html') and not parsed.query)
    except ValueError:
        return False


def local_download(uri, origin):
    return (type(uri) is str and uri.startswith('blob:' + origin + '/') and
            not any(ord(char) < 32 or ord(char) == 127 for char in uri))
