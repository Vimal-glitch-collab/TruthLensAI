# TruthLens AI

A complete local investigation application with a responsive web interface, a Python HTTP API, image and video forensics, article-language review, SQLite report history, JSON export, and browser PDF printing. No cloud account or frontend build tool is required.

**Detection status:** The runnable default is forensic triage, not a validated deepfake or misinformation detector. No trained weights, benchmark accuracy, or factual truth verdicts are fabricated. Machine learning is available through the included training pipeline; deep learning is available through a compatible ONNX model supplied by you. News review is explicitly rule-based. Video sampling does not perform facial identity, lip-sync, or audio classification.

## Start

Use Python 3.12 or 3.13. From this folder:

```powershell
py -3.12 -m venv .venv
.venv/Scripts/python.exe -m pip install -r requirements.txt
.venv/Scripts/python.exe server.py
```

Open http://127.0.0.1:8000. On Windows, `start.ps1` starts an already configured environment. On macOS/Linux, use `python3 -m venv .venv`, `.venv/bin/pip install -r requirements.txt`, and `.venv/bin/python server.py`.

The app binds only to localhost. `PORT` selects another port. It is intended for personal local use; it is not an authenticated, multi-user hosted service. No public deployment is included.

## Use

1. Select Image, Video, or News article.
2. Upload a JPEG/PNG/WebP image or MP4/MOV/WebM/AVI video; alternatively paste article text. The sample article demonstrates language cues and is clearly fictional.
3. Analyze content, inspect observations and limitations, and export JSON or Print / PDF.
4. Reopen or delete investigations in Report history.

Images are limited to 50 MB and 20 megapixels. Videos are limited to 50 MB, two minutes, and approximately 4K resolution. Video codec support depends on OpenCV’s bundled decoder. News text is limited to 50,000 characters. Article URLs are references only and are never fetched. At most two analyses run simultaneously, with four total admitted jobs. Jobs persist their state across browser reloads; interrupted jobs are marked failed after a server restart.

## Actual analysis

- Images: decode and validate the file, read limited EXIF metadata, compute JPEG recompression residuals and Laplacian detail measurements, and optionally run a trained image model.
- Videos: decode up to 24 uniformly spaced frames; measure sampled visual changes and detail variation. When an image model is configured, apply it to sampled whole frames and report their mean output. This is not a temporal deepfake detector. Short events can be missed.
- Articles: inspect a small English sensational-language lexicon, attribution expressions, literal URLs, uppercase words, and exclamation marks. No external factual verification is performed.

All default reports say **Inconclusive**. Model outputs are labeled manipulated-class probabilities, with domain and calibration limitations; they do not replace a human investigation.

## Train the machine-learning baseline

Create a CSV with `path,label`, relative image paths, and ground-truth labels: 0 authentic, 1 manipulated. Supply at least 20 unique files and at least eight per class. A real system needs a much larger, representative, licensed dataset with source-separated evaluation.

```csv
path,label
dataset/authentic/one.jpg,0
dataset/manipulated/two.jpg,1
```

```powershell
.venv/Scripts/python.exe train.py dataset.csv
```

The tool trains logistic regression using NumPy over five forensic features, rejects identical file duplicates, creates a stratified 80/20 holdout, fits normalization on training examples only, and records accuracy, balanced accuracy, and confusion counts in `models/feature-model.json`. Image analysis discovers that file automatically. Set `TRUTHLENS_FEATURE_MODEL` to select another model. Holdout results are dataset-specific and do not establish production accuracy. Separate related sources and near-duplicates before creating independent benchmark sets.

## Add a deep-learning detector

Put a trusted, licensed ONNX model in `models/`, copy `onnx.example.json` to `onnx.json`, and match its preprocessing and output contract to the model documentation:

```powershell
$env:TRUTHLENS_ONNX_CONFIG = "$PWD/models/onnx.json"
.venv/Scripts/python.exe server.py
```

Supported contract: one RGB float32 NCHW input `[1,3,size,size]`; one binary output. `softmax` expects two logits, `sigmoid` expects one manipulated-class logit with index 0, and `probabilities` expects one or two values in [0,1]. `manipulated_index` must match the class label mapping. Default ImageNet normalization is only an example, not a universal preprocessing rule. Outputs use the first output tensor. The adapter resizes the entire image without face cropping; models requiring faces, additional inputs, temporal context, or another preprocessing pipeline need a dedicated adapter. ONNX configuration takes precedence over the feature model. Model setup failures make the job fail rather than silently returning an invented prediction.

The adapter uses ONNX Runtime’s official Python inference API: https://onnxruntime.ai/docs/api/python/api_summary.html. Image-detail measurements follow the discrete Laplacian operator described in OpenCV’s documentation: https://docs.opencv.org/4.x/d5/db5/tutorial_laplace_operator.html.

## Privacy and security

Image bytes remain in memory during analysis. Video uploads use randomly named temporary directories that are removed in `finally`/context-manager cleanup. Raw media and full article text are not stored in the report database. Reports retain filenames, hashes, measurements, optional article URLs, and matched language phrases. Data stays in `data/reports.sqlite3` unless `TRUTHLENS_DATA_DIR` selects another directory. History retains the latest 200 records after subsequent submissions; deletion removes the logical row, not a guaranteed forensic disk wipe.

Host and Origin checks restrict local API access, custom headers protect deletion, content types and body sizes are checked, static assets use a fixed allowlist, displayed data is HTML escaped, and the server sends a restrictive Content Security Policy. Load only trusted local model files. Local access is not user authentication, and native media decoders are not sandboxed. Do not expose this server directly to the internet.

## Test

```powershell
.venv/Scripts/python.exe -m unittest discover -s tests -v
```

The suite checks real image decoding, malformed inputs, video sampling, feature-model predictions, training, queued API jobs, history/export payloads, deletion, and rejected cross-origin requests. Synthetic fixtures validate software behavior, not detection accuracy.

## Structure

`server.py`: bounded-job HTTP API and SQLite persistence. `truthlens/analysis.py`: forensic and model inference methods. `web/`: accessible responsive interface. `train.py`: dataset training. `models/`: model configuration. `tests/`: behavioral verification. `requirements.txt`: runtime dependencies.

## To reach a validated production detector

Choose independently benchmarked models and licensed training data; add face tracking, audio and lip-sync analysis, retrieval-backed claim verification, provenance validation, calibration and false-positive evaluation. A public service additionally needs authenticated users, isolated decoder workers, quotas, retention policies, and deployment hardening. These are development steps, not features claimed by this release.
