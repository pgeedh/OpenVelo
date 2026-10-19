"""
Rebuild the bundled sample fit shown on first launch (assets/demo/).

    python scripts/build_demo.py
"""
import json
import os
import shutil
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from src import engine  # noqa: E402
from src.web import app as web  # noqa: E402

SAMPLE = 'videos/testvideo2.mp4'


def main():
    shutil.rmtree(web.DEMO_DIR, ignore_errors=True)
    os.makedirs(web.DEMO_DIR)
    res = engine.analyze_video(os.path.join(web.SAMPLES_DIR, SAMPLE), profile_key='road_race',
                               progress_cb=lambda f, m, s='': print(f"\r{f:5.0%} {m:<40}", end=''))
    res = web._finalize(web.DEMO_ID, res, {'sample': SAMPLE, 'name': 'testvideo2.mp4', 'type': 'video'},
                        'Sample ride · road bike on a trainer', {'name': 'Sample rider'})
    with open(os.path.join(web.DEMO_DIR, 'result.json'), 'w', encoding='utf-8') as f:
        json.dump(res, f, separators=(',', ':'))
    print(f"\nScore {res['score']} · {res['quality']['grade']} capture · written to {web.DEMO_DIR}")


if __name__ == '__main__':
    main()
