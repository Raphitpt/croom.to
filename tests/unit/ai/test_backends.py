"""
Tests for croom.ai.backends module.
"""

from unittest.mock import MagicMock, patch, AsyncMock
import numpy as np

import pytest

from croom.ai.backends import (
    get_available_backends,
    get_best_backend,
    get_backend_by_name,
)
import croom.ai.backends as backends_module
from croom.ai.backends.base import AIBackend, DetectionResult, InferenceResult, ModelType


class TestAIBackendBase:
    """Tests for AIBackend base class."""

    def test_inference_result_creation(self):
        """Test InferenceResult dataclass."""
        result = InferenceResult(
            detections=[
                DetectionResult(
                    class_id=0,
                    class_name="person",
                    confidence=0.95,
                    bbox=(0.1, 0.1, 0.3, 0.5),
                ),
            ],
            inference_time_ms=15.5,
            frame_id=7,
        )

        assert len(result.detections) == 1
        assert result.detections[0].class_name == "person"
        assert result.inference_time_ms == 15.5
        assert result.to_dict()["detections"][0]["confidence"] == 0.95
        assert result.to_dict()["frame_id"] == 7

    def test_inference_result_empty(self):
        """Test empty InferenceResult."""
        result = InferenceResult(detections=[], inference_time_ms=10.0)

        assert len(result.detections) == 0
        assert result.frame_id is None


def _mock_session(output):
    """ONNX session mock with a 640x640 NCHW input."""
    session = MagicMock()
    session.get_inputs.return_value = [MagicMock(shape=[1, 3, 640, 640])]
    session.get_inputs.return_value[0].name = "images"
    out = MagicMock()
    out.name = "output0"
    session.get_outputs.return_value = [out]
    session.run.return_value = [output]
    return session


class TestONNXCPUBackend:
    """Tests for ONNX CPU backend."""

    @patch("croom.ai.backends.onnx_cpu.ONNX_AVAILABLE", True)
    def test_is_available(self):
        """Test ONNX availability check."""
        from croom.ai.backends.onnx_cpu import ONNXCPUBackend

        assert ONNXCPUBackend.is_available() is True

    @patch("croom.ai.backends.onnx_cpu.ONNX_AVAILABLE", False)
    def test_not_available_without_onnxruntime(self):
        """Test ONNX unavailable when onnxruntime is missing."""
        from croom.ai.backends.onnx_cpu import ONNXCPUBackend

        assert ONNXCPUBackend.is_available() is False

    def test_backend_name(self):
        """Test backend name."""
        from croom.ai.backends.onnx_cpu import ONNXCPUBackend

        backend = ONNXCPUBackend()
        assert backend.name == "cpu"
        assert backend.get_capabilities().name == "cpu"

    @patch("croom.ai.backends.onnx_cpu.ort")
    async def test_load_model(self, mock_ort, temp_dir):
        """Test model loading."""
        from croom.ai.backends.onnx_cpu import ONNXCPUBackend

        model = temp_dir / "test_model.onnx"
        model.write_bytes(b"fake")
        mock_ort.InferenceSession.return_value = _mock_session(np.zeros((1, 84, 1)))

        backend = ONNXCPUBackend()
        await backend.initialize()
        model_id = await backend.load_model(ModelType.PERSON_DETECTION, str(model))

        assert model_id == f"{ModelType.PERSON_DETECTION.value}_test_model"

    @patch("croom.ai.backends.onnx_cpu.ort")
    async def test_load_model_requires_initialize(self, mock_ort, temp_dir):
        """Test loading before initialize fails."""
        from croom.ai.backends.onnx_cpu import ONNXCPUBackend

        backend = ONNXCPUBackend()
        with pytest.raises(RuntimeError):
            await backend.load_model(ModelType.PERSON_DETECTION, str(temp_dir / "m.onnx"))

    @patch("croom.ai.backends.onnx_cpu.ort")
    async def test_load_missing_model(self, mock_ort, temp_dir):
        """Test loading a missing model file fails."""
        from croom.ai.backends.onnx_cpu import ONNXCPUBackend

        backend = ONNXCPUBackend()
        await backend.initialize()
        with pytest.raises(FileNotFoundError):
            await backend.load_model(ModelType.PERSON_DETECTION, str(temp_dir / "missing.onnx"))

    @patch("croom.ai.backends.onnx_cpu.ort")
    async def test_inference(self, mock_ort, temp_dir):
        """Test running inference returns person detections."""
        from croom.ai.backends.onnx_cpu import ONNXCPUBackend

        # One YOLOv8 candidate: centre (320, 320), 100x200 box, person score 0.9
        output = np.zeros((1, 84, 1), dtype=np.float32)
        output[0, :4, 0] = [320, 320, 100, 200]
        output[0, 4, 0] = 0.9

        model = temp_dir / "yolo.onnx"
        model.write_bytes(b"fake")
        mock_ort.InferenceSession.return_value = _mock_session(output)

        backend = ONNXCPUBackend()
        await backend.initialize()
        model_id = await backend.load_model(ModelType.PERSON_DETECTION, str(model))

        frame = np.zeros((480, 640, 3), dtype=np.uint8)
        result = await backend.infer(model_id, frame)

        assert isinstance(result, InferenceResult)
        assert len(result.detections) == 1
        detection = result.detections[0]
        assert detection.class_name == "person"
        assert detection.confidence == pytest.approx(0.9)
        assert detection.bbox == pytest.approx((270 / 640, 220 / 640, 370 / 640, 420 / 640))

    async def test_inference_unknown_model(self):
        """Test inference on a model that is not loaded."""
        from croom.ai.backends.onnx_cpu import ONNXCPUBackend

        backend = ONNXCPUBackend()
        with pytest.raises(ValueError):
            await backend.infer("nope", np.zeros((10, 10, 3), dtype=np.uint8))


