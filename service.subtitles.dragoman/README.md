# Dragoman (service.subtitles.dragoman) v0.3.0 - test build

Your subtitle interpreter: translates subtitles into your language with Google
Gemini while the video keeps playing. Works on any Kodi 19+ device, for streams
and local files. (Up to v0.1.3 this add-on was called AI Subtitle Translator.)

## Install
1. Settings > System > Add-ons > enable **Unknown sources**.
2. File manager > Add source > `https://aiemediaseo-cmyk.github.io/dragoman/` (name it **dragoman-repo**).
3. Add-ons > Install from zip file > dragoman-repo > `repository.dragoman-1.0.0.zip`.
4. Add-ons > Install from repository > Dragoman Repository > Services > **Dragoman**.
5. Open Add-ons > Program add-ons > **Dragoman**. The first time, it walks you
   through adding your free Gemini API key (create one at
   aistudio.google.com/apikey) and confirming the language.

Installed from the repository, Dragoman is updated by Kodi automatically.
Source code: https://github.com/aiemediaseo-cmyk/dragoman

Coming from AI Subtitle Translator: Dragoman copies your API key, languages to
leave alone and settings on its first start, and offers to switch the old
add-on off (otherwise both would translate every subtitle). Uninstall the old
one afterwards: Add-ons > My add-ons > Services > AI Subtitle Translator.

## Adding your API key
- **Enter Gemini API key...** - the keyboard shows what you type, starts with
  the key that is saved now, and the whole key is shown again before it is
  saved (Save / Edit).
- **Import API key from a web address...** - put the key in a paste on your
  phone or computer and type the paste's address, e.g. `pastebin.com/AbCd1234`.
  Pastebin, paste.kodi.tv, GitHub gists, dpaste, paste.ee, Dropbox and Google
  Drive share links work, and so does any page that shows the key as text.
  Make the paste Unlisted (not Private) and delete it once the key is saved;
  the key then exists only on this device.
- **Import API key from a file...** - a .txt file on a USB stick or network share.

## Using it
Play something, then pick a subtitle (or let a subtitle add-on download one).
If it is not in your language, a small progress bar appears in the corner and
the translated subtitle replaces the original when it is ready (usually well
under a minute for a film). Playback never pauses.

- Subtitles already in your language, or in the languages you choose under
  **Languages to leave alone**, are never translated.
- **Translate the current subtitle now** (in the menu) translates the last
  subtitle anyway, even if it is in a left-alone language.
- Finished translations are saved, so picking the same subtitle again loads
  instantly without using your Gemini quota.

## Settings
- **General:** target language (Auto = Kodi's subtitle language), languages to
  leave alone, which sources to translate (download dialog / automatic
  downloads / files next to the video), translate automatically / ask first /
  do nothing.
- **Gemini:** enter the API key, import it from a web address or a file, test
  it, remove it (the key is kept in the add-on's own `state.json`, so closing
  the settings dialog can never undo it), model (default
  gemini-3.5-flash-lite), switch to another free model when the daily quota
  runs out.
- **Advanced:** lines per request, requests per minute, how long to keep saved
  translations, progress bar, notices, detailed log, send log.

## Privacy
Only subtitle text, the title, the plot and the cast names are sent to Google; timings stay on
your device. On Gemini's free tier, Google may use the content to improve its
products. The API key is never written to logs or uploaded reports, and
neither is the address it was imported from (only the site's name).

## Current limits (test build)
SRT and WebVTT subtitles (ASS later). Files are recognised by their content, so
subtitles saved without a .srt ending (a4kSubtitles sometimes does this) work
too. No timing fixes yet (drift check and sync assistant come in the timing
step). The whole file is translated before it is swapped in.

Log: `special://profile/addon_data/service.subtitles.dragoman/translator.log`,
lines in kodi.log start with `[Dragoman]`.
