# Notebook: editor features, Card Creator and AI

This page covers what was added to the Notebook on top of the basic block editor. Syncing between computers has its own page: [NOTEBOOK_SYNC.md](NOTEBOOK_SYNC.md).

The editor is a custom `contenteditable` implementation in `web_notebook/index.html` (no editor framework). The DOM is the state; `saveCurrentEditorToState()` turns it into the JSON snapshot that `notebook_sidebar.py` stores in SQLite. Unknown block types and fields are preserved verbatim as long as the block is not edited (`createStoredBlock` / `storedBlocks`), which is why every addition below is additive.

## Editor

### Nested lists
- List blocks (`bullet`, `numbered`, `todo`) carry an optional `indent` field (0–5). A missing field means 0, so existing notes load unchanged.
- **Tab** / **Shift+Tab** change the level, only inside list blocks. Elsewhere Tab keeps its normal focus behavior.
- Rules: the first list item cannot be indented, an item can be at most one level deeper than the one above it, and the items below it that are already deeper move with it. With several blocks selected (drag selection), Tab moves all selected list items.
- **Enter** on an empty indented item and **Backspace** at the start of an indented item move it one level out. Only at level 0 do they keep their old behavior (turn the item into text).
- New lines inherit type and level.
- Numbers are not stored. CSS counters (`nb0`…`nb5`) show `1.` / `a.` / `i.` per level and restart after any non-list block. Bullets use `•`, `◦`, `▪` by level.
- `numbered` is a new block type: Markdown trigger `1. `, slash command `/numbered`, also in the type menus.
- A numbered item can start at any number: typing `3. ` (one to four digits) at the start of a block makes a numbered item with `start: 3` (omitted for 1); the following items continue from there. Note that this also applies to a paragraph that begins like a German date ("3. Mai …"); Ctrl+Z reverts the conversion.

### LaTeX block
- Block type `latex` with the source in `latex` (`html` stays empty). Slash commands `/latex`, `/math`, `/formel`; the menu also finds it by `formula`, `equation`, `tex`.
- Edit mode (text field with live preview) and display mode. **Escape** or **Ctrl+Enter** leave edit mode; a click or **Enter** on the formula re-opens it.
- Invalid LaTeX shows an error line under the formula and never throws. Limits: 12,000 characters, `trust: false`, `maxExpand: 500`.
- KaTeX 0.16.22 comes from `vendor/katex`. The notebook page is loaded with `setHtml()` (no base URL), so `notebook_sidebar.py` inlines the script and the stylesheet (fonts as `data:` URIs) as inert `text/plain` tags (`_katex_inline_tags`), and the page parses them on first use. Works offline.
- A block holding a formula is not silently converted into another type.

### Inline formulas
Formulas can sit inside any text: a sentence, a list item, a toggle title, a table cell.
- Typing **`$`** inserts an empty formula (shown as `$$`) and opens a small editor under it. What you type there is rendered straight into the text; **Enter** or **Tab** finishes with the caret after the formula, **Esc** also finishes. An empty formula disappears; **Esc** on a fresh, empty one leaves a literal `$` (so "5 $" can still be written), **Backspace** in the empty editor removes it. **Arrow keys** at either end of the editor leave it on that side. Selecting text and typing `$` makes the selection the formula.
- Invalid LaTeX shows the message in the editor and keeps the last good rendering. A click on a formula opens it again; undo restores the earlier version.
- A formula is atomic in the text: the arrow keys step over it, Backspace removes it as a whole. It is not available in code blocks or inside the LaTeX block.
- Storage: only the source, as `<span class="math-inline" data-latex="E=mc^2"></span>` inside the block's `html`. The sanitizer reduces every such element to this form (no rendered markup, no other attributes), so old data, pasted HTML and sync stay small and safe. KaTeX output (HTML only, `trust: false`) is rebuilt on load; until KaTeX is parsed a formula shows its source in `$…$`.
- Everything that reads text understands formulas: copying writes `\( … \)` as plain text and keeps the formulas in the HTML (pasting into the notebook brings them back), the AI note context and the Card Creator use `\( … \)`, which Anki renders itself.

