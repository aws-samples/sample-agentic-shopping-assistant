# Design

The visual system of the NORDLY storefront and its assistant, Aria: warm paper and pine ink by day, a polar-night theme by dark, a variable serif for display over a Korean sans for text, and textiles drawn in CSS instead of photography.

Every value here comes from the source and names its file. Paths are relative to `packages/web/app/` unless they start with `packages/`. Change a value at its source, then update this page. [FRONTEND.md](FRONTEND.md) covers how the app works, and [response layout](design-docs/response-layout.md) how an answer is composed.

## Where the system lives

| Source | Defines |
| --- | --- |
| `assets/css/app.css` | Font imports, the two daisyUI themes, the `--nd-*` brand tokens and their Tailwind names, global styles including Korean line breaking, `.kicker` and `.label`, the `.rise` animation, the weave patterns, answer prose (`.nd-prose`, with code and tables), citation chips (`.product-cite`), the reduced-motion override |
| `packages/web/nuxt.config.ts` | Global CSS (`app.css`, PrimeIcons), `lang="ko"`, the favicon link, `theme-color` |
| `composables/useTheme.ts` | Theme choice and persistence |
| `components/**` | Sizes, radii, shadows and motion, as Tailwind utilities, mostly with arbitrary values |

There is no `tailwind.config` file: Tailwind CSS 4 is configured in CSS (`@theme inline`, `@plugin`). Spacing, breakpoints and the default transition are Tailwind's defaults.

## Typography

| Role | Family | Package | Loaded | Fallbacks | Utility |
| --- | --- | --- | --- | --- | --- |
| Display | Hahmlet Variable, a serif | `@fontsource-variable/hahmlet` | Variable weight, 100–900 | Noto Serif KR, serif | `font-display` |
| Text | IBM Plex Sans KR | `@fontsource/ibm-plex-sans-kr` | 400, 500, 600 | Apple SD Gothic Neo, sans-serif | `font-sans`, the page default |

Source: `assets/css/app.css` (the imports, `--font-display` and `--font-sans`). The sans face is loaded at 400, 500 and 600 only; every light weight in the scale below (300–450) is on display text.

### Scale

Sizes are set per component; there is no named type scale.

| Role | Font | Size | Weight | Line height, tracking | Source |
| --- | --- | --- | --- | --- | --- |
| Hero headline | display | `clamp(2.75rem, 6.4vw, 5.6rem)` | 320 | 1.02, −0.025em | `components/home/Hero.vue` |
| Season name | display | `clamp(2.4rem, 4.8vw, 3.6rem)` | 300 | 1 | `components/home/Seasons.vue` |
| Section heading | display | `clamp(1.8rem, 3.4vw, 2.6rem)` | 340 | normal | `components/home/Categories.vue`, `Seasons.vue`, `HowItWorks.vue` |
| Hero ask input | display | `clamp(1.2rem, 2.3vw, 1.85rem)` | 340 | normal | `components/home/Hero.vue` |
| Chat panel greeting | display | 1.7rem | 340 | snug | `components/chat/Bot.vue` |
| Empty-chat heading | display | 1.45rem | 340 | snug | `components/chat/Content.vue` |
| Dialog title | display | 1.35rem (history), 1.2rem (delete), 1.15rem (error) | 360, 400, 400 | normal | `components/chat/SessionList.vue`, `ErrorModal.vue` |
| Wordmark | display | 1.3rem | 360 | tracking 0.34em | `components/header/Logo.vue` |
| "Aria" in the chat header | display | 1.15rem | 400 | 1 | `components/chat/Header.vue` |
| Answer heading (`h1`–`h4`) | display | 1.02rem | 450 | 1.4 | `assets/css/app.css` (`.nd-prose`) |
| Product name | display | 1rem | 400 | snug | `components/chat/ProductCard.vue` |
| Hero body | sans | 1.06rem | 400 | 1.75 | `components/home/Hero.vue` |
| Answer prose | sans | 0.94rem | 400 | 1.7 | `components/chat/Answer.vue` |
| Code and tables in answers | monospace (`ui-monospace` first) for code; sans for tables | 0.86em (code), 0.86rem (tables) | 400; 600 for table headers | 1.5 for tables | `assets/css/app.css` (`.nd-prose`) |
| Chat text: the shopper's bubble, panel copy and buttons | sans | 0.93rem | 400; 500 for buttons | relaxed | `components/chat/Content.vue`, `Bot.vue` |
| Chat input | sans | 1rem below `sm`, so iOS doesn't zoom in on focus; 0.93rem from `sm` | 400 | normal | `components/chat/Input.vue` |
| Navigation, footer links, page buttons | sans | 0.9rem | 400; 500 for buttons | normal | `components/Header.vue`, `Footer.vue`, `home/Hero.vue` |
| Chips and header buttons | sans | 0.85rem | 400; 500 for "Aria에게 묻기" | normal | `components/home/Hero.vue`, `chat/Suggestion.vue`, `Header.vue`, `header/Account.vue` |
| Label (`.label`) | sans | 0.75rem | 500 | tracking 0.01em, `muted` | `assets/css/app.css` |
| Kicker (`.kicker`) | sans | 0.75rem, uppercase | 600 | tracking 0.22em, `muted` | `assets/css/app.css` |
| Fine print | sans | 0.75rem (`text-xs`) | 400 | normal | `components/chat/Header.vue`, `Launcher.vue`, `Input.vue` |

