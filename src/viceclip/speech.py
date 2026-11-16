"""Local speech recognition with cached models and no remote inference API."""
import importlib.util
import json
from pathlib import Path


def transcribe_file(input_path, output_dir, model="base", device="cpu", language=None):
    target = Path(output_dir)
    target.mkdir(parents=True, exist_ok=True)
    path = target / (Path(input_path).stem + ".json")
    if path.exists():
        return str(path.resolve())
    if importlib.util.find_spec("faster_whisper") is None:
        raise ValueError("Распознавание речи не установлено. Запустите install.sh --profile autopilot")
    from faster_whisper import WhisperModel
    engine = WhisperModel(model, device=device, compute_type="int8" if device == "cpu" else "float16")
    segments, info = engine.transcribe(str(input_path), language=language, vad_filter=True,
                                      word_timestamps=True, beam_size=3)
    result = {"language": info.language, "segments": []}
    for segment in segments:
        text = segment.text.strip()
        if text:
            result["segments"].append({"start":segment.start,"end":segment.end,"text":text,
                "words":[{"start":w.start,"end":w.end,"word":w.word} for w in (segment.words or [])]})
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(result,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    temporary.replace(path)
    return str(path.resolve())
