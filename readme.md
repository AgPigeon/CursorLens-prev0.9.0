# Tool Description

**CursorLens (光标镜)** is a cursor-following floating indicator built with the Python standard-library **tkinter** GUI toolkit, which used for input method detection and display. It shows, right next to the mouse pointer, the current **input language (Chinese / English)** and the current **case state (upper / lower)**. The motivating problem: while typing you often need to check whether CapsLock is on and whether the IME is in Chinese or English mode, which normally means looking down at the status area at the bottom-right corner of the taskbar. CursorLens moves that information into a small capsule beside the cursor, so your eyes stay on the text and your hands stay on the keyboard.Note: This file has only been tested on Windows 11 and is not guaranteed to work on other versions of the operating system

## Folder Structure

- `cursorlensv1.0.0.py` – the source code (a single file, standard library only, no third-party dependency).
- `cursorlens_config.json` – configuration file (created automatically on the first settings change / exit; delete it to restore defaults).
- `readme.md` – this English documentation.
- `readme_zh.md` – the Chinese original documentation.

## Panel Layout

The floating panel (the `◤` marker rotates to point at the mouse):

```
 x +50 y +0    ◤    [ pin ]   [ da ]
 └- caption -┘  └- marker ┘ └lang┘ └case┘
```

| Element | Meaning | Values |
| --- | --- | --- |
| Language badge | Single-character abbreviation of the active input language | 拼 / 五 / 注 / 仓 / 郑 / 中 / EN / 日 / 한 |
| Case badge | CapsLock state (wording depends on the language) | Chinese: 大 / 小; English: U / L |
| Triangle marker | Points at the mouse position | rotates with the offset direction |
| Offset caption | Current x / y offset, e.g. `x +50 y +0` | -100 ~ +100 |

Colours: Chinese = blue, English = slate; upper-case (大 / U) = red-orange, lower-case (小 / L) = slate, so the state is readable from colour alone.

## Core Principle

Three "read chains" plus one "display chain":

1. **Key triggers.** Several virtual-key codes are polled with `GetAsyncKeyState` at roughly 40 Hz. The frame in which a combo goes from "not satisfied as a whole" to "satisfied as a whole" is treated as one trigger (a rising edge), so holding the keys down does not re-fire the panel.
2. **Input-method reading (three merged clues).**
   - **Language id:** `GetKeyboardLayout(foreground window thread)` returns the keyboard layout handle (HKL); its low 16 bits are the language id (0x0804 family = Chinese).
   - **Chinese/English mode (the critical, cross-process part):** first try `ImmGetContext` + `ImmGetConversionStatus` (the `IME_CMODE_NATIVE` bit means Chinese mode). Modern applications (Chrome/Edge/Electron/UWP) go through TSF, so `ImmGetContext` returns NULL for their windows; in that case switch to `ImmGetDefaultIMEWnd` + `SendMessageTimeout(WM_IME_CONTROL, IMC_GETCONVERSIONMODE)`, which reads the same "native mode" flag through a path that does work across processes. This is what makes the Chinese/English state visible inside *other* applications.
   - **IME name:** `ImmGetDescription` / `ImmGetIMEFileName` → registry `Keyboard Layouts\<KLID>` (classic and third-party IMEs) → registry `CTF\TIP` listing the **enabled** TSF IMEs of that language. Windows 11's Microsoft Pinyin / Wubi register no keyboard layout at all, so their name can only be obtained from the last source (its `Display Description` is an indirect string such as `@...dll,-5300` and must be resolved with `SHLoadIndirectString`). The name is then mapped to 拼 / 五 / 注 / 仓.

   The tool also ignores **its own** windows when deciding what "the foreground window" is (it remembers the most recent foreground window that does not belong to this process). Without that, its own panel or settings window would become the foreground, the tool would read its own IME context and the Chinese/English state would look as if it only worked in the settings window.
3. **Case reading.** The lowest bit of `GetKeyState(VK_CAPITAL)` is the CapsLock toggle state.
4. **Positioning and display.** `GetCursorPos` gives the mouse position; adding the user offset gives the panel's top-left corner, which is then clamped into the virtual desktop rect. The window is made borderless with `overrideredirect`, punched through with `-transparentcolor`, and made click-through with `WS_EX_TRANSPARENT`.

**Example.** Press Ctrl+Space to switch to Microsoft Pinyin, then press CapsLock once:

- Ctrl+Space triggers a read → language id 0x0804, native mode = True, description contains "Pinyin" → the language badge shows `拼`, and the case badge shows `小`.
- CapsLock triggers a read → the CapsLock state flips → the case badge becomes `大` while the language badge stays `拼`.
- Ctrl+Space again to switch to English → the language badge becomes `EN` and the case badge switches to the English wording `U` / `L`.

