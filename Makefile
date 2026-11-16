.PHONY: install test doctor sources demo guide

install:
	python3 scripts/bootstrap.py --profile core

test:
	PYTHONPATH=src python3 -m unittest discover -s tests -v

doctor:
	PYTHONPATH=src python3 -m viceclip.cli doctor

sources:
	python3 scripts/verify_upstream.py

demo:
	python3 scripts/make_demo.py
	PYTHONPATH=src python3 -m viceclip.cli run --input examples/demo-source.mp4 --source-id demo --config examples/config.json --transcript examples/transcript.json --top 1 --output-dir output/first-run

guide:
	python3 scripts/build_guide.py
