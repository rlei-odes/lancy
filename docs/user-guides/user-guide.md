# User Guide
Main guide for users. How to get answers from your knowledge base.
---

## Retrieval presets

The RAG Parameters panel controls **how** the system searches your documents before answering — how many passages it retrieves, whether it rewrites your question, whether it re-ranks the results. A preset is a named set of those settings.

The dropdown marks where each preset comes from:

| Prefix | Meaning | Can you overwrite it? |
|---|---|---|
| `★` | Yours. You created it. | Yes |
| `·` | Set by an administrator, shared by everyone | No — you get an error, save under your own name instead |
| `◆` | Fixed, cannot be changed by anyone | No |

### What loads when you switch knowledge base

**Switching knowledge base resets your retrieval settings to `Default`** — the administrator's baseline for the whole organisation. This happens every time, and it replaces whatever you had set.

So if you work with your own preset, the order matters:

1. Switch to the knowledge base you want.
2. *Then* select your `★` preset from the dropdown.

Doing it the other way round loses your selection: the KB switch overwrites it. Your preset is not deleted — just not active until you pick it again.

### Saving your own

Adjust the settings, click the save icon next to the dropdown, and give it a name. Use a name of your own — if you type the name of an administrator preset the save is refused with a message.

Your presets are private: nobody else sees them, and they do not affect anyone else's searches. They are tied to your browser session, so clearing your site data or signing in from a different browser starts you fresh with the administrator's `Default`.

### If you are not sure what to pick

`Default` is chosen by your administrator as the sensible baseline, and it is what you get without doing anything. The other administrator presets (`·`) trade speed against thoroughness — `Fast` retrieves less, `Quality` and `Multi-Document` retrieve more and do extra work per question. Try one if answers feel too shallow or too slow.

---

## Content

Placeholder file
