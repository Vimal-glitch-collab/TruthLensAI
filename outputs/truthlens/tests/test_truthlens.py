import concurrent.futures
import csv
import hashlib
import http.client
import io
import json
import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
from PIL import Image

import server
from train import train
from truthlens.analysis import AnalysisError, FEATURE_NAMES, image_report, news_report, onnx_prediction, video_report


def image_bytes(seed=1):
    data = np.random.default_rng(seed).integers(0, 255, (40, 60, 3), dtype=np.uint8)
    buffer = io.BytesIO()
    Image.fromarray(data).save(buffer, format='PNG')
    return buffer.getvalue()


class AnalysisTests(unittest.TestCase):
    def test_image_observations_not_fake_verdict(self):
        report = image_report(image_bytes(), 'test.png')
        self.assertEqual(report['verdict'], 'Inconclusive')
        self.assertEqual(report['metrics']['width'], 60)
        self.assertEqual(len(report['sha256']), 64)
        self.assertIsNone(report['model'])
        json.dumps(report, allow_nan=False)

    def test_invalid_image(self):
        with self.assertRaises(AnalysisError):
            image_report(b'not an image', 'test.png')

    def test_news_does_not_verify_claims(self):
        text = 'SHOCKING! This miracle is guaranteed to work according to an unnamed expert. https://example.org'
        report = news_report(text, 'News')
        self.assertIn('miracle', report['metrics']['sensational_phrases'])
        self.assertEqual(report['metrics']['links_in_text'], 1)
        self.assertEqual(report['verdict'], 'Inconclusive')
        with self.assertRaises(AnalysisError):
            news_report('too short', 'News')

    def test_training_roundtrip_and_duplicate_rejection(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest, model = root / 'dataset.csv', root / 'model.json'
            with manifest.open('w', newline='', encoding='utf-8') as handle:
                writer = csv.writer(handle); writer.writerow(['path', 'label'])
                for index in range(24):
                    filename = f'{index}.png'; (root / filename).write_bytes(image_bytes(index))
                    writer.writerow([filename, index % 2])
            evaluation = train(manifest, model, epochs=20)
            self.assertEqual(evaluation['train_count'] + evaluation['test_count'], 24)
            with patch.dict(os.environ, {'TRUTHLENS_FEATURE_MODEL': str(model)}):
                report = image_report(image_bytes(80), 'new.png')
            self.assertTrue(0 <= report['model']['manipulated_probability'] <= 1)
            with manifest.open('a', encoding='utf-8') as handle:
                handle.write('0.png,0\n')
            with self.assertRaisesRegex(ValueError, 'Duplicate'):
                train(manifest, model, epochs=1)

    def test_invalid_feature_model(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'model.json'
            path.write_text(json.dumps(dict(features=FEATURE_NAMES, mean=[0]*5, scale=[0]*5, weights=[1]*5, bias=0)))
            with patch.dict(os.environ, {'TRUTHLENS_FEATURE_MODEL': str(path)}), self.assertRaises(AnalysisError):
                image_report(image_bytes(), 'test.png')

    def test_video_and_corrupt_video(self):
        import cv2
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'test.avi'
            writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*'MJPG'), 12, (64, 48))
            self.assertTrue(writer.isOpened())
            for i in range(36):
                writer.write(np.full((48, 64, 3), i * 5, dtype=np.uint8))
            writer.release()
            report = video_report(path, 'test.avi', hashlib.sha256(path.read_bytes()).hexdigest())
            self.assertEqual(report['metrics']['frames_sampled'], 24)
            self.assertEqual(report['verdict'], 'Inconclusive')
            self.assertAlmostEqual(report['metrics']['duration_seconds'], 3)
            path.write_bytes(b'corrupt')
            with self.assertRaises(AnalysisError):
                video_report(path, 'test.avi', '')

    def test_onnx_preprocessing_and_probability_contract(self):
        class Session:
            def get_inputs(self):
                return [type('Input', (), {'name':'image'})()]
            def run(self, outputs, feed):
                tensor = feed['image']
                self_shape = tensor.shape
                if self_shape != (1, 3, 224, 224) or tensor.dtype != np.float32:
                    raise AssertionError('Wrong ONNX preprocessing')
                return [np.array([[0., 2.]])]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'model.onnx').write_bytes(b'fixture')
            config = root / 'model.json'
            config.write_text(json.dumps({'model_path':'model.onnx','activation':'softmax','manipulated_index':1}))
            with patch.dict(os.environ, {'TRUTHLENS_ONNX_CONFIG':str(config)}), patch('truthlens.analysis.onnx_session', return_value=Session()):
                prediction = onnx_prediction(Image.new('RGB',(50,60)))
                self.assertAlmostEqual(prediction['manipulated_probability'], .8808, places=4)
                config.write_text(json.dumps({'model_path':'model.onnx','activation':'wrong'}))
                with self.assertRaises(AnalysisError):
                    onnx_prediction(Image.new('RGB',(50,60)))


class APITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.old_data, cls.old_pool = server.DATA, server.POOL
        server.DATA = Path(cls.tmp.name)
        server.POOL = concurrent.futures.ThreadPoolExecutor(max_workers=2)
        cls.httpd = server.ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown(); cls.httpd.server_close(); cls.thread.join()
        server.POOL.shutdown(wait=True)
        server.DATA, server.POOL = cls.old_data, cls.old_pool
        cls.tmp.cleanup()

    def request(self, method, path, body=None, headers=None):
        connection = http.client.HTTPConnection('127.0.0.1', self.httpd.server_port, timeout=5)
        connection.request(method, path, body=body, headers=headers or {})
        response = connection.getresponse()
        raw = response.read(); status = response.status
        connection.close()
        return status, json.loads(raw)

    def wait_report(self, job_id):
        for _ in range(100):
            status, payload = self.request('GET', f'/api/reports/{job_id}')
            if payload['status'] in ('complete','failed'):
                return payload
            time.sleep(.03)
        self.fail('Job did not finish')

    def test_image_job_history_and_delete(self):
        status, payload = self.request('POST','/api/analyze/image?name=test.png',image_bytes(),{'Content-Type':'application/octet-stream'})
        self.assertEqual(status,202)
        job_id = payload['id']; result = self.wait_report(job_id)
        self.assertEqual(result['status'],'complete')
        self.assertEqual(result['report']['name'],'test.png')
        status, exported = self.request('GET', f'/api/reports/{job_id}/download')
        self.assertEqual(status, 200)
        self.assertEqual(exported, result['report'])
        status, reports = self.request('GET','/api/reports')
        self.assertTrue(any(report['id'] == job_id for report in reports))
        self.assertEqual(self.request('DELETE',f'/api/reports/{job_id}')[0],403)
        self.assertEqual(self.request('DELETE',f'/api/reports/{job_id}',headers={'X-TruthLens-Action':'delete'})[0],200)
        self.assertEqual(self.request('GET',f'/api/reports/{job_id}')[0],404)

    def test_news_job_and_invalid_body(self):
        status, payload = self.request('POST','/api/analyze/news',json.dumps({'text':'A detailed article according to researchers with claims requiring verification.'}),{'Content-Type':'application/json'})
        self.assertEqual(status,202)
        self.assertEqual(self.wait_report(payload['id'])['status'],'complete')
        self.assertEqual(self.request('POST','/api/analyze/news',b'{broken',{'Content-Type':'application/json'})[0],400)
        self.assertEqual(self.request('POST','/api/analyze/news',b'{"text":42}',{'Content-Type':'application/json'})[0],400)

    def test_corrupt_file_job(self):
        status, payload = self.request('POST','/api/analyze/image',b'corrupt',{'Content-Type':'application/octet-stream'})
        self.assertEqual(status,202)
        self.assertEqual(self.wait_report(payload['id'])['status'],'failed')

    def test_cross_origin_host_and_content_type(self):
        self.assertEqual(self.request('GET','/api/health',headers={'Origin':'https://untrusted.example'})[0],403)
        self.assertEqual(self.request('GET','/api/health',headers={'Host':'untrusted.example'})[0],403)
        self.assertEqual(self.request('POST','/api/analyze/news',b'text',{'Content-Type':'text/plain'})[0],415)
        self.assertEqual(self.request('GET','/api/reports/missing')[0],404)
        self.assertEqual(self.request('GET','/../server.py')[0],404)


if __name__ == '__main__':
    unittest.main()
