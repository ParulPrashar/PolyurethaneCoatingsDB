# Waterborne polyurethane coating properties database

A small pipeline and website that reads open-access research papers, extracts property values
with an AI model, stores them in a database, and shows them on a website with the exact
sentence each value came from.

## How to run
1. F_collectXML.py  //searches on Europe PMC
<!-- 2. F_xmlToJSON.py   //converts extracted paper to JSON -->
2. F_parsePapers.py //converts extracted paper to JSON
3. F_extractProperties.py //gemini extracts properties
4. F_verify.py //verify records with what is mentioned in paper
5. load_to_db.py //properties stored in DB
6. app.py //website run

## How it works

```
Europe PMC search        F_saveRawXML.py         raw_xml/<PMCID>.xml
        |
        v
XML to structured JSON   F_extract.py            structured_json/<PMCID>.json
        |
        v
AI model reads each      F_extractProperties.py  extracted/<model>/<PMCID>.json
paper (Gemini API)                               (values + evidence + checks)
        |
        v
SQLite database          load_to_db.py           pu_coatings.db
        |
        v
API + website            app.py + web/           http://127.0.0.1:8000
```

The five properties: tensile strength, elongation at break, water contact angle, water
absorption, glass transition temperature. Values come from the written text of papers only.
Tables are not parsed yet.

## What to install

Nothing new except the Python packages. In your activated `PlumberEnv`:

```
pip install -r requirements.txt
```

- **SQLite** is built into Python. There is nothing to download.
- **The website** is plain HTML, CSS and JavaScript. There are no libraries or build tools.
- **Optional:** DB Browser for SQLite (free) lets you look inside `pu_coatings.db`.

## Folder layout

Put the new files in your existing `pu-coatings-db` folder, next to your other scripts:

```
pu-coatings-db/
    app.py                  <- new (the API and website server)
    load_to_db.py           <- same as before
    requirements.txt        <- new
    README.md               <- new
    web/                    <- new folder
        index.html
        style.css
        script.js
    F_saveRawXML.py         <- yours, unchanged
    F_extract.py            <- yours
    F_extractProperties.py  <- yours
    .env                    <- yours (holds your API key, never share it)
    raw_xml/  structured_json/  extracted/  llm_raw/   <- created by your scripts
    pu_coatings.db          <- created by load_to_db.py
```

`app.py` and the `web/` folder must stay side by side.

## Run order

Run each command from the `pu-coatings-db` folder with `PlumberEnv` active.

1. `python F_saveRawXML.py` downloads papers to `raw_xml/`
2. `python F_extract.py` builds `structured_json/`
3. `python F_extractProperties.py` extracts values with the AI model.
   Papers already done are skipped, so you can stop with Ctrl+C and rerun later.
4. `python load_to_db.py` builds `pu_coatings.db`. Safe to rerun. If it complains about
   several folders in `extracted/`, run `python load_to_db.py extracted/<folder-name>`.
5. `python app.py`, then open **http://127.0.0.1:8000** in your browser.
   Press Ctrl+C in the terminal to stop it.

To refresh the site after more extraction: run step 3, then step 4, then restart step 5.

## What you should see

- A headline sentence with the live counts of values, papers and verified values.
- A property dropdown. Pick one and you get a dot plot (each dot is one value, one row per
  paper) and a table of the same values.
- "Show evidence" on any row (or clicking a dot) opens the sentence from the paper with the
  value highlighted.
- Filled dots and the word "Verified" mean the quoted sentence was found in the paper and
  contains the value. Hollow amber dots mean it was not, so check those by hand.
- `http://127.0.0.1:8000/docs` shows the API and lets you try every endpoint.

## The API

| Endpoint | Returns |
|---|---|
| `GET /api/summary` | totals for papers, values, verified values, models used |
| `GET /api/properties` | each property with its number of values and verified values |
| `GET /api/records?property=...&verified_only=true&limit=500` | the values, with paper, sample, condition, evidence |
| `GET /api/papers` | every paper read and how many values it gave |

The API only reads the database. It never changes it.

## If something goes wrong

| What you see | What it means | Fix |
|---|---|---|
| `Database not found` | `pu_coatings.db` does not exist yet | Run `python load_to_db.py`, then restart `app.py` |
| `no such table` | The database file is empty or broken | Delete `pu_coatings.db`, rerun `load_to_db.py` |
| `Folder not found ... web` | `web/` is not next to `app.py` | Move the `web` folder next to `app.py` |
| `Pass a folder: python load_to_db.py extracted/<name>` | More than one folder inside `extracted/` | Run it with the folder name, as the message says |
| `address already in use` | Something else is using port 8000 | Close the other copy of `app.py`, or change `8000` at the bottom of `app.py` |
| Page says it cannot reach the service | `app.py` is not running | Start it with `python app.py` and reload |
| Page loads but is empty | The database has no values | Run steps 3 and 4 |
| Old data still shows | The browser or server kept the old state | Restart `app.py`, then hard-refresh with Ctrl+F5 |

## Known limits (good next steps)

- **Tables are not read.** Many papers report tensile strength only in tables, so those
  values are missing. Parsing tables is the most useful next step.
- **Verification checks the number and the quote, not the labels.** A value can be real while
  its sample name or condition is slightly off, which is why the evidence sentence is shown
  next to every value.
- **Values like "over 30" are plotted at 30.** The table always shows the original text.
- **Only five properties.** The prompt in `F_extractProperties.py` is where to add more, for
  example adhesion, hardness and gloss.
- **Unit spelling** from the model varies, so the API tidies "degree" into "°" for display.
  Other units are shown as the model wrote them.

## Handing it over

Suggested `.gitignore`:

```
.env
PlumberEnv/
__pycache__/
```

Commit `extracted/` and `pu_coatings.db` so a reader can run the website without an API key.
Never commit `.env`. To run only the website, they need `app.py`, `web/`, `requirements.txt`
and `pu_coatings.db`, then `pip install -r requirements.txt` and `python app.py`.
