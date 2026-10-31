# Thor brand assets

The final design uses a straight T with a wide cap and narrow foot, a double
diagonal H, Odin's outlined geometric O, and the R with a sharp, joined leg.
No installed font is needed: every letter is an outlined vector path.

Three exported images:

- `logo-slim.png`: 500 × 260 wordmark, white letters on the rounded gold gradient.
- `emblem.ico`: white T on the rounded gold app background.
- `sourcefile.ico`: yellow T with a transparent background, without a document
  silhouette, tile, or shadow.

Both ICOs contain 16, 24, 32, 48, 64, 128 and 256 pixel images. The matching
`logo-slim.svg`, `emblem.svg` and `sourcefile.svg` files are the editable masters.
Exploratory lettering studies are retained in `explorations/`.

The gold gradient shifts Odin's original blue to a 50° hue while preserving
its saturation and brightness stops: `#FED818`, `#DAB810`, and `#937A00`.
The transparent source glyph uses solid `#FED818`. The VS Code extension
contributes this SVG as its dark-theme language icon and uses `#937A00` on light
themes. Its listing icon is `editors/vscode/images/emblem.png`.

To regenerate PNG and ICO exports with Chrome and ImageMagick installed:

```sh
python3 tools/build_branding.py
```

Optional `--chrome`, `--magick`, and `--preview-dir` select tool paths or save
PNG previews. This export step is only for editing brand assets; normal Thor
builds and the installed VS Code extension do not need these tools. If the file
glyph changes, update the matching SVGs in `editors/vscode/fileicons/` as well.
