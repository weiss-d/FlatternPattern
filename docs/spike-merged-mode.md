# Spike: Inkscape actions for merged mode

Date: 2026-10-18. Inkscape 1.4.4 (dcaf3e7d9e), Linux.

## Purpose

This spike checks that merged mode can use the Inkscape boolean actions in
batch mode. It also measures the speed and the seam quality.

## Test data

- One tile, 41 × 71 user units, with four stroked lines.
  The stroke width is 3 and the caps are round.
- 45 × 45 = 2025 tile copies with the transform
  `matrix(1.849 0.393 -0.393 1.849 0 0)`.
- A clip rectangle that covers a part of the tiles.

## Results

1. `object-stroke-to-path` on a group converts all strokes in the group.
   The result paths have no `fill` property, so they paint black.
2. `object-ungroup` is not an action name. The correct name is
   `selection-ungroup`. An unknown action name stops the action list.
3. `select-by-selector` accepts CSS selectors such as `#content > path`.
4. After `path-union` and `path-intersection`, the result path stays in the
   parent group of the input. It keeps the id of the bottom input path.
5. Call 1 (one tile: stroke to path, union, intersection) takes 0.23 s.
6. Call 2 with 2025 separate paths and `path-union` takes 35 s.
7. Call 2 with all tiles in one path and `path-union` takes 1.5 to 2.9 s.
   The result is the same: one path with the same subpath count.
8. With a seam overlap of 0 and of 0.01, the result has no subpath smaller
   than 1 square unit. The smallest subpath is 11.8 square units.

## Decisions

- Merged mode writes all tile copies as subpaths of one path in Python.
  It does not write one path for each tile. This makes call 2 about 12 times
  faster.
- The seam overlap stays as a parameter with the default 0.01. It does no
  harm, and other tiles can have seams at small angles.
- The extension reads the result as the path child of a known group.
  It does not use the id of the result.