There are two label styles. `.kicker` is for short English kickers above Korean headings ("Shop by category", "History"). `.label` is for Korean labels, with tight tracking because Hangul reads poorly when tracked wide. Section body copy and captions use Tailwind's `text-sm` (0.875rem) and `text-xs` (0.75rem). No text is smaller than 0.75rem (12px).

Korean wraps between words (eojeol), not inside them: `html` sets `word-break: keep-all`, with `overflow-wrap: anywhere` so a long URL or product code still breaks instead of overflowing. Table cells in answers use `overflow-wrap: break-word`, and their wrapper scrolls sideways.

## Color

### Brand tokens

Components use these, not the daisyUI names. Each is a CSS variable switched by the theme and exposed to Tailwind under the token name (`bg-paper`, `text-ink`, `border-line`), with opacity modifiers such as `bg-card/95`.

| Token | Variable | Light (`nordly`) | Dark (`nordly-night`) | Used for |
| --- | --- | --- | --- | --- |
| `paper` | `--nd-paper` | `#f3efe7` | `#0e1412` | Page background, input fields, product cards; text on `ink` buttons and on the `lingon` delete button |
| `paper-deep` | `--nd-paper-deep` | `#ebe5da` | `#151d1a` | The seasons band; hover fill of icon and outline buttons; code and table headers in answers |
| `card` | `--nd-card` | `#fbf8f2` | `#18211d` | Chat panel, dialogs, citation chips, highlighted chips |
| `ink` | `--nd-ink` | `#1a211e` | `#ece6da` | Text, primary buttons, the shopper's bubble |
| `ink-soft` | `--nd-ink-soft` | `#2f3833` | `#d6cfc2` | Secondary text |
| `muted` | `--nd-muted` | `#5e655f` | `#a3a9a1` | Captions, labels, kickers, placeholders, notes under an answer |
| `line` | `--nd-line` | `#d8d1c4` | `#2a3430` | Borders and dividers |
| `lingon` | `--nd-lingon` | `#a63a2a` | `#e0705a` | The accent: brand mark, hover fill of primary buttons, focus ring, error text, activity dots, text selection |
| `pine` | `--nd-pine` | `#24413a` | `#8fb3a6` | Only the dot before "실제 카탈로그에 있는 상품만 추천해요." in the chat panel |

`--nd-grain-opacity` is 0.06 in light and 0.08 in dark. Selected text is `card` on `lingon`. Source: `assets/css/app.css` (the `:root` and `[data-theme]` blocks, `@theme inline` and `::selection`).

### daisyUI themes

`app.css` turns off daisyUI's built-in themes (`themes: false`) and defines two of its own. Their values mirror the brand tokens. The app uses only one daisyUI component, the error dialog's `modal`, so these values matter mostly there.

