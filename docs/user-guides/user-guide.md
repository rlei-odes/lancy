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

## My Documents

Under **Advanced Features → My Documents** you can upload your own documents without adding them to the shared knowledge base. They go into a **pile**: a private collection that only you see.

1. Type a name and click **Create**.
2. Drag files onto the pile, or click **Add files**. PDF, Word, Excel, Markdown, text, EPUB, e-mail (`.eml`) and images are accepted.
3. Click **Process**. Lancy reads the files, splits them into passages and makes them searchable. A progress bar shows which file it is working on and roughly how long is left.

Processing runs on the server, so you can leave the page meanwhile. When it is done the pile is marked *ready* and can be used in Batch Analysis.

If you add or remove files later, the pile is marked *needs reprocessing* — click **Process** again to include the changes. A pile marked *failed* can simply be processed again; if it keeps failing, tell your administrator.

You can keep up to 20 piles with up to 200 files each. A pile belongs to your browser session, like your presets.

---

## Batch Analysis

**Advanced Features → Batch Analysis** asks the same questions of many documents and gives you the answers as a table — for example, *"What eye protection does this safety data sheet require?"* across a hundred data sheets.

Each document is analysed separately, one after another. A large batch keeps the language model busy and slows down chat for everyone while it runs, so prefer running big batches outside busy hours.

### 1. Documents

Choose where the documents come from: the knowledge base selected in the sidebar, or one of your processed piles. Then list the documents, one per line — by filename as ingested, or by document ID if your knowledge base uses them. Selecting a pile fills in its files for you; you can delete lines to analyse only some of them.

### 2. Questions

Add one row per question and choose what kind of answer you want:

| Type | You get |
|---|---|
| Text | A short written answer, or empty if the document doesn't say |
| Yes / no | `true` or `false` |
| Number | A number, or empty if the document doesn't say |
| Choice | One of the answers you list, e.g. `yes, no, unclear` |

The model is told not to guess: when a document doesn't answer a question, it leaves the answer empty. A **Yes / no** question can't be empty, so there `false` means "no" *or* "not mentioned". When that difference matters — "does the sheet require eye protection?" — use **Choice** with an answer such as `unclear` instead.

Ask about one thing per row. More than about ten questions in one run lowers the quality of each answer; split them into two runs instead.

**Question sets.** Use **Save as…** to store the current questions under a name, and the **Question set** dropdown to load them again later. Loading a set replaces the questions in the editor. When you change a loaded set the dropdown shows *modified*; **Save** writes your changes back to it. The trash icon deletes the set. Question sets are private to you.

### 3. Verify and run

**Verify** checks every document before anything is spent on the language model: it lists which documents were not found and which are too long to analyse in one go. Then **Run analysis** starts the batch.

The run happens on the server. A progress bar shows how many documents are done — green for answered, amber for skipped, red for errors — which document is being analysed, and an estimate of the time left. **You can leave the page or close the tab**; when you come back, the run and its results are still there. **Stop** finishes the current document and then stops.

### Results

Answers appear in the table as each document finishes. Download them at any time: **CSV** for spreadsheets, with each column headed by its question, or **JSON**, which lists the questions alongside the answers.

The status column tells you what happened per document:

| Status | Meaning |
|---|---|
| `ok` | Answered |
| `over_budget` | Too long to analyse in one go — skipped |
| `no_chunks` | Not found in the selected source |
| `error:…` | Something went wrong for this document; the rest of the run continues |

### Running again

**Run analysis** again with the same questions and documents only analyses what is still missing: documents you have added since, and documents that ended in an error. Answers you already have are kept. This also continues a run that was stopped, or interrupted by a server restart.

If you **change the questions** — or switch to a different source — the existing answers no longer fit. Lancy asks before starting, and the new run replaces the old results, so download them first if you still need them. **Clear results** discards them without running.
