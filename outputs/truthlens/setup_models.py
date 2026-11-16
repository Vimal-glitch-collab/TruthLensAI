"""Install both pinned detectors, preserving existing files until verification passes."""
import argparse
import hashlib
import importlib.util
import json
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def file_hash(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def download(url, target, expected_hash):
    for attempt in range(3):
        try:
            return download_once(url, target, expected_hash)
        except (TimeoutError, ConnectionError, urllib.error.URLError):
            if attempt == 2:
                raise
            print('Download interrupted; retrying...', flush=True)
            time.sleep(1)


def download_once(url, target, expected_hash):
    digest = hashlib.sha256()
    total = 0
    with urllib.request.urlopen(url, timeout=60) as source, target.open('wb') as destination:
        while block := source.read(1024 * 1024):
            total += len(block)
            if total > 150 * 1024 * 1024:
                raise ValueError('Model exceeds download limit')
            digest.update(block)
            destination.write(block)
            if total % (10 * 1024 * 1024) == 0:
                print(f'Downloaded {total // (1024 * 1024)} MB', flush=True)
    if digest.hexdigest() != expected_hash:
        raise ValueError('Model checksum mismatch; existing model was preserved')


def install(kind):
    config = json.loads((ROOT / 'models' / ('image-detector.json' if kind == 'image' else 'onnx.json')).read_text(encoding='utf-8'))
    target = ROOT / 'models' / config['model_path']
    if target.is_file() and file_hash(target) == config['sha256']:
        print(f'{kind.capitalize()} detector already verified.', flush=True)
        return
    if kind == 'image' and importlib.util.find_spec('onnx') is None:
        raise RuntimeError('Image export requires the build dependency: python -m pip install -r requirements-models.txt')
    with tempfile.TemporaryDirectory(prefix='model-setup-', dir=target.parent) as directory:
        staging = Path(directory)
        output = staging / target.name
        base = f"{config['source']}/resolve/{config['revision']}"
        print(f'Installing pinned {kind} detector...', flush=True)
        if kind == 'image':
            checkpoint = staging / 'model.safetensors'
            download(base + '/model.safetensors', checkpoint, config['checkpoint_sha256'])
            subprocess.run([sys.executable, str(ROOT / 'tools/export_community_model.py'),
                            str(checkpoint), '--output', str(output)], check=True)
        else:
            download(base + '/onnx/model_quantized.onnx', output, config['sha256'])
        if file_hash(output) != config['sha256']:
            raise ValueError('Exported model checksum mismatch; existing model was preserved')
        output.replace(target)
    print(f'{kind.capitalize()} detector installed and verified.', flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--kind', choices=('all', 'image', 'video'), default='all')
    args = parser.parse_args()
    for kind in ('image', 'video') if args.kind == 'all' else (args.kind,):
        install(kind)


if __name__ == '__main__':
    main()