| daisyUI variable | `nordly` | `nordly-night` |
| --- | --- | --- |
| `base-100`, `base-200`, `base-300` | `#f3efe7`, `#ebe5da`, `#d8d1c4` | `#0e1412`, `#151d1a`, `#2a3430` |
| `base-content` | `#1a211e` | `#ece6da` |
| `primary`, `primary-content` | `#1a211e`, `#f3efe7` | `#ece6da`, `#0e1412` |
| `secondary`, `secondary-content` | `#24413a`, `#f3efe7` | `#8fb3a6`, `#0e1412` |
| `accent`, `accent-content` | `#a63a2a`, `#fbf8f2` | `#e0705a`, `#0e1412` |
| `neutral`, `neutral-content` | `#2a312d`, `#f3efe7` | `#1d2622`, `#ece6da` |
| `error`, `error-content` | `#a63a2a`, `#fbf8f2` | `#e0705a`, `#0e1412` |
| Flags | `default: true`, `color-scheme: light` | `prefersdark: true`, `color-scheme: dark` |

### Fixed colors

These don't change with the theme.

| What | Values | Source |
| --- | --- | --- |
| Weave patterns | knit `#b9a58a`, twill `#2d4a43`, herringbone `#6d2f27`, each with white or black stripes at 10–16% | `assets/css/app.css` |
| Season tones, for the hover bar | spring `#8fb3a6`, summer `#d9b56f`, fall `#b5653f`, winter `#24413a` | `components/home/Seasons.vue` |
| Product swatches | 31 catalog colour names mapped to hex (trimmed, case-insensitive); unknown names `#a8a294` | `components/chat/ProductCard.vue` |
| Text on textiles | `#f6e9e4` on the hero's tweed; category numerals `white/80` on twill and herringbone, and `#1a211e` at 80% on knit | `components/home/Hero.vue`, `home/Categories.vue` |
| Overlays | `rgb(14 20 18 / 0.35)` behind the history dialog and `/ 0.5` behind the delete confirmation, both blurred | `components/chat/SessionList.vue` |
| Browser chrome | `theme-color` `#1a211e`; favicon: a `#1a211e` tile, a `#f3efe7` star, a `#e0705a` dot | `packages/web/nuxt.config.ts`, `packages/web/public/favicon.svg` |


## Surfaces and texture

- **Paper grain.** `body::before` is a fixed SVG fractal-noise layer at `--nd-grain-opacity` that ignores the pointer. It sits above the page background and below the app (`#__nuxt` is at `z-index: 1`).
- **Textiles instead of photos.** `.weave-knit`, `.weave-twill` and `.weave-herringbone` fill the hero's still life and the category cards. A product card shows a swatch in the product's colour with a diagonal stripe.
- **Frosted header.** The site header is `paper` at 85% opacity with `backdrop-blur-md`.

Source: `assets/css/app.css`, `components/home/Hero.vue`, `home/Categories.vue`, `chat/ProductCard.vue`, `Header.vue`.

## Spacing and layout

Spacing uses Tailwind's default scale (0.25rem steps) and its default `sm`, `md` and `lg` breakpoints; there are no custom spacing tokens.

| Element | Values | Source |
| --- | --- | --- |
| Page container | max width 1280px; `px-5`, `sm:px-8` | `components/Header.vue`, `Footer.vue`, `home/*` |
| Site header | 4rem high, sticky | `components/Header.vue` |
| Chat panel | Full screen below `sm`; from `sm`, 420px wide and `min(700px, 100vh - 3rem)` high, 1.5rem from the bottom and right | `components/chat/Bot.vue` |
| Minimized panel | A 232 × 56px pill, 1.5rem from the bottom and right | `components/chat/Bot.vue` |
| Chat padding | Header `px-5 py-4`; transcript `px-5 py-6`; input bar `px-4 pt-3 pb-4` | `components/chat/Header.vue`, `Content.vue`, `Input.vue` |
| Transcript rhythm | 1.25rem between turns, 0.75rem between the parts of an answer | `components/chat/Content.vue` |
| Shopper bubble | At most 82% of the transcript's width | `components/chat/Content.vue` |

## Radius