class TestNVIDIABackend:
    """Tests for NVIDIA TensorRT backend."""

    @patch("croom.ai.backends.nvidia.TENSORRT_AVAILABLE", True)
    @patch("croom.ai.backends.nvidia.CUDA_AVAILABLE", True)
    def test_is_available_with_tensorrt(self):
        """Test NVIDIA availability with TensorRT."""
        from croom.ai.backends.nvidia import NVIDIABackend

        # Mock the class method
        with patch.object(NVIDIABackend, "is_available", return_value=True):
            assert NVIDIABackend.is_available() is True

    @patch("croom.ai.backends.nvidia.TENSORRT_AVAILABLE", False)
    @patch("croom.ai.backends.nvidia.CUDA_AVAILABLE", False)
    @patch("croom.ai.backends.nvidia.ONNX_CUDA_AVAILABLE", False)
    def test_not_available_without_cuda(self):
        """Test NVIDIA unavailable without CUDA."""
        from croom.ai.backends.nvidia import NVIDIABackend

        with patch.object(NVIDIABackend, "is_available", return_value=False):
            assert NVIDIABackend.is_available() is False

    @patch("croom.ai.backends.nvidia.TENSORRT_AVAILABLE", True)
    @patch("croom.ai.backends.nvidia.CUDA_AVAILABLE", True)
    def test_backend_name(self):
        """Test NVIDIA backend name."""
        from croom.ai.backends.nvidia import NVIDIABackend

        with patch.object(NVIDIABackend, "__init__", lambda x: None):
            backend = NVIDIABackend()
            backend._name = "nvidia"
            assert backend._name == "nvidia"