### Colors on single words
- The format toolbar's color button (`A ▾`) colors the selected words as an inline `<span style="color/background-color">`. Selections across blocks color each block. Re-coloring replaces the old color instead of nesting; **Default** removes it. Whole-block color from the gutter menu works as before.
- Stored colors are theme independent. In Dark Mode, `body.nightMode` CSS rules display readable variants of the palette; the stored data does not change.

- **Highlight shortcut and button:** `Ctrl+Shift+H` (Cmd on macOS) colors the selected words with the last color picked in the palette; the first time that is a yellow background. Pressing it again on text that already has exactly that color removes it. The format toolbar has a matching button that shows the color and the shortcut. The last color (`highlightColor`: property and palette key) is stored with the notebook but is not part of undo history; picking "Default" never changes it.
- The shortcut is configurable in the Synapse settings, **Sidebar → Notebook** (same key recorder as the sidebar shortcuts; clearing it switches the shortcut off). It is stored as `notebook_highlight_shortcut` (Qt portable text) and handled by the notebook page itself, so changes apply without a reload.

### Typing helpers
- **`(`** writes `()` with the caret inside; **`)`** in front of an existing `)` steps over it. A selection is replaced as usual. The LaTeX source field is left alone.
- **Markdown triggers** (`# `, `## `, `### `, `- `, `1. `, `[] `, `> `) convert the block once the next character after the space is typed, and the caret stays at the end of the text.
- **Slash menu** opens only for a `/` at the start of the text or after a space, closes as soon as nothing matches, and understands aliases (`data-alias` on the menu items). Commands may contain digits (`/h1`).

### Copying several blocks
Blocks are separate editable fields, so the browser cannot select text across them. Dragging over several blocks (or **Ctrl/Cmd+A** a second time, or **Shift+Up/Down** at the edge of a block) selects whole blocks; the selection then works with **Ctrl+C**, **Ctrl+X** and **Ctrl+V**.
- Copy writes plain text (lists with numbering and indentation, `\[ … \]` for formulas), HTML for other apps, and block data (`application/x-synapse-blocks`: type, text, indent, start number, …) so pasting back into the notebook recreates the blocks. Pasting inserts after the current block, or replaces an empty one; with blocks selected it inserts after the selection.
- Pasted block data is treated as untrusted: known types and fields only, HTML sanitized, at most 500 blocks, no stable block ids.
- With a block selection, **Escape** clears it, **Backspace/Delete** delete the selected blocks (also when the selection was made with the keyboard), and typing ends it.

## Tabs: where you were, and keyboard switching
- Switching between Notebook, To-do, PDF and Changes reloads the page, which used to throw you back to the top. The pages now report their scroll position (debounced) to the add-on, which keeps it while Anki runs and hands it back on load (`window.__SYNAPSE_VIEW_STATE__`). The notebook remembers it per page, also when you switch pages inside the notebook or the data is reloaded after a sync; the to-do list remembers its own position. The PDF viewer does not restore its page yet.
- **Ctrl+Tab** and **Ctrl+Shift+Tab** (or **Ctrl+PageDown** / **Ctrl+PageUp**) switch to the next/previous visible tab and wrap around; tabs you have hidden in the workspace settings are skipped, the Changes tab is always included. The handler (`workspace_tab_keys.js`) is inlined into every tool page and runs before the focused field sees the key.

## PDF search
In the PDF tab, **Ctrl+F** (or the magnifier button) opens a search bar; **Enter** / **Shift+Enter** or F3 step through the hits, **Esc** closes it. The whole document is searched, ignoring case and accents (`CAFE` finds "Café"), and several spaces count as one. The first search reads the text of every page once (progress is shown); hits on the visible pages are highlighted and the current one is scrolled into view, also on pages that are not rendered yet. A new document or zoom change keeps the search working.

