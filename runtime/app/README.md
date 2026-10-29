# Application resources

`import "thor:app"` provides `resource_path(relative, allocator=context.allocator)`.
It returns an owned absolute path to project/package data. Delete the result with
the same allocator. Paths are relative to the entry project's root, such as
`assets/image.png`; absolute paths and `..` components are rejected.

Thor supplies the original entry directory for development builds. `thor publish`
selects the appropriate executable-relative package layout, so published resources
continue to work when the app is moved and the source project is unavailable.
Normal file I/O and the process working directory retain their ordinary behavior.

See [publishing](../../docs/publishing.md) for file selection, configuration,
platform layouts and ownership examples.
