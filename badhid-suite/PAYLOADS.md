# badhid-suite — payload catalog

Every payload here is **harmless**: it only **types text**, **opens an app**, or
runs a **read-only local command** (like `whoami`). None of them change, delete,
download, disable, persist, or exfiltrate anything. Close any window they open
and you're back to normal.

Most target **Windows** (they use the Win+R "Run" box to launch Notepad / a
browser / cmd). Each file's header has the one-line tweak for macOS/Linux where
relevant. The plain type-only ones (`hello_world`, `ascii_cat`, `keymap_test`,
etc.) work anywhere if you focus a text field first.

Fire any of them with the CLI:
```bash
sudo ./badhidctl.sh arm
sudo ./badhidctl.sh fire <name>.duck   my-own-pc
```

## Mild — proof it works
| Payload | What it does |
|---|---|
| `hello_world.duck` | Types one line into the focused window. The quietest proof. |
| `welcome.duck` | Opens Notepad, types a short friendly intro. |
| `ascii_cat.duck` | Opens Notepad, draws a little ASCII cat. |
| `fortune.duck` | Opens Notepad, types a wholesome fortune. |
| `open_calculator.duck` | Just opens the Calculator — proof it can launch apps. |
| `keymap_test.duck` | **Utility:** types every letter/digit/symbol so you can confirm nothing is dropped or mistyped. Run this first on a new target. |

## Funny
| Payload | What it does |
|---|---|
| `rickroll.duck` | Opens the classic video in the default browser. |
| `capslock_prank.duck` | Toggles Caps Lock 8 times, then leaves it exactly as it started. Silly, harmless. |
| `ghost_typer.duck` | Types slowly and hesitantly like a shy ghost. |
| `shrug.duck` | Types an ASCII shrug and a one-liner. |
| `loading_bar.duck` | Types a fake progress bar that ends "Task failed successfully." |
| `too_many_notepads.duck` | Opens three Notepad windows with messages. Mildly chaotic; just close them. |

## Spooky — scary-*looking*, completely harmless
| Payload | What it does |
|---|---|
| `spooky_skull.duck` | Opens Notepad, draws an ASCII skull & crossbones with "I SEE YOU" / "I'M COMING FOR YOU!". Pure text. |
| `i_see_you.duck` | Slow creepy message that defuses itself at the end. |
| `redrum.duck` | Types "REDRUM" a few times (it's "murder" backwards — a movie nod). |
| `hacker_theater.duck` | Fake "hollywood hacking" log typed into Notepad ("ACCESS GRANTED", "DECRYPTING…"). Pure theater — it's a text editor, nothing happens. |
| `fake_selfdestruct.duck` | A dramatic 5→1 countdown that does absolutely nothing. |
| `haunted_browser.duck` | Opens a YouTube search for "Spooky Scary Skeletons". |

## Sketchy-*looking* — opens a real terminal, runs READ-ONLY commands
These prove the HID can reach a shell, using commands that only **print**
information and change nothing. They're the edgiest in the box and still
completely safe.
| Payload | What it does |
|---|---|
| `shell_whoami.duck` | Opens cmd, runs `whoami`, `hostname`, `ver` — prints who/what the machine is. Read-only. |
| `shell_netinfo.duck` | Opens cmd, runs `ipconfig` — prints this machine's network settings. Read-only. |

## Where the line is (so you can write your own safely)
Harmless = types text, opens an app, or runs a read-only command. The moment a
payload would **delete, modify, download-and-run, disable a protection, install
persistence, send data off the box, or grant remote access**, it stops being a
lab gag and becomes malware — this suite doesn't ship those, and you shouldn't
point one at anything you don't own or aren't authorized to test. Keep your own
creations on the harmless side of that line (or clearly scoped to your own
send-off hardware, deliberately).
