# Publish example

From the Thor installation:

```sh
thor run examples/publish
thor publish examples/publish
```

`assets/` is included automatically and `assets/icon.png` supplies the icon.
`thor.toml` adds `data/defaults.txt`, sets the name/version/ID and selects GUI mode.
Icon conversion requires Pillow (`python3 -m pip install Pillow`).

On macOS this creates `Thor Publish Demo.app` in the directory where you ran the
command. Move it elsewhere; the copied files continue to work without this source
directory or Thor installed. The demo has no GUI of its own, so a Finder launch
quietly runs and exits without opening Terminal. To see its output:

```sh
"./Thor Publish Demo.app/Contents/MacOS/Thor"
```

The inner executable name follows the directory where you publish; the example
above assumes the installation's `Thor` directory. Choose one explicitly with
`-out:demo`, then use `Contents/MacOS/demo`. To request a Terminal launch instead:

```sh
thor publish examples/publish --app-type console -out:demo
```

On Windows extract the published ZIP and launch its `.exe`. On Linux extract the
tar archive and use `AppRun`; optional `install-desktop.sh` adds a menu entry.

See [the complete publishing guide](../../docs/publishing.md).