## Card Creator in the Notebook
The Card Creator of the PDF tab is also available here (button **Card Creator** in the notebook header).
- Card types: Basic, Basic + Reversed, Cloze. Deck field with search (`window.__SYNAPSE_DECKS__` and the current deck are injected when the notebook loads, so a deck created later appears after the next load; typing a new name creates it).
- Select text in a note and use **Card front** / **Card back** in the format toolbar to append it to the draft. Option "Add the notebook page as source" puts the page title in small text on the back.
- The unfinished session lives in the notebook snapshot as `cardCreatorSession` (sanitized when loaded, excluded from page undo/redo). It survives a restart.
- Creating goes through `NotebookPanel._create_pdf_card_batch(body, notebook=True)`, the same validated, undoable batch as the PDF tab (answer callback `onNotebookCardBatchCreated`). Limits are the PDF ones: 500 cards per session, 50,000 characters per card.

### Card Creator width

Drag the left edge of the Card Creator to make it wider or narrower (280–760 px, at most 75 % of the window); double-click the edge to reset. The width is remembered separately for the notebook and the PDF tab. In the PDF tab the handle appears in the side-by-side layout (fullscreen or window mode); in the narrow sidebar the creator sits above the PDF.

### Rich card fields (Notebook and PDF Card Creator)
Front and Back are small rich-text editors, so a card can have headings, lists, formulas and pictures. **The result is plain Anki HTML**: the cards render the same in Anki on an iPad, phone or desktop without this add-on.

| You write | The field stores |
| --- | --- |
| `#` / `##` / `###` + space, or the **H1–H3** buttons | `<h1>`…`<h3>` (deeper levels from pasted text become `<h3>`) |
| `-`, `*` or `•` + space, or **•** | `<ul><li>` |
| `1.` / `4)` + space, or **1.** | `<ol>` (starts at the number you typed: `start="4"`) |
| **Tab** / **Shift+Tab** (or **⇥ ⇤**) in a list | nested lists; nested ordered lists use `type="a"`, then `type="i"` |
| **Ctrl+B / I / U** | `<b>`, `<i>`, `<u>` |
| `$x^2$` (or **∑**) | `\(x^2\)`, rendered by Anki's MathJax; `\[ … \]` pasted from a note becomes a display formula |
| **Ctrl+V** of a picture, or dropping an image file | `<img data-img="3">` in the session, `<img src="synapse_notes_<hash>.<ext>">` in Anki |

- `$…$` follows the usual rule: no space after the opening or before the closing `$`, and the closing `$` is not followed by a digit, so "costs $5 and $6" stays text. A click on a formula turns it back into `$…$` for editing. Formulas containing `}}` are written as `} }` so they cannot end a cloze early.
- Cloze: **New cloze** / **Same cloze** insert `{{cN::` and `}}` around the selection; formatting that the selection covers completely stays inside (`{{c1::<b>x</b>}}`). A cloze may contain markup and formulas.
- Pasted HTML (from a website, a PDF, another note) is cleaned in the page and again in Python: only the tags above survive; classes, styles, links, scripts and foreign images are dropped. Selections taken from the notebook or the PDF keep their `\( … \)` formulas.
- The session stores each field as an HTML string (flag `rich: true` on the session). Sessions saved by older versions hold plain text with `[[img:N]]` markers and are converted once when loaded.
- Pictures stay out of the field HTML: the session keeps `images` (id → `data:` URI) and `nextImage`. Large screenshots are scaled to 1600 px and re-encoded; at most 12 pictures per session, 2 MB each. Pictures no field uses any more are dropped when the session is saved. Loaded sessions are cleaned (only well-formed png/jpeg/gif/webp).
- On creating, the batch carries the field HTML and just the pictures cards use. `card_html.sanitize` (allow-list, pure Python) re-checks every field, `notebook_cards.decode_batch_images` checks the pictures by their bytes (SVG rejected; at most 40 and 40 MB per batch), and `<img data-img>` becomes `<img src="synapse_notes_<hash>.<ext>">` with the file in Anki's media folder, identical pictures once. A placeholder without its picture stops the batch with a message. A field with only empty blocks counts as empty. Limits: 50,000 characters per field in the page; the cloze rule is `{{cN::…}}` up to the first `}}`.
- Code: `web_notebook/card_editor.js` (the editor; the field element exposes `.value` like a textarea) and `web_notebook/card_images.js` (picture helpers), both inlined into the two pages by `notebook_sidebar.py`; KaTeX is inlined into the PDF page as it is for the notebook.

