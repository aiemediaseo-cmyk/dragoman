import sys, pathlib
from playwright.sync_api import sync_playwright
src, out, w, h, scale = sys.argv[1], sys.argv[2], int(sys.argv[3]), int(sys.argv[4]), float(sys.argv[5]) if len(sys.argv) > 5 else 1
with sync_playwright() as p:
    b = p.chromium.launch()
    pg = b.new_page(viewport={'width': w, 'height': h}, device_scale_factor=scale)
    pg.goto(pathlib.Path(src).resolve().as_uri())
    pg.evaluate("document.fonts.ready")
    pg.wait_for_timeout(300)
    pg.screenshot(path=out, clip={'x': 0, 'y': 0, 'width': w, 'height': h}, omit_background=False)
    b.close()
