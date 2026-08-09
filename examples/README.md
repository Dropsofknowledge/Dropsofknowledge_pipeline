# Example data

`sample_lecture/` contains a ready-made `transcript.srt` and `clip_plan.json`.
Add an `audio.mp3` (any lecture) and a `background.png`, then:

    START_HERE.cmd  ->  Import Lecture  ->  Kabair / 0048 / (this folder)

Or with the portable harness:

    python3 harness/dok_harness.py import . Kabair 0048 examples/sample_lecture
    python3 harness/dok_harness.py render Projects/Kabair_0048 .
