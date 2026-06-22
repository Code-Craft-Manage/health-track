# Measurement guide images

Ten original, photorealistic cards showing how to take each body measurement
Health-Track tracks — a model demonstrating the technique with the tape at the
target landmark, plus numbered steps. They replace the reference screenshots in
`../original-measurements-images/` (which came from a third-party app) with
imagery generated in the user's own Gemini account, so there's no licensing or
royalty exposure.

## Files

- `cards/<key>.png` — the finished cards (photo + baked-in numbered steps).
  Ready to use. `cards/<key>.svg` is the editable source.
- `photos/<key>.png` — the raw photoreal images (no text), if you want them on
  their own. 572×1024 portrait.
- `build_cards.py` — rebuilds the cards from the photos + step text. Edit the
  wording, fonts, or colours at the top and re-run.
- `image-prompts.md` — the text-to-image prompts used to generate the photos
  (and notes for regenerating any of them for free).

## Cards → Health-Track fields

One card per measurement *technique*; left/right fields share a technique.

| Card (`key`) | Bot field(s) in `config`/`bot.py` |
|---|---|
| `neck`      | `neck` |
| `shoulders` | `shoulders` |
| `chest`     | `chest` |
| `biceps`    | `biceps_left`, `biceps_right` |
| `waist`     | `waist` |
| `abdomen`   | `abdomen` |
| `hips`      | `hips` |
| `thigh`     | `thigh_left`, `thigh_right` |
| `weight`    | `weight` |
| `calf`      | `calf_left`, `calf_right` |

## Rebuilding the cards

```sh
python3 build_cards.py        # writes cards/*.svg and cards/*.png
```

Requires `rsvg-convert` (`brew install librsvg`). To swap a photo, replace the
file in `photos/` and re-run. To regenerate a photo, see `image-prompts.md`.