| Radius | Used by | Source |
| --- | --- | --- |
| Full (`rounded-full`) | Buttons, chips, the chat field, icon buttons, avatars, the launcher, the minimized panel, citation chips | components; `.product-cite` in `assets/css/app.css` |
| 18px, with a 5px bottom-right corner | The shopper's bubble | `components/chat/Content.vue` |
| 14px | The chat panel from `sm`, dialogs | `components/chat/Bot.vue`, `ErrorModal.vue`, `SessionList.vue` |
| 10px | Product card, unknown-product note, the history error in the transcript | `components/chat/ProductCard.vue`, `Answer.vue`, `Content.vue` |
| 8px; 4px | Code blocks and table wrappers in answers; inline code | `assets/css/app.css` (`.nd-prose`) |
| 0.375rem (`rounded-md`) | The account error popover | `header/Account.vue` |
| 5px | Product swatch | `components/chat/ProductCard.vue` |
| 3px; 2px | Hero and category textiles; their labels | `components/home/Hero.vue`, `Categories.vue` |

daisyUI's `--radius-box` (0.375rem), `--radius-field` and `--radius-selector` (999px) are set in both themes (`assets/css/app.css`). The error dialog is the only daisyUI component, and its box overrides the radius to 14px.

## Elevation and layers

| Element | Shadow | Source |
| --- | --- | --- |
| Chat panel | `0 40px 90px -40px rgb(0 0 0 / 0.6)` | `components/chat/Bot.vue` |
| Launcher | `0 18px 40px -18px rgb(0 0 0 / 0.55)` | `components/chat/Launcher.vue` |
| Hero textiles | `0 40px 70px -40px` at 0.55–0.6 alpha; the smallest `0 30px 60px -30px` at 0.6 | `components/home/Hero.vue` |
| Category cards | `0 30px 60px -40px rgb(0 0 0 / 0.5)` | `components/home/Categories.vue` |
| History and delete dialogs | `shadow-2xl` | `components/chat/SessionList.vue` |

| Layer (`z-index`) | Element |
| --- | --- |
| 0, then 1 | Paper grain, then the app root |
| 30 | Site header |
| 40 | Launcher |
| 50 | Chat panel |
| 60 | History dialog |
| 70 | Delete confirmation; the skip link while focused |
| Top layer | Error dialog, a native `<dialog>` opened with `showModal()` |

## Motion

| Motion | Values | Where | Source |
| --- | --- | --- | --- |
| Rise | `nd-rise`: opacity 0 → 1 and `translateY(14px)` → none over 0.9s, `cubic-bezier(0.2, 0.7, 0.1, 1)`, delayed `--i` × 110ms | Hero copy, form, examples and textiles | `assets/css/app.css` (`.rise`), `components/home/Hero.vue` |
| Panel open and close | Opacity 0.3s ease-out and transform 0.35s `cubic-bezier(0.2, 0.7, 0.1, 1)` from `translateY(18px) scale(0.985)`; closing 0.2s ease-in | Chat panel | `components/chat/Bot.vue` |
| Minimize and restore | Width, height and radius, 300ms ease-out | Chat panel | `components/chat/Bot.vue` |
| Launcher | Opacity and `translateY(12px)`, 0.25s ease; lifts 2px on hover | Launcher | `components/chat/Launcher.vue` |
| Turning marks | 90° (logo, 700ms), 45° (header "Aria" button, 500ms), 180° (launcher, 700ms); the category arrow 45° (500ms) | On hover | `components/header/Logo.vue`, `Header.vue`, `chat/Launcher.vue`, `home/Categories.vue` |
| Underlines | Grow from the left: 1px `lingon` under navigation (300ms); a 4px tone bar under a season (500ms) | On hover | `components/Header.vue`, `home/Seasons.vue` |
| Card lift | A category card rises 4px (500ms) | On hover | `components/home/Categories.vue` |
| Activity | Three bouncing `lingon` dots (delays −0.3s, −0.15s, 0); a pinging dot on the chat avatar; a pulsing dot when minimized; a spinning mark (2.4s) on the sign-in callback; PrimeIcons spinners | While busy | `components/chat/Content.vue`, `chat/Header.vue`, `pages/auth/callback.vue` |
| Color changes | `transition-colors`, Tailwind's default 150ms | Buttons, links, chips | components |

