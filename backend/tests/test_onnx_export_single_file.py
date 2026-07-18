"""Tests for Windows-safe single-file ONNX export helper."""

from __future__ import annotations

import os
import tempfile
import unittest
from unittest.mock import MagicMock, patch


class TestExportOnnxSingleFile(unittest.TestCase):
    def test_exports_without_sidecar_and_invalidates(self):
        from app.services.bots.ml_model_artifacts import export_onnx_single_file

        invalidate = MagicMock()

        with tempfile.TemporaryDirectory() as td:
            dest = os.path.join(td, "model.onnx")

            def fake_export(model, args, path, **kwargs):
                # Simulate single-file export (no .data sidecar).
                with open(path, "wb") as f:
                    f.write(b"onnx-bytes")

            with patch("torch.onnx.export", side_effect=fake_export):
                out = export_onnx_single_file(
                    MagicMock(),
                    MagicMock(),
                    dest,
                    input_names=["input"],
                    output_names=["out"],
                    invalidate=invalidate,
                )

            self.assertEqual(out, os.path.abspath(dest))
            self.assertTrue(os.path.isfile(dest))
            self.assertFalse(os.path.isfile(dest + ".data"))
            invalidate.assert_called_once()

    def test_consolidates_external_data_sidecar(self):
        from app.services.bots.ml_model_artifacts import export_onnx_single_file

        with tempfile.TemporaryDirectory() as td:
            dest = os.path.join(td, "model.onnx")

            def fake_export(model, args, path, **kwargs):
                with open(path, "wb") as f:
                    f.write(b"proto")
                with open(path + ".data", "wb") as f:
                    f.write(b"weights")

            fake_onnx = MagicMock()
            fake_onnx.load.return_value = MagicMock()

            with patch("torch.onnx.export", side_effect=fake_export), patch.dict(
                "sys.modules", {"onnx": fake_onnx}
            ):
                # Re-import path uses `import onnx` inside the helper.
                out = export_onnx_single_file(
                    MagicMock(),
                    MagicMock(),
                    dest,
                    input_names=["input"],
                    output_names=["out"],
                )

            self.assertEqual(out, os.path.abspath(dest))
            fake_onnx.load.assert_called()
            fake_onnx.save_model.assert_called()
            self.assertFalse(os.path.isfile(dest + ".data"))


if __name__ == "__main__":
    unittest.main()
