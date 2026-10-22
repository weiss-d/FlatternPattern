# Contributing

Thank you for your help. To report a problem, use the
[issue form](https://github.com/weiss-d/FlatternPattern/issues/new/choose).

## Change the code

1. Set up the tools and run the tests. The
   [Development](README.md#development) section of the README gives the steps.
2. Make a branch from `develop`.
3. Add a test for each change in behavior. For a change in the output, also
   add a small SVG file to `tests/data/flatten/`. The render tests compare
   the input and the output of each file in this folder.
4. Open the pull request against `develop`, not `main`.

CI runs the linters and all tests for each pull request.

## Commit messages

Use the [Conventional Commits](https://www.conventionalcommits.org/en/v1.0.0/)
format: `<type>(<scope>): <description>`. The pre-commit hook checks the
format.

- Write the description in lowercase and in the imperative mood.
- Use one scope from this list: `inx`, `core`, `resolve`, `tiling`,
  `clipped`, `merged`, `cleanup`, `tests`, `docs`, `repo`.

Example: `fix(tiling): include stroke box in tile culling`.