The hero's placeholder also cycles through its examples every 3.2 seconds, in JavaScript (`components/home/Hero.vue`).

Under `prefers-reduced-motion: reduce`, `app.css` cuts every animation and transition to 0.01ms with a single iteration, and turns off smooth scrolling. The placeholder rotation isn't affected.

## Iconography

- **PrimeIcons 7**, loaded globally in `packages/web/nuxt.config.ts`, mostly at 0.8rem. In use: `pi-plus` (new conversation), `pi-history` (past conversations), `pi-comments` (a conversation), `pi-trash` (delete), `pi-times` (close), `pi-minus` (minimize), `pi-window-maximize` (restore), `pi-sun` and `pi-moon` (theme), `pi-arrow-up` (send), `pi-stop-circle` (stop, and the note on a stopped answer), `pi-info-circle` (the other notes under an answer), `pi-arrow-up-right` (category card), `pi-exclamation-circle` (error), and `pi-spinner` with `pi-spin` (busy).
- **Brand mark** (`components/brand/Mark.vue`): a four-point north star in `currentColor`, with an optional ring. Only the logo shows the ring.
- **Favicon** (`packages/web/public/favicon.svg`): the star in `#f3efe7` on a `#1a211e` rounded tile, with a `#e0705a` dot.
- Icons and marks are decorative (`aria-hidden="true"`); icon-only buttons carry an `aria-label` and a `title`.

## Components

### Site header

`components/Header.vue`: sticky, 4rem high, `paper` at 85% with a blur, a `line` bottom border. On the left, category buttons (hidden below `md`) at 0.9rem in `ink-soft`, each with a 1px `lingon` underline that grows on hover; each asks Aria a preset question. Next, the logo (`header/Logo.vue`): the ringed mark and the brand name, centered from `md` up. On the right, an outline pill "Aria에게 묻기" (an `ink` border that fills with `ink` on hover; only "Aria" below `sm`), which opens the panel, and the account text button "로그인"/"로그아웃" (`header/Account.vue`).

### Hero ask box

`components/home/Hero.vue`: a `.label` "Aria에게 물어보기", then a row on a 2px `ink` bottom border that turns `lingon` on focus. The row holds the mark in `lingon`, a borderless input in display type whose placeholder cycles through the examples, and an `ink` pill "물어보기" that turns `lingon` on hover. Example chips follow, outlined in `line` on `card` at 60%. Submitting opens the panel with the question.

### Chat launcher

`components/chat/Launcher.vue`: a fixed bottom-right `ink` pill with the mark, "Aria" in display type, "무엇이든 물어보세요" (ask anything) at 0.75rem, and a `lingon` dot. It is hidden while the panel is open.

### Chat panel

`components/chat/Bot.vue`: a `card` surface with a `line` border, a 14px radius and a deep, soft shadow; full screen on small screens. The signed-out and welcome states use the mark, a display greeting and full-width pill buttons: an `ink` primary and an `ink`-outlined secondary. When the sign-in expires mid-conversation, a `card` banner with a `line` top border takes the input's place: "로그인이 만료되었어요" in `ink`, a `muted` line, and an `ink` pill "다시 로그인하기".

Its header (`components/chat/Header.vue`) has a 40px `ink` circle with the mark and, while loading, a pinging `lingon` dot; "Aria" and a 0.75rem `muted` subtitle; then 32px round icon buttons for the theme, history (signed in only), minimize and close. Minimized, the header is the whole pill: the avatar, "Aria", a pulsing dot while loading, restore and close.

### Message blocks

`components/chat/Content.vue`:

- **The shopper's message**: a right-aligned bubble, `paper` text on `ink`, at most 82% wide.
- **Aria's answer**: a 28px round avatar (a `line` border and a `lingon` mark) beside a column of parts. Text parts are `.nd-prose` in `ink`: paragraphs 0.6em apart, disc and decimal lists, `strong` at 600, `h1`–`h4` in display type, quotes behind a 2px `line` rule, inline code and code blocks on `paper-deep` (blocks wrap), and tables in a `line`-bordered wrapper that scrolls sideways, with `paper-deep` headers. Links render as their text, and images not at all.
- **A note under an answer**: `text-xs` in `muted` after a `pi-stop-circle` (stopped) or `pi-info-circle` icon (cut off, or cut for length).
- **A turn's error**: centered `lingon` text under the transcript, with its action as an `ink` pill.
- **A history error**: at the top of the transcript, a `paper` box with a `line` border and a 10px radius, `ink-soft` text, an `ink` pill "다시 시도하기" and an outlined "닫기".

