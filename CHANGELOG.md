# Change log

This file records the changes in each release of FlatternPattern.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
The version numbers follow [Semantic Versioning](https://semver.org/).

## [0.1.1] - 2026-10-22

### Fixed

- Merged outlines: a paint that does not show no longer makes the merged shape larger.
  For example, an invisible stroke on the tile content now gives no extra area.
- Clipped copies: the output has fewer groups. A tile with one content element
  gets no wrapper group that holds only that element.

## [0.1.0] - 2026-10-21

First release.

### Added

- Clipped copies: one copy of the tile content in each tile, with a clip in
  the shape of the object.
- Merged outlines: one filled path for each object, for stencils, plotters
  and cutters.
- Pattern strokes, with a mask in the shape of the stroke.
- An option to overlap clipped copies. The overlap hides thin light seams
  between the tiles.
- Removal of the pattern definitions that nothing uses after the change.

[0.1.1]: https://github.com/weiss-d/FlatternPattern/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/weiss-d/FlatternPattern/releases/tag/v0.1.0
