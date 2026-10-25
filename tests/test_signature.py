"""validate.py checks dist/v1/manifest.json.sig against keys/ed25519.json (pure-Python Ed25519, stdlib only)."""

import base64
import hashlib
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from helpers import ROOT, load_script, validate

ed25519 = load_script("ed25519")
SECRET = bytes.fromhex("9d61b19deffd5a60ba844af492ec2cc44449c5697b326919703bac031cae7f60")  # RFC 8032 test 1


class Ed25519(unittest.TestCase):
    def test_rfc8032_test_1(self):
        pub = ed25519.public_key(SECRET)
        self.assertEqual(pub.hex(), "d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a")
        sig = ed25519.sign(SECRET, b"")
        self.assertEqual(sig.hex(), "e5564300c360ac729086e2cc806e828a84877f1eb8e5d974d873e065224901555fb8821590a33bacc61e39701cf9b46bd25bf5f0595bbe24655141438e7a100b")
        self.assertTrue(ed25519.verify(pub, b"", sig))

    def test_rejects(self):
        pub = ed25519.public_key(SECRET)
        sig = ed25519.sign(SECRET, b"manifest")
        self.assertFalse(ed25519.verify(pub, b"manifest ", sig))
        self.assertFalse(ed25519.verify(pub, b"manifest", sig[:-1] + bytes([sig[-1] ^ 1])))
        self.assertFalse(ed25519.verify(ed25519.public_key(b"\x01" * 32), b"manifest", sig))
        self.assertFalse(ed25519.verify(pub, b"manifest", sig[:63]))
        # s >= q is malleable and refused
        s = int.from_bytes(sig[32:], "little") + ed25519.q
        self.assertFalse(ed25519.verify(pub, b"manifest", sig[:32] + s.to_bytes(32, "little")))


class SignatureCheck(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        for d in ("source", "dist", "schema"):
            shutil.copytree(ROOT / d, self.tmp / d)
        self.pub = ed25519.public_key(SECRET)

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def write_key(self, **over):
        (self.tmp / "keys").mkdir(exist_ok=True)
        key = {"algorithm": "Ed25519", "keyId": hashlib.sha256(self.pub).hexdigest()[:16],
               "publicKey": base64.b64encode(self.pub).decode(), **over}
        (self.tmp / "keys/ed25519.json").write_text(json.dumps(key))

    def sign(self, data=None):
        data = data if data is not None else (self.tmp / "dist/v1/manifest.json").read_bytes()
        (self.tmp / "dist/v1/manifest.json.sig").write_text(base64.b64encode(ed25519.sign(SECRET, data)).decode() + "\n")

    def test_before_first_signed_release_only_warns(self):
        errors, warnings = validate.check_signature(self.tmp)
        self.assertEqual(errors, [])
        self.assertIn("not checked", warnings[0])
        errors, warnings = validate.validate(self.tmp)
        self.assertEqual(errors, [])

    def test_valid_signature(self):
        self.write_key()
        self.sign()
        self.assertEqual(validate.check_signature(self.tmp), ([], []))
        self.assertEqual(validate.validate(self.tmp)[0], [])

    def test_manifest_changed_after_signing(self):
        self.write_key()
        self.sign()
        m = self.tmp / "dist/v1/manifest.json"
        m.write_bytes(m.read_bytes() + b" ")
        errors, _ = validate.check_signature(self.tmp)
        self.assertTrue(any("doesn't verify" in e for e in errors))

    def test_signed_by_another_key(self):
        other = ed25519.public_key(b"\x02" * 32)
        self.pub = other
        self.write_key()
        self.pub = ed25519.public_key(SECRET)
        self.sign()
        self.assertTrue(validate.check_signature(self.tmp)[0])

    def test_key_without_signature_fails(self):
        self.write_key()
        self.assertTrue(any("missing" in e for e in validate.check_signature(self.tmp)[0]))

    def test_signature_without_key_fails(self):
        self.sign()
        self.assertTrue(any("doesn't" in e for e in validate.check_signature(self.tmp)[0]))

    def test_wrong_key_id(self):
        self.write_key(keyId="0000000000000000")
        self.sign()
        self.assertTrue(any("keyId" in e for e in validate.check_signature(self.tmp)[0]))

    def test_garbage_signature(self):
        self.write_key()
        (self.tmp / "dist/v1/manifest.json.sig").write_text("not base64!!\n")
        self.assertTrue(any("base64" in e for e in validate.check_signature(self.tmp)[0]))


if __name__ == "__main__":
    unittest.main()
