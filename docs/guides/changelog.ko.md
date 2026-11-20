# 변경 기록 작성 가이드

[English](changelog.md)

릴리스마다 2단계 제목 하나를 사용합니다: `## [X.Y.Z]` 또는 `## [vX.Y.Z]`.
뒤에 날짜를 붙일 수 있습니다. `X.Y.Z`는 실제 시맨틱 버전으로 바꾸고 최신 릴리스를
먼저 배치합니다. 아직 출시하지 않은 작업은 `[Unreleased]`에 기록합니다.

같은 릴리스 제목 아래에 내용이 일치하는 영어와 한국어 기록을 작성합니다.
해당하는 경우 기능, 개선, 버그 수정으로 구분하고 빈 범주는 생략합니다.
기계가 읽는 버전 제목을 중복하거나 번역하지 마세요.

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

위 템플릿은 두 언어를 함께 작성하는 전체 형식입니다. 날짜 자리표시자는 실제
릴리스 날짜로 바꾸고 각 영어 항목에 대응하는 한국어 항목을 작성합니다.

`scripts/release.py`는 `CHANGELOG.md`의 명시적인 숫자 버전 제목을 읽습니다.
`[Unreleased]`만으로는 태그를 만들지 않습니다. 미출시 메모 수정으로 이전 릴리스를
다시 태그하면 안 됩니다. CI와 배포 게이트는 [릴리스](release.ko.md)를 참고하세요.
