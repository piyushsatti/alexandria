import json
import tarfile
import tempfile
import unittest
from pathlib import Path

from tools.create_offline_bundle import create_bundle


class OfflineBundleTests(unittest.TestCase):
    def test_bundle_contains_release_inputs_and_stdio_config(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            image = root / "image.tar"
            image.write_bytes(b"opaque image archive")
            data = root / "data"
            (data / "builds" / "generation").mkdir(parents=True)
            (data / "builds" / "generation" / "passages.lance").write_text("db")
            (data / "models").mkdir()
            (data / "models" / "model.bin").write_text("model")
            (data / "active.json").write_text(
                json.dumps(
                    {
                        "database": "builds/generation",
                        "revision": "knowledge-revision",
                        "embedding_model": "test/model",
                        "documents": 1,
                        "passages": 1,
                    }
                )
            )
            (data / "release-data-receipt.json").write_text(
                json.dumps({"indexed_revision": "knowledge-revision"})
            )
            output = root / "bundle.tar.gz"
            manifest = create_bundle(
                image,
                data,
                output,
                product_revision="product-revision",
                release="2026.09.21",
                image_ref="alexandria@sha256:abc",
            )
            self.assertFalse(manifest["network_required"])
            with tarfile.open(output, "r:gz") as archive:
                names = set(archive.getnames())
                self.assertIn("alexandria-image.tar", names)
                self.assertIn("data/active.json", names)
                self.assertIn("release-manifest.json", names)
                config = json.loads(archive.extractfile("stdio-config.json").read())
            self.assertEqual(
                config["mcpServers"]["alexandria"]["args"][-2:],
                ["--data", "/data"],
            )

    def test_rejects_existing_output(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            image = root / "image.tar"
            image.write_bytes(b"image")
            data = root / "data"
            data.mkdir()
            (data / "active.json").write_text(
                json.dumps(
                    {
                        "revision": "r",
                        "embedding_model": "m",
                        "documents": 0,
                        "passages": 0,
                    }
                )
            )
            (data / "release-data-receipt.json").write_text(
                json.dumps({"indexed_revision": "r"})
            )
            output = root / "bundle.tar.gz"
            output.write_text("existing")
            with self.assertRaisesRegex(ValueError, "already exists"):
                create_bundle(
                    image,
                    data,
                    output,
                    product_revision="p",
                    release="2026.09.21",
                    image_ref="image",
                )


if __name__ == "__main__":
    unittest.main()
