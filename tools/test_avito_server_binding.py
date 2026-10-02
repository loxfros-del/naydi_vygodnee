"""Real loopback socket regressions: one Avito server must own its port."""
from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import os
from pathlib import Path
import socket
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from avito_service.config import ServiceConfig
from avito_service.http_api import make_server


def server(port=0):
    # No provider construction, local secrets, job requests, or external network.
    return make_server("127.0.0.1", port, service=object(), config=ServiceConfig(),
                       owner_token="", access_token="")


class AvitoServerBindingTests(unittest.TestCase):
    def test_second_real_server_cannot_listen_on_existing_address(self):
        with server() as first:
            self.assertEqual(first.socket.getsockopt(socket.SOL_SOCKET, socket.SO_ACCEPTCONN), 1)
            with self.assertRaises(OSError):
                with server(first.server_address[1]):
                    pass

    def test_new_server_cannot_share_a_legacy_http_server_port(self):
        with ThreadingHTTPServer(("127.0.0.1", 0), BaseHTTPRequestHandler) as legacy:
            with self.assertRaises(OSError):
                with server(legacy.server_address[1]):
                    pass

    @unittest.skipUnless(os.name == "nt", "Windows exclusive binding semantics")
    def test_reuseaddr_socket_cannot_take_the_exclusive_port(self):
        with server() as first, socket.socket(socket.AF_INET, socket.SOCK_STREAM) as duplicate:
            self.assertEqual(first.socket.getsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE), 1)
            self.assertEqual(first.socket.getsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR), 0)
            duplicate.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            with self.assertRaises(OSError):
                duplicate.bind(first.server_address)


if __name__ == "__main__":
    unittest.main()
