# Changelog guide

[한국어](changelog.ko.md)

Keep each release under one level-two heading: `## [X.Y.Z]` or `## [vX.Y.Z]`,
optionally followed by a date. Replace `X.Y.Z` with a semantic version.
Keep the newest release first. Use `[Unreleased]` for work not yet released.

Add matching English and Korean notes beneath that same release heading.
Use Features, Improvements and Bug Fixes when relevant; omit empty categories.
Do not duplicate or translate the machine-readable version heading.

```markdown
## [X.Y.Z] (YYYY-MM-DD)

### English

#### Features

- Describe the new feature.

#### Improvements

- Describe the improvement.

#### Bug Fixes

- Describe the fix.

### 한국어

#### 기능

- 새 기능을 설명합니다.

#### 개선

- 개선 사항을 설명합니다.

#### 버그 수정

- 수정 내용을 설명합니다.
```

`scripts/release.py` reads explicit numeric version headings in `CHANGELOG.md`.
`[Unreleased]` alone creates no tag. Changes to unreleased notes must not retag
an old release. See [release](release.md) for the CI and deployment gates.
