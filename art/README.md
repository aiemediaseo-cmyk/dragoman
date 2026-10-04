# Artwork sources

`icon.html` (512×512) and `fanart.html` (1920×1080) are SVG drawings rendered with Chromium.
They use Playfair Display (SIL Open Font License) from the `@fontsource/playfair-display` npm package.

```sh
cd art
npm install @fontsource/playfair-display
pip install playwright        # uses an installed Chromium
python3 render.py icon.html icon.png 512 512
python3 render.py fanart.html fanart.png 1920 1080
```

Then save `icon.png` (RGB) as `service.subtitles.dragoman/resources/icon.png` and `repository.dragoman/icon.png`,
and `fanart.png` as JPEG (quality 88) as `resources/fanart.jpg` and `repository.dragoman/fanart.jpg`.
