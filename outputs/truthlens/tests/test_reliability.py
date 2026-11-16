import hashlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
from PIL import Image

import setup_models
from evaluate import metrics
from truthlens import analysis, validation


class ReliabilityTests(unittest.TestCase):
    def test_cloud_proxy_accepts_only_explicit_hosts_and_origins(self):
        import server
        from types import SimpleNamespace
        handler = server.Handler.__new__(server.Handler)
        handler.server = SimpleNamespace(server_port=10000)
        env = {'TRUTHLENS_PUBLIC_ORIGIN':'https://truthlens.example',
               'RENDER_EXTERNAL_URL':'https://truthlens-api.onrender.com'}
        with patch.dict(os.environ, env, clear=True):
            for host in ('truthlens-api.onrender.com', 'truthlens.example'):
                handler.headers = {'Host':host, 'Origin':'https://truthlens.example'}
                self.assertTrue(handler.trusted_request())
            handler.headers = {'Host':'truthlens-api.onrender.com','Origin':'https://evil.example'}
            self.assertFalse(handler.trusted_request())
            handler.headers = {'Host':'evil.example','Origin':'https://truthlens.example'}
            self.assertFalse(handler.trusted_request())
            self.assertIn('; Secure', handler.session_cookie('fixture'))
        with patch.dict(os.environ, {'RENDER_EXTERNAL_URL':'https://truthlens-api.onrender.com'}, clear=True):
            self.assertIn('; Secure', handler.session_cookie('fixture'))

    def test_missing_model_is_not_ready_and_has_actionable_error(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / 'model.json'
            config.write_text(json.dumps({'model_path': 'missing.onnx'}))
            with patch.dict(os.environ, {'TRUTHLENS_ONNX_CONFIG': str(config)}):
                status = analysis.detector_status()
                self.assertFalse(status['available'])
                self.assertIn('setup', status['message'])
                with self.assertRaisesRegex(analysis.AnalysisError, 'weights are missing'):
                    analysis.detector_prediction(Image.new('RGB', (256, 256)))

    def test_malformed_model_configuration_is_reported(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / 'model.json'
            for value in ('[]', 'null', '{', '{}'):
                config.write_text(value)
                with patch.dict(os.environ, {'TRUTHLENS_ONNX_CONFIG': str(config)}):
                    self.assertFalse(analysis.detector_status()['available'])
                    with self.assertRaises(analysis.AnalysisError):
                        analysis.detector_prediction(Image.new('RGB', (256, 256)))

    def test_readiness_checks_inference_and_invalidates_when_model_changes(self):
        class Session:
            def get_inputs(self):
                return [type('Input', (), {'name': 'image'})()]
            def run(self, outputs, feed):
                return [np.array([[0., 1.]])]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            model = root / 'model.onnx'
            model.write_bytes(b'fixture')
            config = root / 'model.json'
            config.write_text(json.dumps({'model_path': model.name, 'sha256': hashlib.sha256(b'fixture').hexdigest()}))
            with patch.dict(os.environ, {'TRUTHLENS_ONNX_CONFIG': str(config)}), patch.object(analysis, 'onnx_session', return_value=Session()):
                self.assertTrue(analysis.detector_status()['available'])
                model.write_bytes(b'changed model')
                self.assertFalse(analysis.detector_status()['available'])

    def test_nonfinite_feature_bias_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / 'feature.json'
            model = dict(features=analysis.FEATURE_NAMES, mean=[0]*5, scale=[1]*5, weights=[1]*5)
            for bias in (float('nan'), float('inf'), -float('inf')):
                config.write_text(json.dumps({**model, 'bias': bias}))
                with patch.dict(os.environ, {'TRUTHLENS_FEATURE_MODEL': str(config)}):
                    with self.assertRaises(analysis.AnalysisError):
                        analysis.feature_prediction(Image.new('RGB', (32, 32)))

    def test_invalid_evaluation_probabilities_are_rejected(self):
        for values in ([.1], [.1, float('nan')], [.1, float('inf')], [-.1, .9], [.1, 1.1], [[.1, .9]]):
            with self.assertRaises(ValueError):
                metrics([0, 1], values)

    def test_non_object_benchmark_does_not_crash(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for value in ('[]', 'null', '42'):
                (root / 'evaluation.json').write_text(value)
                with patch.object(validation, 'model_hash', return_value='a'*64):
                    self.assertEqual(validation.status(root)['status'], 'invalid_benchmark')

    def test_model_install_preserves_existing_file_on_bad_download(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'models').mkdir()
            target = root / 'models' / 'detector.onnx'
            target.write_bytes(b'existing')
            (root / 'models' / 'onnx.json').write_text(json.dumps({
                'model_path': target.name, 'sha256': hashlib.sha256(b'expected').hexdigest(),
                'source': 'https://example.org/model', 'revision': 'pinned'}))
            with patch.object(setup_models, 'ROOT', root), patch('setup_models.urllib.request.urlopen', return_value=io.BytesIO(b'bad download')):
                with self.assertRaisesRegex(ValueError, 'checksum'):
                    setup_models.install('video')
            self.assertEqual(target.read_bytes(), b'existing')
            self.assertFalse(list((root / 'models').glob('model-setup-*')))

    def test_verified_model_install_does_not_download_again(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'models').mkdir()
            (root / 'models' / 'detector.onnx').write_bytes(b'verified')
            (root / 'models' / 'onnx.json').write_text(json.dumps({
                'model_path': 'detector.onnx', 'sha256': hashlib.sha256(b'verified').hexdigest()}))
            with patch.object(setup_models, 'ROOT', root), patch('setup_models.urllib.request.urlopen') as download:
                setup_models.install('video')
                download.assert_not_called()