### Product card

`components/chat/ProductCard.vue`: a row on `paper` with a `line` border (`ink-soft` on hover), a 10px radius and 0.75rem padding. On the left, a 66 × 84px swatch in the product's colour with a diagonal stripe and the year on a `card` tag. On the right: a kicker with the product's top-level category (`masterCategory`) and season (0.75rem, tracking 0.16em), the name in display type, the category path in `muted`, a row with a colour dot and name, the gender and a usage pill, and a `lingon` text button "비슷한 상품 찾기 →" (find similar).

### Citation chips and the unknown-product note

- **Inline citation** (`.product-cite` in `assets/css/app.css`): the product's name in an inline pill, with a 1px `line` border on `card`, padding 0 0.5em, 0.86em type, line height 1.55, `ink` text. `.is-unknown` makes the border dashed and the text `muted`, reading "확인할 수 없는 상품" (unverifiable product).
- **Unknown standalone citation** (`components/chat/Answer.vue`): a note with a dashed `line` border, a 10px radius, `text-xs` in `muted`: "검색 결과에서 확인할 수 없는 상품이에요." (not in the search results).

### Status line

`components/chat/Content.vue`: under the transcript while a reply streams, indented to the answer column: three 6px bouncing `lingon` dots and a `text-xs` `muted` label. While history loads, a `lingon` spinner and "대화를 불러오고 있어요…" take their place. The same `role="status"` element stays in the page between turns, visually hidden, and holds the announcement of how the last turn ended. [FRONTEND.md](FRONTEND.md#tool-step-labels) lists the labels.

### Input bar

`components/chat/Input.vue`: `card` with a `line` top border. While loading, a small outline pill "응답 멈추기" (stop) with `pi-stop-circle` sits above the field. The field is a pill (`paper`, a `line` border that turns `ink` on focus) with a borderless input, 1rem below `sm` and 0.93rem from `sm`, with its placeholder in `muted`. The input stays enabled while a reply streams; only the 36px round `ink` send button is disabled (`lingon` on hover, 35% opacity when disabled). A 0.75rem disclaimer follows.

### Suggestion chips

`components/chat/Suggestion.vue`: pills at 0.85rem. A highlighted chip has a `card` fill, an `ink` border at 25% and `ink` text; a plain one a `line` border and `ink-soft` text. Both darken the border to `ink` on hover.

### Dialogs

- **Error** (`components/chat/ErrorModal.vue`): a daisyUI `modal`, a bottom sheet below `sm` and centered above. A `card` box (at most `max-w-sm`, 14px radius) with a `lingon` icon in a `lingon`/10 circle, the title "문제가 생겼어요" (something went wrong) in display type, the message in `muted`, and under a `line` divider an `ink` pill: "확인" (OK), or the error's action ("다시 시도하기", "새 대화 시작하기" or "다시 로그인하기") beside an outlined "닫기" (close).
- **History** (`components/chat/SessionList.vue`): a blurred overlay and a `card` panel (at most `max-w-md`, 14px, `shadow-2xl`) with the kicker "History", the title "지난 대화" (past conversations) in display type, rows divided by `line`, each titled by when it started or "대화 N" over a `muted` ID line ("지금 대화 ·" first for the open one) with a delete button, and a "새 대화 시작하기" (new conversation) pill on a `paper` footer. A list that fails to load shows its error with an `ink`-outlined "다시 시도하기"; a new conversation that fails shows its error in `lingon` under the pill.
- **Delete confirmation**: an `alertdialog` card with an outlined "취소" (cancel) and a `lingon` "삭제" (delete) in `paper` text. A failed delete shows its error in `lingon` above the buttons.

## Dark mode

- There are two daisyUI themes, `nordly` and `nordly-night`. The `--nd-*` tokens switch on `[data-theme="nordly-night"]` (`assets/css/app.css`), so components need no `dark:` variants, and none is defined.
- `useTheme().init()` runs when the layout mounts. A saved choice (`localStorage.theme`, `dark` or `light`) wins; otherwise `prefers-color-scheme` decides. It sets `data-theme` on `<html>`. The chat header's sun or moon button toggles the theme and saves the choice; there is no toggle outside the chat panel.
- Until `init()` runs, `<html>` has no `data-theme`, so the `--nd-*` tokens hold their light values even when the system prefers dark. daisyUI's `prefersdark` switches only daisyUI's own variables.
- The fixed colors above don't switch, `theme-color` included.

## Accessibility

- **Language.** `<html lang="ko">` (`packages/web/nuxt.config.ts`).
- **Skip link.** "본문으로 건너뛰기" (skip to content) jumps to `#main`. It is hidden until focused, then shows as an `ink` pill (`layouts/default.vue`).
- **Focus.** `:focus-visible` draws a 2px `lingon` outline, offset 3px. It sits in the base layer so utilities can override it (`assets/css/app.css`). The chat and hero inputs remove the outline and show focus on their container instead: the chat field's border turns `ink`, and the hero's bottom border turns from `ink` to `lingon`. The chat input stays enabled, and keeps focus, while a reply streams.
- **Live regions.** The transcript isn't a live region, so streamed text isn't read out delta by delta. The panel has one status region, the status line (`components/chat/Content.vue`): it reads each step, and announces once how a turn ended. The answer being written has `aria-busy="true"`. The chat header's subtitle isn't live. The history dialog's loading text and the callback message are `role="status"`. A turn's error under the transcript isn't live, because its dialog already announced it; the other inline errors, the expired-sign-in banner and the history error are `role="alert"`.
- **Dialogs.** The chat panel is a non-modal `role="dialog"` labelled "Aria 쇼핑 어시스턴트", and doesn't trap focus. Opening it moves focus to the message input or the view's primary button; Esc closes it, except while an IME is composing; closing returns focus to what opened it, or to the launcher. The history dialog is a `role="dialog"` with `aria-modal="true"`: it takes focus when it opens, Esc closes it (or first the delete confirmation, a `role="alertdialog"` that focuses "취소"), and focus returns to its opener. The error dialog is a native `<dialog>` opened with `showModal()`; its `close` event, from Esc, the backdrop or a button, resets the store's flag.
- **Labels.** Icon-only buttons have an `aria-label` and a `title`. Icons and marks are `aria-hidden`. The logo link is labelled "`<brand>` 홈" (home), and the chat input has a visually hidden label. Each history row's delete button is labelled "<title> 삭제", and each card's "비슷한 상품 찾기" is described by the product's name. A product's colour is given as text beside its swatch.
- **Motion.** See the reduced-motion override under [motion](#motion).

### Contrast

WCAG contrast ratios, computed from the token values:

| Pair | Light | Dark |
| --- | --- | --- |
| `ink` on `paper`, on `card` | 14.3, 15.5 | 15.0, 13.3 |
| `ink-soft` on `paper` | 10.6 | 12.0 |
| `muted` on `paper`, `card`, `paper-deep` | 5.2, 5.7, 4.8 | 7.8, 6.9, 7.2 |
| `lingon` on `paper`, on `card` | 5.6, 6.1 | 5.9, 5.2 |
| `paper` on `ink` (primary button) | 14.3 | 15.0 |
| `paper` on `lingon` (primary button on hover, the delete button) | 5.6 | 5.9 |
| `muted` on `paper` (the chat and hero placeholders) | 5.2 | 7.8 |
| `line` on `paper`, on `card` (borders) | 1.3, 1.4 | 1.5, 1.3 |
| `#1a211e` at 80% on the knit textile (category numeral) | 4.8 | 4.8 |

Every text pair above reaches 4.5:1 in both themes. Borders in `line` reach only 1.3–1.5:1, against the 3:1 that WCAG asks of a boundary that identifies a control; the suggestion chips, the chat field, the stop button and the delete dialog's cancel button are outlined only in `line`.