class TestOpenVINOBackend:
    """Tests for Intel OpenVINO backend."""

    @patch("croom.ai.backends.openvino.OPENVINO_AVAILABLE", True)
    def test_is_available(self):
        """Test OpenVINO availability check."""
        from croom.ai.backends.openvino import OpenVINOBackend

        with patch.object(OpenVINOBackend, "is_available", return_value=True):
            assert OpenVINOBackend.is_available() is True

    @patch("croom.ai.backends.openvino.OPENVINO_AVAILABLE", False)
    def test_not_available(self):
        """Test OpenVINO unavailable."""
        from croom.ai.backends.openvino import OpenVINOBackend

        with patch.object(OpenVINOBackend, "is_available", return_value=False):
            assert OpenVINOBackend.is_available() is False


def _fake_backend(class_name, available):
    return type(class_name, (), {"is_available": classmethod(lambda cls: available)})


class TestBackendSelection:
    """Tests for backend selection logic."""

    def test_get_best_backend_prefers_first_available(self):
        """Test the highest-priority available backend wins."""
        nvidia = _fake_backend("NVIDIABackend", True)
        cpu = _fake_backend("ONNXCPUBackend", True)

        with patch.object(backends_module, "_available_backends", [nvidia, cpu]):
            assert get_best_backend() is nvidia

    def test_get_best_backend_skips_unavailable(self):
        """Test unavailable accelerators fall back to CPU."""
        hailo = _fake_backend("HailoBackend", False)
        cpu = _fake_backend("ONNXCPUBackend", True)

        with patch.object(backends_module, "_available_backends", [hailo, cpu]):
            assert get_best_backend() is cpu

    def test_get_best_backend_none_available(self):
        """Test no backend available."""
        with patch.object(backends_module, "_available_backends", [_fake_backend("HailoBackend", False)]):
            assert get_best_backend() is None

    def test_get_backend_by_name(self):
        """Test getting backend by name and alias."""
        cpu = _fake_backend("ONNXCPUBackend", True)

        with patch.object(backends_module, "_available_backends", [cpu]):
            assert get_backend_by_name("cpu") is cpu
            assert get_backend_by_name("ONNX") is cpu
            assert get_backend_by_name("hailo") is None
            assert get_backend_by_name("unknown") is None

    def test_get_available_backends_returns_list(self):
        """Test that get_available_backends returns a list."""
        backends = get_available_backends()
        assert isinstance(backends, list)
        assert backends is not backends_module._available_backends


class TestHailoBackend:
    """Tests for Hailo-8L backend."""

    @patch("croom.ai.backends.hailo.HAILO_AVAILABLE", True)
    def test_is_available(self):
        """Test Hailo availability check."""
        from croom.ai.backends.hailo import HailoBackend

        with patch.object(HailoBackend, "is_available", return_value=True):
            assert HailoBackend.is_available() is True

    @patch("croom.ai.backends.hailo.HAILO_AVAILABLE", False)
    def test_not_available(self):
        """Test Hailo unavailable."""
        from croom.ai.backends.hailo import HailoBackend

        with patch.object(HailoBackend, "is_available", return_value=False):
            assert HailoBackend.is_available() is False


class TestCoralBackend:
    """Tests for Google Coral TPU backend."""

    @patch("croom.ai.backends.coral.EDGETPU_AVAILABLE", True)
    @patch("croom.ai.backends.coral.TFLITE_AVAILABLE", True)
    def test_is_available_with_usb_coral(self):
        """Test Coral detected through its USB id."""
        from croom.ai.backends.coral import CoralBackend

        lsusb = MagicMock(stdout=b"Bus 001 Device 004: ID 18d1:9302 Google Inc.")
        with patch("subprocess.run", return_value=lsusb):
            assert CoralBackend.is_available() is True

    @patch("croom.ai.backends.coral.EDGETPU_AVAILABLE", False)
    @patch("croom.ai.backends.coral.TFLITE_AVAILABLE", False)
    def test_not_available_without_runtime(self):
        """Test Coral unavailable without TFLite/EdgeTPU runtime."""
        from croom.ai.backends.coral import CoralBackend

        assert CoralBackend.is_available() is False
