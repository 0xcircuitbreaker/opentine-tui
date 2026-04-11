# opentine-tui

Terminal dashboard for [opentine](https://github.com/0xcircuitbreaker/opentine) — visual management console for agent runs.

Built with [Textual](https://textual.textualize.io/).

## Install

```bash
pip install opentine-tui
```

## Usage

```bash
tine-dashboard
```

## Layout

```
┌── Runs ─────────┐ ┌── Steps ────────────────────┐ ┌── Details ─────────┐
│ a3f8  ● comp    │ │ * think "I'll search..."     │ │ Run: a3f8          │
│ b7c1  ● comp    │ │ > tool  search("mass of sun")│ │ Model: claude      │
│ c9d2  ◉ running │ │ * think "The mass is..."     │ │ Steps: 4           │
│                  │ │ + done                       │ │ Cost: $0.003       │
│                  │ │                              │ │ Duration: 12.3s    │
└──────────────────┘ └──────────────────────────────┘ └─────────────────────┘
```

## Keybindings

| Key | Action |
|---|---|
| `q` | Quit |
| `r` | Refresh run list |
| `1` | Focus runs panel |
| `2` | Focus steps panel |
| `3` | Focus details panel |
| `Enter` | View selected run |

## License

Apache 2.0