## Detailed Algorithm Flow

① Key polling → ② trigger decision (settings hotkey first, then the trigger keys) → ③ read the IME state → ④ read CapsLock → ⑤ choose the case wording from the language → ⑥ render the panel → ⑦ position, follow the mouse, auto-hide on timeout.

(LaTeX:

① Trigger condition (`t` = current sample, `K` = set of pressed keys, `Ω` = selected trigger combos):

Trigger_{t} = \left( K_{t} \supseteq c \right) \wedge \neg \left( K_{t-1} \supseteq c \right), \quad c \in \Omega

② Positioning (offset first, then clamp into the virtual desktop):

(x, y) = \left( \min\left( \max\left( x_{mouse} + \Delta x, \ V_{x} \right), \ V_{x} + V_{w} - W \right), \ \min\left( \max\left( y_{mouse} + \Delta y, \ V_{y} \right), \ V_{y} + V_{h} - H \right) \right)

③ Case mapping (`caps` = CapsLock state, `cjk` = Chinese mode):

case = \begin{cases} 大, & caps \wedge cjk \\ 小, & \neg caps \wedge cjk \\ U, & caps \wedge \neg cjk \\ L, & \neg caps \wedge \neg cjk \end{cases}
)

## Usage Instructions

- Run `cursorlensv1.0.0.py`: `python cursorlensv1.0.0.py`. If you prefer no console window, use `pythonw cursorlensv1.0.0.py` (or just double-click the file when it is associated with `pythonw`). On start-up the panel is shown next to the cursor for 2 seconds as a "the tool is running" hint.

- Default triggers are `CapsLock` and `Ctrl+Space`. Pressing one pops the panel to the lower-right of the mouse (defaults `x +20`, `y +20`); it stays for **1 second by default** and then hides automatically, following the mouse while visible.

- The display mode is configurable (default "on trigger"):

  - **On trigger (default):** show for a while after a trigger key, then auto-hide. The duration is chosen from **0.3 / 0.5 / 1 / 2 / 4 seconds** (default 1 s).
  - **Toggle (sticky):** press the trigger once to show, press again to hide; never auto-hides.
  - **Always visible:** keep showing and following the mouse.

- The default settings hotkey is the three-key combo `Ctrl+Alt+C`. It opens a tabbed settings window:

  - **Triggers & Display**: tick the candidate trigger combos (**at most 2**), pick the display mode (on trigger / toggle / always), pick the visible duration (0.3 / 0.5 / 1 / 2 / 4 s), and record a new settings hotkey.
  - **Position**: x / y offset sliders (-100 ~ +100 px), a reset button, and the keep-inside-screen clamp. While this window is open the panel previews live so you can tune it visually.
  - **Style / Misc**: scale, opacity, show offset caption / show marker, click-through switch, **show diagnostics**, the language-detection fallback (auto / force 拼 / 五 / 中 / EN), UI language (中文 / English), a **Diagnostics** button (copies the detection data), restore defaults, and quit.

- Every change in the settings window takes effect **immediately** and is written to `cursorlens_config.json`; there is no "Apply" button, and the file is read back on the next start.

- Quitting: open the settings window and click "退出程序 / Quit". (While running in the background there is no taskbar icon, so this hotkey is the only way in.)

## Additional Notes & Boundary Conditions

- ① **Platform limit.** Only Windows can read real IME and key state (it relies on `user32.dll` / `imm32.dll`). On other platforms the module still imports and runs, but the state is empty and nothing is displayed; adapt by replacing the `WinAPI` implementation.

- ② Chinese/English detection uses two paths: `ImmGetContext` (most accurate in-process / for classic apps) and `WM_IME_CONTROL` (cross-process, works for modern apps). When neither can be read — for instance an administrator-privileged foreground window or an exclusive full-screen game blocked by UIPI — the tool falls back to the language id alone (Chinese layouts → 中/拼, anything else → `EN`). Turn on **show diagnostics** and press a trigger key to inspect the `source` field and confirm which case you hit.

- ③ Pinyin/Wubi resolution order: description / file name / keyboard layout name → the **only enabled** TSF IME of that language. If several IMEs are enabled for the same language (for example both Microsoft Pinyin and Microsoft Wubi), there is no cross-process way to tell which one is active, so the panel shows the generic `中`; pin it under **Style / Misc → Language detection** as `拼` / `五` / `中` / `EN`.

- ④ **When the badge looks wrong:** open the settings window → Style / Misc → enable "show diagnostics" → close the settings → focus the application that misbehaves and press a trigger key. The panel then carries an extra line with the raw `hkl / n= / o= / source / ime=` values; the **Diagnostics** button copies a full report. That data tells you immediately whether the mode was unreadable or the IME name simply was not recognised.

