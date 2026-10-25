# User Guide
Main guide for users. How to get answers from your knowledge base.
---

## Asking questions

Type your question in the box at the bottom and press Enter. Lancy searches the knowledge base for passages that fit, hands the best ones to the language model, and the answer cites the documents it used.

### Choosing the knowledge base

The button next to the question box names the knowledge base a new chat searches, with how many documents and passages it holds. Pick another one before your first question. Once a chat has started it stays with its knowledge base — the button shows a lock — so start a new chat to ask another one.

How the search works — how many passages it retrieves, whether it rewrites your question, whether it re-ranks the results — is set by your administrator for each knowledge base. The **Reference** in the menu at the bottom left explains each setting.

### Options for the next question

The **+** next to the question box offers:

- **Filter documents** — in a new chat, limits the search to documents with matching properties, such as a document type. Only offered when your administrator has set up filters for the knowledge base. The filter stays fixed for the conversation.
- **Chat only** — skips the search for the next message; the model answers from the conversation and its general knowledge.

Whatever is switched on shows as a small label above the question; click its × to remove it.

### The Documents panel

The panel on the right shows, for an answer, every document the search found. The button below each answer — for example *3 documents · 1 cited* — opens that answer's documents, and clicking a document name in the answer opens and expands that document. While an answer is being written, the panel already lists what was found; which documents were cited follows when the answer is complete.

Each document shows one small square per passage:

| Square | Meaning |
|---|---|
| Filled | The answer cites this passage |
| Outlined | Given to the model, not cited |
| Dashed | Found, but ranked too low by the re-ranking step, so the model never saw it |

**Semantic** and **Keyword** tell which search found the document: semantic search matches meaning, keyword search matches words. Expand a document to read its passages and where each one ranked; the arrow icon opens the document itself. *Cited* filters the list down to the documents the answer used.

### Asking about specific documents

Tick documents in the panel and choose **Ask using only these**: your next question is answered from those documents read in full, without searching. The panel estimates whether they fit the model; if they are too long, select fewer. After that one question, the chat searches normally again.

### Suggested follow-up questions

Below the latest answer, Lancy suggests a few follow-up questions. Click one to ask it.

---

## My Documents

Under **Advanced Features → My Documents** you can upload your own documents without adding them to the shared knowledge base. They go into a **pile**: a private collection that only you see.

1. Type a name and click **Create**.
2. Drag files onto the pile, or click **Add files**. PDF, Word, Excel, Markdown, text, EPUB, e-mail (`.eml`) and images are accepted.
3. Click **Process**. Lancy reads the files, splits them into passages and makes them searchable. A progress bar shows which file it is working on and roughly how long is left.

Processing runs on the server, so you can leave the page meanwhile. When it is done the pile is marked *ready* and can be used in Batch Analysis.

If you add or remove files later, the pile is marked *needs reprocessing* — click **Process** again to include the changes. A pile marked *failed* can simply be processed again; if it keeps failing, tell your administrator.

You can keep up to 20 piles with up to 200 files each. A pile belongs to your browser session, like your question sets.

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
