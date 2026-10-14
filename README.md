# CommunityOS

CommunityOS is a search tool for community resources in Waterloo Region in Canada. Type what you need in English or Mandarin, then it returns a list of places that fit.

## Why create CommunityOS

I'm an international student living in Kitchener-Waterloo region, and I can totally understand some new international students or new immigrates feel confused in a new country and don't know where they should turn to when they have different problems.

## What it does

- Covers five kinds of resources: food assistance, donation drop-offs, learning help, study spaces and healthcare.
- Understands short requests like "where can I donate a sofa" or "free dental clinic".
- Filters by category, what the place actually offers, and constraints such as free, open now, no ID, or no appointment. If nothing matches, it relaxes the constraints step by step and tells you what it dropped.
- Ranks results with a visible score.
- Shows why each result was picked. The explanation is built from the database, not generated freely, so hours and addresses aren't made up.
- Lets users report a card that is wrong or closed. Cards with several open reports get flagged for review.
- Has an admin page for handling reports and reviewing newly extracted resources.

## How to use it

### Use LLM to fetch data

Before using LLM, make sure you've already downloaded Gemini SDK and got your own key. For downloading: 

```sh
python -m pip install -r requirements.txt
```

or just downloading SDK: 

```sh
python -m pip install google-genai
```

Then set up environment variables

```sh
$env:GEMINI_API_KEY = "Your Gemini API key"
$env:GEMINI_MODEL = "gemini-3.8-flash"
# Search uses local rules; Gemini is reserved for webpage ingestion.
$env:USE_LLM_INTENT = "0"
$env:ADMIN_TOKEN = "Set a admin token for yourself so you can enter admin version"
```

With all those set up, you can fetch data through websites in sources.json (or you can add other websites in it)

```sh
python -m backend.ingest --sources-file data/sources.json
```

You can also ingest a single page: 

```sh
python -m backend.ingest https://example.org/some-page --region kitchener
```

Extracted records go to `data/pending.json`.



### Run CommunityOS

```sh
python -m backend.simple_server
```

Open http://localhost:8000 to see it.

There is also a local admin version at http://localhost:8000/admin. Enter the `ADMIN_TOKEN` you set so you can use it. If you didn't set one, the default is `demo-admin`.

If you'd rather fun the FastAPI version (download it from requirements.txt):

```sh
uvicorn backend.main:app --reload
```