- ⑤ The trigger candidates live in the `TRIGGER_CANDIDATES` list near the top of the source file — add or remove entries there. Each candidate is a combo string such as `ctrl+space`; key names support `ctrl` / `shift` / `alt` / `win` / `space` / `capslock` / `f1`~`f24` / `a`~`z` / `num0`~`num9` / `` ` `` `-` `=` `[` `]` `\` `;` `'` `,` `.` `/`.

- ⑥ The settings hotkey is meant to be a three-key combo: during recording, press **3 or more keys at the same time** to finish, or press `Esc` to cancel. If the recorded combo overlaps a selected trigger (one is a subset of the other) the tool warns you that they may interfere.

- ⑦ The ±100 px offset range is the agreed adjustment window; larger values written into the config file are clamped back to the range. The default `x +20 y +20` means "lower-right of the cursor".

- ⑧ Click-through is enabled by default: the panel never steals a click and never takes focus, at the cost of not being right-clickable — use the settings hotkey instead. If you disable it, increase the offset, otherwise the panel may cover something you want to click.

- ⑨ Performance: the key poll runs every 25 ms (about 40 Hz) and the state is refreshed every 180 ms in always-visible mode. The canvas is only redrawn when the badge content or the configuration changes; while following the mouse only the window position is updated, so idle cost is negligible.

- ⑩ If CapsLock has been remapped to "toggle Chinese/English" (a common user customisation), the CapsLock trigger and the language switch then happen together. That is expected — both badges update so you can confirm the result.

- ⑪ The panel uses colour-key transparency (not a per-pixel alpha mask), so the rounded panel has hard edges. For soft shadows and a truly translucent rounded capsule, pre-render a PNG with Pillow and use it as the panel image.

## Configuration fields (`cursorlens_config.json`)

| Field | Default | Meaning |
| --- | --- | --- |
| `triggers` | `["capslock","ctrl+space"]` | Trigger combos, at most 2 |
| `settings_hotkey` | `"ctrl+alt+c"` | Three-key combo that opens the settings window |
| `offset_x` / `offset_y` | `20` / `20` | Offset from the mouse position (-100 ~ +100) |
| `display_mode` | `"trigger"` | Display mode: trigger / sticky / always |
| `dwell_ms` | `1000` | Visible duration after a trigger, trigger mode only (300 / 500 / 1000 / 2000 / 4000 ms; other values snap to the nearest choice) |
| `clamp_to_screen` | `true` | Clamp into the virtual desktop |
| `show_caption` | `false` | Show the `x +n y +n` offset caption |
| `show_marker` | `true` | Show the triangle marker pointing at the mouse |
| `show_debug` | `false` | Show the IME-detection diagnostics line under the panel |
| `click_through` | `true` | Mouse click-through |
| `lang_override` | `"auto"` | Language detection: auto / pin / wubi / en |
| `scale` | `1.0` | Scale (0.6 ~ 2.0) |
| `alpha` | `0.95` | Opacity (0.30 ~ 1.0) |
| `ui_language` | `"zh"` | Settings UI language: zh / en |

## Visualisation – Panel Rendering & Following Mechanism (Summary & Memo)

**Screen coordinate system:** mouse position, offset, target position, virtual desktop rect.
**Window coordinate system:** canvas size, badge width, panel corner radius.

- **① Initialisation:** The main `Tk()` window is only `withdraw()`n and serves as the event source; the panel is a `Toplevel` with `overrideredirect(True)` and is hidden right after creation, so no blank frame flashes on start-up.

- **② Rendering:** The canvas is fully redrawn (`canvas.delete('all')` then repaint) rather than partially updated: the canvas size is computed from the content first, then the rounded panel, the offset caption, the rotating triangle marker and the two rounded badges with their text are drawn, and `geometry()` syncs the window size. Tk's canvas does no anti-aliasing, which is exactly why colour-key transparency produces no coloured fringes.

- **③ Following:** Every frame reads the mouse position once, computes target = mouse + offset, clamps it into the virtual desktop and calls `geometry('WxH+x+y')`; the call is skipped when the coordinates have not changed.

- **④ Transparency & click-through:** Both the toplevel background and the canvas background are set to the transparent key colour (`#FF00FE`) and punched out with `-transparentcolor`; then `SetWindowLongPtr` adds `WS_EX_TRANSPARENT | WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW | WS_EX_LAYERED` so the panel takes no focus, lets clicks pass through and stays out of the Alt+Tab list.

- **⑤ Show & hide:** A trigger stores `hide_at = now + dwell_ms` and every frame compares the monotonic clock against it; the panel is kept visible while "always visible", "toggle mode is on" or "settings window open" is active, the last one doubling as a live preview.