## AI assistant and notes
The chat (`chat_ui.html`, `ai_assistant.py`) can read notebook pages and suggest cards from them. Reading is strictly read-only.

### Notes as context ("Select notes")
- The picker (`list_notes`) shows the page tree with sections and sub pages. The selection is attached to the following questions (at most 30 pages).
- On each send, `notebook_text.pages_to_context` reads the pages fresh from the saved notebook and converts blocks to plain text: headings, lists with indentation and numbering, `- [x]` todos, quotes, code, toggles, tables, formulas as `\[ … \]`. Colors and other formatting are dropped; only `http(s)` links survive. Limits: 12,000 characters per page, 40,000 in total.
- The text goes into a `<notebook_pages>` block of the hidden request prompt, marked as untrusted reference material. It is not stored in the chat history. If a selected page no longer exists the request stops with a message.

### Card suggestions ("Cards from notes")
1. `notes_cards` builds a prompt (`notebook_cards.build_prompt`) and asks the configured provider for a JSON array of `{front, back, image}` (non-streaming `_complete_text`, up to 4,096 output tokens on OpenAI-compatible APIs). `parse_cards` tolerates code fences and wrappers, keeps at most 30 cards, and drops anything malformed.
2. The preview lets you edit, deselect and choose the deck and where a picture goes. Nothing is created before you confirm.
3. `create_cards` validates the request again (`prepare_notes`), writes pictures to Anki's media folder and adds the notes with `col.add_notes` (note type from `pdf_card_types.resolve(col, "basic")`).

### Pictures
- Pasted pictures are stored in notes as `data:image/…;base64`. For the AI each distinct picture becomes a marker (`[img1]`, `[img2]`, …); **the picture data is never sent to the provider**. The model may attach a marker to a card only when the picture sits directly next to the text the card is about; invented markers are dropped.
- The web view only names a marker. The bytes come from a local cache of the last suggestion (`_card_images`) and are checked by their magic bytes (png, jpg, gif, webp; SVG is rejected), at most 5 MB each and 40 per request. Files are stored as `synapse_notes_<sha1>.<ext>`, so a picture used twice is stored once.
- Card text is HTML-escaped; `\( … \)` and `\[ … \]` stay intact for Anki's MathJax.

### Privacy
Selected pages go to the AI provider you configured, only after you select them. With Ollama or a llama.cpp server they stay on your computer.

## Data format additions
All additive; older data and unknown fields keep working.

| Where | What |
| --- | --- |
| block | `type: "numbered"`, `type: "latex"` with `latex` |
| inline text | `<span class="math-inline" data-latex="…"></span>` (formulas inside any block's `html`) |
| list block | `indent` (0–5, omitted when 0) |
| numbered block | `start` (omitted for 1) |
| snapshot root | `cardCreatorSession`, `highlightColor` (device-local, not part of undo history, not synced) |
| add-on settings | `sync_*` (see [NOTEBOOK_SYNC.md](NOTEBOOK_SYNC.md)) |

## Tests
- Python: `test_notebook_text`, `test_notebook_cards`, `test_card_html`, `test_ai_notes_context`, `test_ai_cards_action`, `test_notebook_card_batch` (run with `python -m unittest discover -s tests -p "test_*.py"`).
- Browser (Playwright, Chromium-based browser via `SYNAPSE_TEST_CHROME`): `web_notebook_lists_latex_colors.cjs` (lists, LaTeX, colors, parentheses, slash menu, markdown caret), `web_notebook_card_creator.cjs`, `web_card_editor.cjs` (rich fields on both pages), `web_chat_cards.cjs`, `web_notebook_copy_paste.cjs`, `web_notebook_highlight.cjs`, `web_notebook_inline_math.cjs`, `web_notebook_scroll.cjs`, `web_tab_keys.cjs`, `web_pdf_card_images.cjs`, `web_pdf_search.cjs`, `web_creator_resize.cjs`. `test_notebook_shortcuts`, `test_sidebar_default_width` and `test_notebook_view_state` cover the shortcut setting, the opening width, the remembered scroll position and tab switching.
