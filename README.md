<p align="center"><img src="service.subtitles.dragoman/resources/icon.png" width="160" alt="Dragoman"></p>

# Dragoman

**Your subtitle interpreter for Kodi.** When a subtitle is loaded during playback, Dragoman
translates it into your language with Google Gemini and switches to the translation when it is
ready. Playback never pauses.

- Works with subtitles picked in Kodi's subtitle dialog, downloaded automatically by a subtitle
  add-on (a4kSubtitles and others), or sitting next to a local video.
- Subtitles already in your language, or in languages you choose to leave alone, are not translated.
- Runs on any Kodi 19+ device: Android TV, phones, Windows, macOS, Linux, LibreELEC.
- SRT and WebVTT subtitles. Finished translations are saved, so the same subtitle loads instantly next time.
- Fixes subtitle timing too: a Sync assistant in Kodi's subtitle dialog (pick a line, press OK when you
  hear it) and a check for subtitles made for another frame rate. Fixes are remembered.
- You need your own free Gemini API key: [aistudio.google.com/apikey](https://aistudio.google.com/apikey).

## Installation

1. In Kodi, open **Settings > System > Add-ons** and turn on **Unknown sources**.
2. Open the **File manager** and choose **Add source**.
3. Enter `https://aiemediaseo-cmyk.github.io/dragoman/` and name it **dragoman-repo**.
4. Go to **Add-ons > Install from zip file > dragoman-repo** and install `repository.dragoman-1.0.0.zip`.
5. Go to **Add-ons > Install from repository > Dragoman Repository > Services > Dragoman** and install it.
6. Open **Add-ons > Program add-ons > Dragoman** and add your Gemini API key.

Kodi then keeps Dragoman up to date by itself.

**Adding the key.** Type it (the keyboard shows what you type, and the whole key is shown again
before it is saved), or put it in an **Unlisted** paste on pastebin.com and choose
**Import API key from a web address...** with the paste's address, e.g. `pastebin.com/AbCd1234`.
Delete the paste afterwards; the key then exists only on your device.

**Coming from AI Subtitle Translator** (Dragoman's earlier name)? Dragoman copies your key,
languages and settings on its first start and offers to switch the old add-on off. Uninstall the
old one afterwards.

## Fixing the timing

During playback open **Subtitles > Download > Dragoman** and choose **Sync assistant**. Pick a line
you have not heard yet and press OK the moment you hear it: once fixes a delay; doing it again later
in the film fixes a frame-rate drift too. Subtitles made for a different frame rate (they drift
further off as the film goes on) are spotted, and Dragoman asks before fixing them
(**Settings > Timing**). Fixes, **Undo** and "no" are remembered for that video and subtitle, also for
its translations.

## Privacy

Only subtitle text, the title, the plot and the cast names are sent to Google; timings stay on your device. On
Gemini's free tier, Google may use the content to improve its products. The API key is never
written to logs or uploaded log reports.

## Repository layout

| Path | What it is |
| --- | --- |
| `service.subtitles.dragoman/` | The add-on |
| `repository.dragoman/` | The Kodi repository add-on that installs and updates Dragoman |
| `docs/` | The published Kodi repository (built by the script below; served by GitHub Pages and raw.githubusercontent.com) |
| `tests/` | Tests with stand-in Kodi modules and a fake Gemini server |
| `scripts/build_repo.py` | Builds `docs/` |
| `art/` | Sources of the icon and fanart |

## Releasing a new version

1. Change the add-on and raise `version` in `service.subtitles.dragoman/addon.xml` (add a line to `<news>`).
2. Run the tests: `cd tests && python3 -m unittest test_core test_service`
3. Build the repository: `python3 scripts/build_repo.py`
4. Commit everything, including `docs/`, and push to `main`. Kodi picks the update up on its next
   repository check (Add-ons > ... > Check for updates to do it at once).

## License

GPL-2.0-or-later, see [LICENSE.txt](LICENSE.txt). © AIE Media.
